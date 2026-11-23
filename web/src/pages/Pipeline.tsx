// Pipeline: le aziende che sto studiando, dall'idea alla decisione, con cosa manca per stare a ragione nella fase
// ("fatto quando": il contenuto minimo di un memo d'investimento). Calcoli e controlli nel motore (servizio pipeline).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty, Num, Skeleton } from "../components/ui";
import { VerdictBadge } from "../components/verdict";
import { api, type CompanyRow, type PipelineBoard, type PipelineCard } from "../lib/api";
import { fmt } from "../lib/fmt";

function AddForm({ board, onDone }: { board: PipelineBoard; onDone: () => void }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [ticker, setTicker] = useState("");
  const [stage, setStage] = useState("idea");
  const [note, setNote] = useState("");
  const res = useQuery({
    queryKey: ["search", q], enabled: q.trim().length > 0 && !ticker, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=6`),
  });
  const save = useMutation({
    mutationFn: () => api(`/companies/${encodeURIComponent(ticker)}/pipeline`, { method: "PUT", body: JSON.stringify({ stage, note: note || null }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pipeline"] }); qc.invalidateQueries({ queryKey: ["company-pipeline", ticker] }); onDone(); },
  });
  return (
    <Card title="Aggiungi o sposta un'azienda">
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
        <label className="field"><span>Fase</span>
          <select className="select" value={stage} onChange={(e) => setStage(e.target.value)}>
            {board.stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="field wide"><span>Nota (es. perché la scarto, da dove viene l'idea)</span>
          <input className="text-input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
        </label>
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={!ticker || save.isPending} onClick={() => save.mutate()}>Salva</button>
        <button type="button" className="btn ghost" onClick={onDone}>Annulla</button>
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </Card>
  );
}

function PipeCard({ c }: { c: PipelineCard }) {
  return (
    <div className="pipe-card">
      <div className="top"><Link to={`/azienda/${encodeURIComponent(c.ticker)}`}><b>{c.ticker}</b></Link>{c.category && <VerdictBadge category={c.category} label={c.verdict ?? undefined} />}</div>
      <div className="nm" title={c.name}>{c.name}</div>
      <div className="line">
        <span>{fmt(c.price, "price", { currency: c.currency ?? undefined })}</span>
        <span title="margine del tuo fair value sul prezzo">{c.margin === null ? <span className="na">FV –</span> : <Num v={c.margin} kind="pct" change digits={0} />}</span>
        <span title="giorni in questa fase">{c.days === null ? "" : `${c.days} g`}</span>
      </div>
      {c.missing.length > 0 ? <div className="miss">manca: {c.missing.join(", ")}</div>
        : (c.stage === "pronta" || c.stage === "portafoglio") && <div className="ok">✓ pronta per la fase</div>}
      {c.note && <div className="nt">{c.note}</div>}
    </div>
  );
}

export function PipelinePage() {
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["pipeline"], queryFn: () => api<PipelineBoard>("/pipeline") });
  if (q.isPending) return <div className="page"><h1>Pipeline</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>Pipeline</h1><Empty what="Pipeline non disponibile" why={q.error.message} /></div>;
  const b = q.data;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Pipeline</h1>
          <p className="lead">Le aziende che stai studiando, dall'idea alla decisione. Si aggiungono qui o dalla scheda (menu Pipeline).</p></div>
        {!adding && <button type="button" className="btn primary" onClick={() => setAdding(true)}>+ Azienda</button>}
      </div>
      {(adding || b.count === 0) && <AddForm board={b} onDone={() => setAdding(false)} />}
      {b.count === 0 ? (
        <Empty what="La pipeline è vuota" why="Per passare a «Pronta per decidere» servono tesi in una frase, rischi, cosa ti farebbe cambiare idea, un fair value e una valutazione salvata: è il contenuto minimo di un memo d'investimento." />
      ) : (
        <>
          <div className="pipe-board">
            {b.stages.map((s) => (
              <section key={s.key} className="pipe-col" aria-label={s.label}>
                <h3>{s.label} <span className="count">{s.cards.length}</span></h3>
                {s.cards.map((c) => <PipeCard key={c.ticker} c={c} />)}
              </section>
            ))}
          </div>
          <p className="honest">Fatto quando: «Pronta per decidere» = tesi in una frase, rischi, cosa farebbe cambiare idea (scheda → La mia ricerca), fair value e una valutazione salvata nel diario (scheda → Valutazione); «In portafoglio» anche la data di revisione. Lo storico degli spostamenti resta salvato.</p>
          {b.history.length > 0 && (
            <details className="details card" style={{ padding: 16 }}>
              <summary>Storico degli spostamenti</summary>
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>Quando</th><th>Ticker</th><th>Fase</th><th>Nota</th></tr></thead>
                  <tbody>{b.history.map((h, i) => (
                    <tr key={`${h.ticker}-${h.at}-${i}`}><td>{fmt(h.at.slice(0, 10), "date")} {h.at.slice(11, 16)}</td>
                      <td><Link to={`/azienda/${encodeURIComponent(h.ticker)}`}><b>{h.ticker}</b></Link></td><td>{h.label}</td>
                      <td className="wrap">{h.note ?? ""}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
