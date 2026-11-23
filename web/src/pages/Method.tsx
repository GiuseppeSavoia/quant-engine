// Il mio metodo: profilo (come investi), metriche personali (formule sulle metriche del catalogo, con l'anteprima di
// oggi prima di salvarle) e domande della checklist. Calcoli e controlli nel motore (servizio method): qui solo moduli.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty, Notice, Segmented, Skeleton } from "../components/ui";
import { api, ApiError, type FormulaPreview, type MethodPage, type Profile } from "../lib/api";
import { fmt, type Kind } from "../lib/fmt";

type Tab = "profilo" | "metriche" | "checklist";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

function ProfileForm({ m }: { m: MethodPage }) {
  const qc = useQueryClient();
  const [p, setP] = useState<Profile>(m.profile);
  const save = useMutation({
    mutationFn: () => api<Profile>("/method/profile", { method: "PUT", body: JSON.stringify(p) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["method"] }),
  });
  const area = (key: "principles" | "red_flags" | "sell_rules", label: string, ph: string) => (
    <label className="field wide"><span>{label}</span>
      <textarea rows={4} value={p[key]} placeholder={ph} onChange={(e) => setP({ ...p, [key]: e.target.value })} /></label>
  );
  return (
    <Card title="Il mio profilo" right="guida il resto dell'app">
      <div className="form-grid">
        <div className="field wide"><span>Cosa cerco soprattutto</span>
          <div className="pills">
            {m.styles.map((s) => {
              const on = p.styles.includes(s);
              return <button key={s} type="button" className={`pill${on ? " on" : ""}`} aria-pressed={on}
                onClick={() => setP({ ...p, styles: on ? p.styles.filter((x) => x !== s) : [...p.styles, s] })}>{s}</button>;
            })}
          </div>
        </div>
        <label className="field"><span>Per quanto tengo un titolo</span>
          <select className="select" value={p.horizon} onChange={(e) => setP({ ...p, horizon: e.target.value })}>
            {m.horizons.map((h) => <option key={h} value={h}>{h}</option>)}
          </select></label>
        <label className="field"><span>Quante aziende in portafoglio</span>
          <input className="text-input" type="number" min={1} max={200} value={p.positions}
            onChange={(e) => setP({ ...p, positions: Number(e.target.value) || 1 })} /></label>
        {area("principles", "I miei principi: cosa deve avere un'azienda per interessarmi", "es. ROIC sopra il 15% da anni, debito basso, management che riacquista azioni solo quando costano poco…")}
        {area("red_flags", "Cosa mi fa scartare subito un'azienda", "es. diluizione continua, utili non coperti da cassa, business che non capisco…")}
        {area("sell_rules", "Quando vendo", "es. la tesi è sbagliata, il prezzo supera di molto il mio fair value, trovo di meglio…")}
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>Salva il profilo</button>
        {save.isSuccess && <span className="pos">Profilo salvato.</span>}
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </Card>
  );
}

function Metrics({ m }: { m: MethodPage }) {
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [formula, setFormula] = useState("");
  const [kind, setKind] = useState("num");
  const [better, setBetter] = useState<"" | "high" | "low">("high");
  const [description, setDescription] = useState("");
  const [usedBy, setUsedBy] = useState<string[] | null>(null);
  const debounced = useDebounced(formula.trim(), 400);
  const preview = useQuery({
    queryKey: ["formula", debounced, better], enabled: debounced.length > 0, retry: false, placeholderData: keepPreviousData,
    queryFn: () => api<FormulaPreview>("/method/metrics/preview", { method: "POST", body: JSON.stringify({ formula: debounced, better: better || null }) }),
  });
  const create = useMutation({
    mutationFn: () => api("/method/metrics", { method: "POST", body: JSON.stringify({ label, formula, fmt: kind, better: better || null, description }) }),
    onSuccess: () => { setLabel(""); setFormula(""); setDescription(""); qc.invalidateQueries({ queryKey: ["method"] }); qc.invalidateQueries({ queryKey: ["explore-catalog"] }); },
  });
  const remove = useMutation({
    mutationFn: (key: string) => api<{ used_by: string[] }>(`/method/metrics/${encodeURIComponent(key)}`, { method: "DELETE" }),
    onSuccess: (r) => { setUsedBy(r.used_by); qc.invalidateQueries({ queryKey: ["method"] }); qc.invalidateQueries({ queryKey: ["explore-catalog"] }); },
  });
  const k: Kind = kind === "pct" ? "pct" : kind === "x" ? "mult" : "num";
  const p = preview.data;
  return (
    <>
      <Card title="Le mie metriche" right="valgono ovunque: Esplora, Confronta, strategie e backtest">
        <p className="honest" style={{ marginTop: 0 }}>Una metrica tua è una formula sulle metriche del catalogo, per esempio <code>fcf_yield - 0.02 * net_debt_ebitda</code> (rendimento del FCF penalizzato dal debito) oppure <code>roic - 0.08</code> (ROIC oltre un costo del capitale dell'8%).</p>
        {m.custom_metrics.length === 0 ? <p className="honest">Nessuna metrica personale: creala qui sotto.</p> : (
          <table className="dense">
            <thead><tr><th>Nome</th><th>Formula</th><th>Si legge come</th><th>Meglio se</th><th aria-label="Azioni" /></tr></thead>
            <tbody>{m.custom_metrics.map((c) => (
              <tr key={c.key}>
                <td><b>{c.label}</b>{c.description ? <div className="honest">{c.description}</div> : null}</td>
                <td><code>{c.formula}</code></td>
                <td>{m.formats.find((f) => f.value === c.fmt)?.label ?? c.fmt}</td>
                <td>{c.better === "high" ? "più alta" : c.better === "low" ? "più bassa" : "–"}</td>
                <td className="n"><button type="button" className="btn ghost" aria-label={`Elimina ${c.label}`}
                  onClick={() => { if (window.confirm(`Eliminare "${c.label}"? Sparisce da Esplora, Confronta e dalle strategie che la usano.`)) remove.mutate(c.key); }}>✕</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {usedBy && usedBy.length > 0 && <Notice tone="warn" title="Da sistemare:">la usavano queste tue strategie: {usedBy.join(", ")}.</Notice>}
      </Card>
      <Card title="Nuova metrica">
        <div className="form-grid">
          <label className="field"><span>Nome</span><input className="text-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="es. FCF corretto per il debito" /></label>
          <div className="row">
            <label className="field"><span>Si legge come</span>
              <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>{m.formats.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}</select></label>
            <label className="field"><span>Meglio se</span>
              <select className="select" value={better} onChange={(e) => setBetter(e.target.value as "" | "high" | "low")}>
                <option value="high">più alta</option><option value="low">più bassa</option><option value="">nessuno dei due</option></select></label>
          </div>
          <label className="field wide"><span>Formula</span><input className="text-input mono" value={formula} onChange={(e) => setFormula(e.target.value)} placeholder="es. fcf_yield - 0.02 * net_debt_ebitda" spellCheck={false} /></label>
          <label className="field wide"><span>Cosa misura (facoltativo)</span><input className="text-input" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </div>
        {debounced && (preview.isError ? (
          <Notice tone="neg">{preview.error instanceof ApiError ? preview.error.message : String(preview.error)}</Notice>
        ) : p ? (p.computable === 0 ? (
          <Notice tone="warn">Oggi non è calcolabile per nessuna delle {p.total} aziende: a tutte manca almeno una delle metriche usate.</Notice>
        ) : (
          <Notice tone="info">
            Oggi calcolabile per {p.computable} aziende su {p.total}. Mediana {fmt(p.median, k)}, dal 10° al 90° percentile {fmt(p.p10, k)} – {fmt(p.p90, k)}.
            {p.best.length > 0 && <> Le migliori oggi: {p.best.map((b, i) => <span key={b.ticker}>{i ? ", " : ""}<Link to={`/azienda/${encodeURIComponent(b.ticker)}`}>{b.ticker}</Link> ({fmt(b.value, k)})</span>)}.</>}
          </Notice>
        )) : null)}
        <div className="actions">
          <button type="button" className="btn primary" disabled={!label.trim() || !formula.trim() || preview.isError || create.isPending} onClick={() => create.mutate()}>Salva la metrica</button>
          {create.isError && <span className="neg">{create.error.message}</span>}
          {create.isSuccess && <span className="pos">Metrica salvata.</span>}
        </div>
        <details className="names">
          <summary>Nomi delle metriche da usare nella formula</summary>
          <p className="honest">Operazioni: + − * / ** e parentesi. Funzioni: {m.functions.join(", ")}. Le percentuali sono frazioni: 15% si scrive 0.15.</p>
          <div className="table-wrap tall">
            <table className="dense"><thead><tr><th>Nome nella formula</th><th>Metrica</th><th>Gruppo</th></tr></thead>
              <tbody>{m.formula_names.map((n) => (
                <tr key={n.key}><td><button type="button" className="code-link" onClick={() => setFormula((f) => (f ? `${f} ${n.key}` : n.key))}>{n.key}</button></td><td>{n.label}</td><td>{n.group}</td></tr>
              ))}</tbody></table>
          </div>
        </details>
      </Card>
    </>
  );
}

function Checklist({ m }: { m: MethodPage }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [h, setH] = useState("");
  const [editing, setEditing] = useState<{ id: number; question: string; help: string; weight: number } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["method"] });
  const add = useMutation({ mutationFn: () => api("/method/checklist", { method: "POST", body: JSON.stringify({ question: q, help: h }) }), onSuccess: () => { setQ(""); setH(""); refresh(); } });
  const edit = useMutation({ mutationFn: () => api(`/method/checklist/${editing!.id}`, { method: "PUT", body: JSON.stringify({ question: editing!.question, help: editing!.help, weight: editing!.weight }) }), onSuccess: () => { setEditing(null); refresh(); } });
  const remove = useMutation({ mutationFn: (id: number) => api(`/method/checklist/${id}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <Card title="La mia checklist" right="voti da 1 a 5 nella scheda; il voto complessivo è la media pesata">
      <p className="honest" style={{ marginTop: 0 }}>Le domande che ti fai su ogni azienda. Togliere una domanda non cancella i voti già dati.</p>
      {m.checklist.length === 0 ? <p className="honest">Nessuna domanda: aggiungi la prima qui sotto.</p> : (
        <ul className="questions">
          {m.checklist.map((c) => (
            <li key={c.id}>
              {editing?.id === c.id ? (
                <div className="form-grid">
                  <label className="field wide"><span>Domanda</span><input className="text-input" value={editing.question} onChange={(e) => setEditing({ ...editing, question: e.target.value })} /></label>
                  <label className="field wide"><span>Come rispondere</span><input className="text-input" value={editing.help} onChange={(e) => setEditing({ ...editing, help: e.target.value })} /></label>
                  <div className="field"><span>Peso</span>
                    <Segmented label="Peso" value={String(editing.weight)} onChange={(v) => setEditing({ ...editing, weight: Number(v) })}
                      options={["0", "0.5", "1", "1.5", "2", "2.5", "3"].map((w) => ({ value: w, label: w.replace(".", ",") }))} /></div>
                  <div className="actions">
                    <button type="button" className="btn primary" disabled={edit.isPending} onClick={() => edit.mutate()}>Salva la domanda</button>
                    <button type="button" className="btn ghost" onClick={() => setEditing(null)}>Annulla</button>
                    {edit.isError && <span className="neg">{edit.error.message}</span>}
                  </div>
                </div>
              ) : (
                <div className="q-row">
                  <div><b>{c.question}</b>{c.help && <div className="honest">{c.help}</div>}</div>
                  <span className="honest">peso {fmt(c.weight, "num", { digits: 1 })}</span>
                  <button type="button" className="btn ghost" onClick={() => setEditing({ ...c })}>Modifica</button>
                  <button type="button" className="btn ghost" aria-label={`Togli la domanda ${c.question}`} onClick={() => remove.mutate(c.id)}>✕</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="sub">Nuova domanda</div>
      <div className="row">
        <input className="text-input grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="es. Il debito è gestibile anche in una recessione?" aria-label="Nuova domanda" />
        <input className="text-input grow" value={h} onChange={(e) => setH(e.target.value)} placeholder="Come rispondere (facoltativo)" aria-label="Come rispondere" />
        <button type="button" className="btn primary" disabled={!q.trim() || add.isPending} onClick={() => add.mutate()}>Aggiungi la domanda</button>
      </div>
    </Card>
  );
}

export function MethodPage() {
  const [tab, setTab] = useState<Tab>("profilo");
  const q = useQuery({ queryKey: ["method"], queryFn: () => api<MethodPage>("/method") });
  return (
    <div className="page">
      <h1>Il mio metodo</h1>
      <p className="lead">Qui metti del tuo: come investi, le tue metriche, le domande che ti fai su ogni azienda. Tutto resta salvato e guida il resto dell'app.</p>
      <div><Segmented label="Sezione" value={tab} onChange={setTab}
        options={[{ value: "profilo", label: "Il mio profilo" }, { value: "metriche", label: "Le mie metriche" }, { value: "checklist", label: "La mia checklist" }]} /></div>
      {q.isPending ? <Skeleton height={300} /> : q.isError ? <Empty what="Pagina non disponibile" why={q.error.message} /> : (
        tab === "profilo" ? <ProfileForm key={JSON.stringify(q.data.profile)} m={q.data} /> : tab === "metriche" ? <Metrics m={q.data} /> : <Checklist m={q.data} />
      )}
    </div>
  );
}
