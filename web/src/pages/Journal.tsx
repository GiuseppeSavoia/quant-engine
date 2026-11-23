// Diario delle decisioni: riepilogo (quante volte avevi ragione contro l'indice, dopo almeno 3 mesi), decisioni da
// rivedere con il modulo di revisione, nuova decisione, tutte le decisioni con l'esito. Controlli e calcoli nel motore
// (servizio portfolio, modules/journal.py): qui solo moduli e tabella.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api, type CompanyRow, type Journal, type JournalEntry } from "../lib/api";
import { fmt } from "../lib/fmt";

function NewDecision({ j, onDone }: { j: Journal; onDone: () => void }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [ticker, setTicker] = useState("");
  const [action, setAction] = useState(j.actions[0]?.value ?? "BUY");
  const [reason, setReason] = useState("");
  const [change, setChange] = useState("");
  const [price, setPrice] = useState("");
  const [review, setReview] = useState("");
  const res = useQuery({
    queryKey: ["search", q], enabled: q.trim().length > 0 && !ticker, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=6`),
  });
  const save = useMutation({
    mutationFn: () => {
      const p = price.trim() ? Number(price.replace(/\./g, "").replace(",", ".")) : null;
      return api<{ id: number }>("/journal", { method: "POST", body: JSON.stringify({
        ticker, action, reason, change_mind: change || null, price: p, review_date: review || null }) });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["journal"] }); onDone(); },
  });
  return (
    <Card title="Nuova decisione" right="il perché conta più della decisione">
      <div className="form-grid">
        <label className="field"><span>Azienda</span>
          {ticker ? (
            <span className="chip">{ticker}<button type="button" aria-label="Cambia azienda" onClick={() => { setTicker(""); setQ(""); }}>✕</button></span>
          ) : (
            <div className="add-company">
              <div className="search"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Scrivi il ticker o il nome" style={{ paddingLeft: 12 }} aria-label="Azienda" /></div>
              {(res.data?.results.length ?? 0) > 0 && (
                <ul className="pick">{res.data!.results.map((r) => (
                  <li key={r.ticker}><span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
                    <button type="button" className="btn" onClick={() => setTicker(r.ticker)}>Scegli</button></li>
                ))}</ul>
              )}
            </div>
          )}
        </label>
        <label className="field"><span>Decisione</span>
          <select className="select" value={action} onChange={(e) => setAction(e.target.value)}>
            {j.actions.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </label>
        <label className="field wide"><span>Perché</span>
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="La tesi in due righe: cosa ti aspetti e perché" />
        </label>
        <label className="field wide"><span>Cosa mi farebbe cambiare idea</span>
          <input className="text-input" value={change} onChange={(e) => setChange(e.target.value)} placeholder="es. margine operativo sotto il 10% per due trimestri" />
        </label>
        <label className="field"><span>Prezzo (vuoto = ultima chiusura)</span>
          <input className="text-input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="es. 182,50" />
        </label>
        <label className="field"><span>Rivedi il (vuoto = fra 6 mesi)</span>
          <input className="text-input" type="date" value={review} onChange={(e) => setReview(e.target.value)} />
        </label>
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={!ticker || !reason.trim() || save.isPending} onClick={() => save.mutate()}>Salva decisione</button>
        <button type="button" className="btn ghost" onClick={onDone}>Annulla</button>
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </Card>
  );
}

function ReviewForm({ e, verdicts, bench }: { e: JournalEntry; verdicts: string[]; bench: string }) {
  const qc = useQueryClient();
  const [verdict, setVerdict] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api(`/journal/${e.id}/review`, { method: "POST", body: JSON.stringify({ verdict, note }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["journal"] }),
  });
  return (
    <div className="review">
      <div className="review-head">
        <b><Link to={`/azienda/${encodeURIComponent(e.ticker)}`}>{e.ticker}</Link> · {e.action_label} il {fmt(e.date, "date")} a {fmt(e.price, "price", { currency: e.currency })}</b>
        <span>Da allora <Num v={e.ret} kind="pct" change /> · {bench} <Num v={e.bench_ret} kind="pct" change /> · differenza <Num v={e.excess} kind="pp" /></span>
      </div>
      <p className="honest">Perché: {e.reason || "–"}<br />Cosa mi farebbe cambiare idea: {e.change_mind || "–"}</p>
      <div className="row">
        <input className="text-input grow" value={note} onChange={(x) => setNote(x.target.value)} placeholder="Com'è andata e cosa hai imparato" aria-label="Com'è andata" />
        <div className="pills" role="radiogroup" aria-label="Giudizio">
          {verdicts.map((v) => <button key={v} type="button" role="radio" aria-checked={verdict === v} className={`pill${verdict === v ? " on" : ""}`} onClick={() => setVerdict(v)}>{v}</button>)}
        </div>
        <button type="button" className="btn primary" disabled={!verdict || save.isPending} onClick={() => save.mutate()}>Salva la revisione</button>
      </div>
    </div>
  );
}

export function JournalPage() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["journal"], queryFn: () => api<Journal>("/journal") });
  const del = useMutation({
    mutationFn: (id: number) => api(`/journal/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["journal"] }),
  });
  if (q.isPending) return <div className="page"><h1>Diario delle decisioni</h1><Skeleton height={240} /></div>;
  if (q.isError) return <div className="page"><h1>Diario delle decisioni</h1><Empty what="Diario non disponibile" why={q.error.message} /></div>;
  const j = q.data;
  const due = j.entries.filter((e) => e.review_due);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Diario delle decisioni</h1>
          <p className="lead">Annota ogni decisione con il perché: dopo almeno 3 mesi l'app ti dice se avevi ragione contro l'indice ({j.benchmark}).</p></div>
        {!adding && <button type="button" className="btn primary" onClick={() => setAdding(true)}>+ Decisione</button>}
      </div>
      {adding && <NewDecision j={j} onDone={() => setAdding(false)} />}
      {j.entries.length === 0 ? (
        !adding && <Empty what="Il diario è vuoto" why="Annota ogni decisione (compro, vendo, non compro, aspetto) con il perché e cosa ti farebbe cambiare idea. Anche il perché scritto quando aggiungi un acquisto nel Portafoglio arriva qui." />
      ) : (
        <>
          <Card>
            <dl className="kv big">
              <div><dt>Decisioni</dt><dd>{fmt(j.summary.decisions, "int")}</dd></div>
              <div><dt>Giudicabili (3 mesi e più)</dt><dd>{fmt(j.summary.judged, "int")}</dd></div>
              <div><dt>Avevi ragione</dt><dd>{fmt(j.summary.rate, "pct", { digits: 0 })}</dd></div>
              <div><dt>Revisioni scadute</dt><dd className={j.summary.due ? "warn" : undefined}>{fmt(j.summary.due, "int")}</dd></div>
            </dl>
            <p className="honest">Ragione = il titolo ha fatto meglio dell'indice ({j.benchmark}) se avevi comprato o deciso di aspettare, peggio se avevi venduto o deciso di non comprare. Prima di 3 mesi il confronto dice poco.</p>
          </Card>
          {due.length > 0 && (
            <Card title="Da rivedere" right={`${due.length} ${due.length === 1 ? "decisione" : "decisioni"} con la data di revisione passata`}>
              {due.map((e) => <ReviewForm key={e.id} e={e} verdicts={j.verdicts} bench={j.benchmark} />)}
            </Card>
          )}
          <Card title="Tutte le decisioni" right="dalla più recente">
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Data</th><th>Titolo</th><th>Decisione</th><th className="n">Prezzo</th><th className="n">Da allora</th><th className="n">Contro l'indice</th><th>Esito</th><th>Perché</th><th>La tua revisione</th><th aria-label="Azioni" /></tr></thead>
                <tbody>
                  {j.entries.map((e) => (
                    <tr key={e.id}>
                      <td>{fmt(e.date, "date_short")}</td>
                      <td><Link to={`/azienda/${encodeURIComponent(e.ticker)}`}><b>{e.ticker}</b></Link></td>
                      <td>{e.action_label}</td>
                      <td className="n">{fmt(e.price, "price", { currency: e.currency })}</td>
                      <td className="n"><Num v={e.ret} kind="pct" change /></td>
                      <td className="n"><Num v={e.excess} kind="pp" /></td>
                      <td>{e.right === true ? <Badge tone="pos">✓ ragione</Badge> : e.right === false ? <Badge tone="neg">✗ torto</Badge> : <Badge tone="off">presto</Badge>}</td>
                      <td className="wrap clip" title={e.reason ?? undefined}>{e.reason}</td>
                      <td className="wrap clip">{e.verdict ? `${e.verdict}${e.review_note ? `: ${e.review_note}` : ""}` : <span className="na">–</span>}</td>
                      <td className="n"><button type="button" className="btn ghost" aria-label={`Elimina la decisione su ${e.ticker} del ${fmt(e.date, "date_short")}`}
                        onClick={() => { if (window.confirm(`Eliminare la decisione su ${e.ticker} del ${fmt(e.date, "date")}? Non si può annullare.`)) del.mutate(e.id); }}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="honest">Esito "presto": meno di 3 mesi, o prezzi mancanti per il confronto.</p>
            {del.isError && <Notice tone="neg">Non eliminata: {del.error.message}</Notice>}
          </Card>
        </>
      )}
    </div>
  );
}
