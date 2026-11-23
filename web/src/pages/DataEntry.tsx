// Inserisci dati: voci di bilancio a mano (prevalgono sulla SEC per lo stesso periodo e voce; i derivati li calcola il
// motore) e aziende fuori dall'indice. Controlli e salvataggio nel motore (servizio data_admin).
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Notice, Segmented, Skeleton } from "../components/ui";
import { api, type CompanyRow } from "../lib/api";
import { fmt } from "../lib/fmt";

type N = number | null;
interface Manual {
  ticker: string; kind: string; unit: string; share_unit: string; currency: string; units: string[]; periods: string[];
  items: { key: string; label: string; values: N[] }[];
  manual: { period_end: string; kind: string; metric: string; label: string; value: N; filed: string }[];
}
interface Custom { currencies: string[]; sectors: string[]; companies: { ticker: string; name: string; sector: string | null; currency: string | null; has_prices: boolean }[] }

const parse = (t: string): number | null => {
  const v = t.trim().replace(/\s/g, "").replace(/\u2212/g, "-"); // fmt scrive i negativi con il segno tipografico
  if (!v) return null;
  // come le celle mostrate: punto = migliaia, virgola = decimali ("1.234,5")
  const x = Number(v.replace(/\./g, "").replace(",", "."));
  return Number.isFinite(x) ? x : null;
};
const show = (v: N) => (v === null ? "" : fmt(v, "num", { digits: Math.abs(v) < 100 ? 2 : 0 }));

function Picker({ ticker, onPick }: { ticker: string; onPick: (t: string) => void }) {
  const [q, setQ] = useState("");
  const res = useQuery({ queryKey: ["search", q], enabled: q.trim().length > 0, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=6`) });
  return (
    <div className="add-company">
      <div className="search"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder={ticker ? `${ticker} · cambia azienda` : "Scrivi il ticker o il nome"} style={{ paddingLeft: 12 }} aria-label="Azienda" /></div>
      {q && (res.data?.results.length ?? 0) > 0 && (
        <ul className="pick">{res.data!.results.map((r) => (
          <li key={r.ticker}><span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
            <button type="button" className="btn" onClick={() => { onPick(r.ticker); setQ(""); }}>Scegli</button></li>
        ))}</ul>
      )}
    </div>
  );
}

function Editor({ m, onSaved }: { m: Manual; onSaved: () => void }) {
  const qc = useQueryClient();
  const [extra, setExtra] = useState<string[]>([]);
  const [newPeriod, setNewPeriod] = useState("");
  const [cells, setCells] = useState<Record<string, string>>({});
  const [validity, setValidity] = useState<"oggi" | "pubblicazione">("oggi");
  const [days, setDays] = useState(60);
  const periods = [...new Set([...m.periods, ...extra])].sort();
  const changes: Record<string, Record<string, number>> = {};
  let bad = 0;
  for (const [k, text] of Object.entries(cells)) {
    const [item, period] = k.split("|");
    const x = parse(text);
    if (x === null) { if (text.trim()) bad++; continue; }
    const old = m.items.find((i) => i.key === item)?.values[m.periods.indexOf(period)] ?? null;
    // testo uguale a quello mostrato (arrotondato): non è una modifica
    if (old !== null && (text.trim() === show(old) || Math.abs(old - x) <= 1e-9 * Math.max(1, Math.abs(old)))) continue;
    (changes[period] ??= {})[item] = x;
  }
  const n = Object.values(changes).reduce((a, c) => a + Object.keys(c).length, 0);
  const save = useMutation({
    mutationFn: () => api<{ saved: number }>(`/manual/${encodeURIComponent(m.ticker)}`, { method: "POST", body: JSON.stringify({
      changes, kind: m.kind, unit: m.unit, share_unit: m.share_unit, days_after_close: validity === "oggi" ? null : days }) }),
    onSuccess: () => { setCells({}); setExtra([]); qc.invalidateQueries({ queryKey: ["manual", m.ticker] }); qc.invalidateQueries({ queryKey: ["company", m.ticker] }); onSaved(); },
  });
  return (
    <>
      <p className="honest">Valori in {m.unit} di {m.currency} (azioni in {m.share_unit}). Già compilati con i dati attuali: modifica solo le celle che vuoi cambiare o aggiungere. Il capex si può scrivere con o senza segno; il free cash flow si calcola da solo.</p>
      <div className="table-wrap">
        <table className="dense edit">
          <thead><tr><th>Voce</th>{periods.map((p) => <th key={p} className="n">{fmt(p, "date")}</th>)}</tr></thead>
          <tbody>{m.items.map((it) => (
            <tr key={it.key}><td>{it.label}</td>{periods.map((p) => {
              const k = `${it.key}|${p}`;
              const i = m.periods.indexOf(p);
              const v = cells[k] ?? (i >= 0 ? show(it.values[i]) : "");
              return <td key={p} className="n"><input className={`cell${cells[k] !== undefined ? " changed" : ""}`} inputMode="decimal" value={v} aria-label={`${it.label} al ${fmt(p, "date")}`}
                onChange={(e) => setCells({ ...cells, [k]: e.target.value })} /></td>;
            })}</tr>
          ))}</tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <input className="text-input" type="date" value={newPeriod} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setNewPeriod(e.target.value)} aria-label="Nuovo periodo (data di chiusura)" />
        <button type="button" className="btn" disabled={!newPeriod || periods.includes(newPeriod)} onClick={() => { setExtra([...extra, newPeriod]); setNewPeriod(""); }}>Aggiungi la colonna</button>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <Segmented label="Da quando valgono" value={validity} onChange={setValidity}
          options={[{ value: "oggi", label: "Da oggi (consigliato: nel backtest non li sapevi)" }, { value: "pubblicazione", label: "Dalla pubblicazione originale" }]} />
        {validity === "pubblicazione" && <label className="check">giorni dopo la chiusura <input className="text-input" type="number" min={0} max={365} value={days} style={{ width: 80 }} onChange={(e) => setDays(Number(e.target.value))} /></label>}
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={!n || bad > 0 || save.isPending} onClick={() => save.mutate()}>{!n ? "Nessuna modifica da salvare" : n === 1 ? "Salva 1 valore" : `Salva ${n} valori`}</button>
        {bad > 0 && <span className="warn">{bad} celle non sono numeri</span>}
        {save.isSuccess && <span className="pos">✓ Salvati {save.data.saved} valori (compresi i derivati): li trovi nella scheda dell'azienda.</span>}
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </>
  );
}

function ManualData() {
  const qc = useQueryClient();
  const [ticker, setTicker] = useState(() => { try { return localStorage.getItem("qe_ultima_azienda") ?? ""; } catch { return ""; } });
  const [kind, setKind] = useState<"annual" | "ttm">("annual");
  const [unit, setUnit] = useState("milioni");
  const [shareUnit, setShareUnit] = useState("milioni");
  const [picked, setPicked] = useState<number[]>([]);
  const q = useQuery({ queryKey: ["manual", ticker, kind, unit, shareUnit], enabled: !!ticker, placeholderData: keepPreviousData,
    queryFn: () => api<Manual>(`/manual/${encodeURIComponent(ticker)}?kind=${kind}&unit=${encodeURIComponent(unit)}&share_unit=${encodeURIComponent(shareUnit)}`) });
  const del = useMutation({
    mutationFn: () => api(`/manual/${encodeURIComponent(ticker)}/delete`, { method: "POST", body: JSON.stringify({
      rows: picked.map((i) => q.data!.manual[i]).map((r) => ({ period_end: r.period_end.slice(0, 10), metric: r.metric, filed: r.filed.slice(0, 10) })) }) }),
    onSuccess: () => { setPicked([]); qc.invalidateQueries({ queryKey: ["manual", ticker] }); },
  });
  const m = q.data;
  return (
    <>
      <Card>
        <div className="form-grid">
          <label className="field"><span>Azienda {ticker && <b>· {ticker}</b>}</span><Picker ticker={ticker} onPick={(t) => { setTicker(t); setPicked([]); }} /></label>
          <div className="field"><span>Periodo</span><Segmented label="Periodo" value={kind} onChange={setKind}
            options={[{ value: "annual", label: "Esercizio completo" }, { value: "ttm", label: "Ultimi 12 mesi a un trimestre" }]} /></div>
          <label className="field"><span>Importi in</span><select className="select" value={unit} onChange={(e) => setUnit(e.target.value)}>{(m?.units ?? ["unità", "migliaia", "milioni", "miliardi"]).map((u) => <option key={u}>{u}</option>)}</select></label>
          <label className="field"><span>Azioni in</span><select className="select" value={shareUnit} onChange={(e) => setShareUnit(e.target.value)}>{(m?.units ?? ["unità", "migliaia", "milioni", "miliardi"]).map((u) => <option key={u}>{u}</option>)}</select></label>
        </div>
        {m && m.currency !== "USD" && <Notice tone="info">{m.ticker} è quotata in {m.currency}: inserisci i bilanci in {m.currency}, la stessa valuta del prezzo, altrimenti capitalizzazione e multipli non tornano.</Notice>}
      </Card>
      {!ticker ? <Empty what="Scegli un'azienda" why="Cerca il ticker o il nome qui sopra." /> : q.isPending ? <Skeleton height={300} /> : q.isError ? <Empty what="Non disponibile" why={q.error.message} /> : (
        <>
          <Card title={`Dati di bilancio di ${m!.ticker}`}><Editor key={`${m!.ticker}-${kind}-${unit}-${shareUnit}`} m={m!} onSaved={() => setPicked([])} /></Card>
          <Card title="Valori che hai inserito" right={m!.manual.length ? `${m!.manual.length} valori` : undefined}>
            {m!.manual.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun valore inserito a mano per {m!.ticker}: quelli che salvi qui sopra compaiono qui, da dove puoi anche toglierli.</p> : (
              <>
                <div className="table-wrap" style={{ maxHeight: 360 }}>
                  <table className="dense">
                    <thead><tr><th aria-label="Scegli" /><th>Chiusura</th><th>Periodo</th><th>Voce</th><th className="n">Valore</th><th>Vale dal</th></tr></thead>
                    <tbody>{m!.manual.map((r, i) => (
                      <tr key={i}><td><input type="checkbox" checked={picked.includes(i)} aria-label={`Scegli ${r.label}`} onChange={(e) => setPicked(e.target.checked ? [...picked, i] : picked.filter((x) => x !== i))} /></td>
                        <td>{fmt(r.period_end.slice(0, 10), "date")}</td><td>{r.kind}</td><td>{r.label}</td><td className="n">{fmt(r.value, "num", { digits: 0 })}</td><td>{fmt(r.filed.slice(0, 10), "date")}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
                <div className="actions">
                  <button type="button" className="btn" disabled={!picked.length || del.isPending} onClick={() => del.mutate()}>{picked.length ? (picked.length === 1 ? "Togli il valore scelto" : `Togli i ${picked.length} valori scelti`) : "Scegli i valori da togliere"}</button>
                  {del.isSuccess && <span className="honest">Tolti: dove c'era, torna a valere il dato SEC.</span>}
                </div>
              </>
            )}
          </Card>
        </>
      )}
    </>
  );
}

function CustomCompanies() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["custom-companies"], queryFn: () => api<Custom>("/custom-companies") });
  const [f, setF] = useState({ symbol: "", name: "", sector: "", currency: "USD" });
  const add = useMutation({
    mutationFn: () => api<{ ticker: string }>("/custom-companies", { method: "POST", body: JSON.stringify({ ...f, sector: f.sector || null }) }),
    onSuccess: () => { setF({ symbol: "", name: "", sector: "", currency: "USD" }); qc.invalidateQueries({ queryKey: ["custom-companies"] }); },
  });
  const remove = useMutation({
    mutationFn: (t: string) => api(`/custom-companies/${encodeURIComponent(t)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["custom-companies"] }),
  });
  if (q.isPending) return <Skeleton height={200} />;
  if (q.isError) return <Empty what="Non disponibile" why={q.error.message} />;
  const c = q.data;
  return (
    <>
      <Card title="Aggiungi un'azienda">
        <p className="honest" style={{ marginTop: 0 }}>Per un'azienda fuori dall'S&P 500 serve il simbolo di Yahoo Finance (es. ENI.MI per Eni a Milano, NESN.SW per Nestlé, ASML.AS per ASML ad Amsterdam). I prezzi si scaricano con l'aggiornamento dei dati; per le americane anche i bilanci SEC, per le altre li inserisci tu. Le aziende aggiunte non entrano nei backtest delle strategie.</p>
        <div className="form-grid">
          <label className="field"><span>Simbolo Yahoo</span><input className="text-input mono" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} placeholder="es. ENI.MI" /></label>
          <label className="field"><span>Nome</span><input className="text-input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="es. Eni" /></label>
          <label className="field"><span>Settore</span><select className="select" value={f.sector} onChange={(e) => setF({ ...f, sector: e.target.value })}><option value="">Scegli il settore</option>{c.sectors.map((s) => <option key={s}>{s}</option>)}</select></label>
          <label className="field"><span>Valuta del prezzo e dei bilanci</span><select className="select" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}>{c.currencies.map((s) => <option key={s}>{s}</option>)}</select></label>
        </div>
        <div className="actions">
          <button type="button" className="btn primary" disabled={!f.symbol.trim() || add.isPending} onClick={() => add.mutate()}>Aggiungi l'azienda</button>
          {add.isSuccess && <span className="pos">✓ {add.data.ticker} aggiunta: i prezzi arrivano con il prossimo aggiornamento dei dati.</span>}
          {add.isError && <span className="neg">{add.error.message}</span>}
        </div>
      </Card>
      {c.companies.length > 0 && (
        <Card title="Aziende che hai aggiunto" right={`${c.companies.length}`}>
          <table className="dense">
            <tbody>{c.companies.map((x) => (
              <tr key={x.ticker}><td><Link to={`/azienda/${encodeURIComponent(x.ticker)}`}><b>{x.ticker}</b></Link></td><td>{x.name}</td><td>{x.sector ?? "settore –"}</td><td>{x.currency}</td>
                <td>{x.has_prices ? <Badge tone="pos">✓ prezzi presenti</Badge> : <Badge tone="warn">! prezzi da scaricare</Badge>}</td>
                <td className="n"><button type="button" className="btn ghost" onClick={() => { if (window.confirm(`Togliere ${x.ticker} dalle tue aziende? I valori di bilancio inseriti restano salvati e tornano se la riaggiungi.`)) remove.mutate(x.ticker); }}>Togli</button></td></tr>
            ))}</tbody>
          </table>
        </Card>
      )}
    </>
  );
}

export function DataEntryPage() {
  const [tab, setTab] = useState<"bilancio" | "aziende">("bilancio");
  return (
    <div className="page">
      <h1>Inserisci dati</h1>
      <p className="lead">Inserisci le voci di bilancio che vuoi, per qualsiasi periodo: tutto il resto (free cash flow, margini, multipli, ROIC, giudizio, DCF) lo calcola l'app. Un valore inserito a mano prevale su quello della SEC per lo stesso periodo e voce.</p>
      <div style={{ marginBottom: 16 }}>
        <Segmented label="Sezione" value={tab} onChange={setTab} options={[{ value: "bilancio", label: "Dati di bilancio" }, { value: "aziende", label: "Aggiungi un'azienda" }]} />
      </div>
      {tab === "bilancio" ? <ManualData /> : <CustomCompanies />}
    </div>
  );
}
