// Diario delle decisioni: riepilogo (quante volte avevi ragione contro l'indice, dopo almeno 3 mesi), decisioni da
// rivedere con il modulo di revisione, nuova decisione, tutte le decisioni con l'esito. Controlli e calcoli nel motore
// (servizio portfolio, modules/journal.py): qui solo moduli e tabella.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api, type CompanyRow, type Journal, type JournalEntry } from "../lib/api";
import { fmt } from "../lib/fmt";
import { t, tn } from "../lib/i18n";

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
    <Card title={t("Nuova decisione")} right={t("il perché conta più della decisione")}>
      <div className="form-grid">
        <label className="field"><span>{t("Azienda")}</span>
          {ticker ? (
            <span className="chip">{ticker}<button type="button" aria-label={t("Cambia azienda")} onClick={() => { setTicker(""); setQ(""); }}>✕</button></span>
          ) : (
            <div className="add-company">
              <div className="search"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Scrivi il ticker o il nome")} style={{ paddingLeft: 12 }} aria-label={t("Azienda")} /></div>
              {(res.data?.results.length ?? 0) > 0 && (
                <ul className="pick">{res.data!.results.map((r) => (
                  <li key={r.ticker}><span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
                    <button type="button" className="btn" onClick={() => setTicker(r.ticker)}>{t("Scegli")}</button></li>
                ))}</ul>
              )}
            </div>
          )}
        </label>
        <label className="field"><span>{t("Decisione")}</span>
          <select className="select" value={action} onChange={(e) => setAction(e.target.value)}>
            {j.actions.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </label>
        <label className="field wide"><span>{t("Perché")}</span>
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("La tesi in due righe: cosa ti aspetti e perché")} />
        </label>
        <label className="field wide"><span>{t("Cosa mi farebbe cambiare idea")}</span>
          <input className="text-input" value={change} onChange={(e) => setChange(e.target.value)} placeholder={t("es. margine operativo sotto il 10% per due trimestri")} />
        </label>
        <label className="field"><span>{t("Prezzo (vuoto = ultima chiusura)")}</span>
          <input className="text-input" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={t("es. 182,50")} />
        </label>
        <label className="field"><span>{t("Rivedi il (vuoto = fra 6 mesi)")}</span>
          <input className="text-input" type="date" value={review} onChange={(e) => setReview(e.target.value)} />
        </label>
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={!ticker || !reason.trim() || save.isPending} onClick={() => save.mutate()}>{t("Salva decisione")}</button>
        <button type="button" className="btn ghost" onClick={onDone}>{t("Annulla")}</button>
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
        <b><Link to={`/azienda/${encodeURIComponent(e.ticker)}`}>{e.ticker}</Link> · {t("{action} il {date} a {price}", { action: e.action_label, date: fmt(e.date, "date"), price: fmt(e.price, "price", { currency: e.currency }) })}</b>
        <span>{t("Da allora")} <Num v={e.ret} kind="pct" change /> · {bench} <Num v={e.bench_ret} kind="pct" change /> · {t("differenza")} <Num v={e.excess} kind="pp" /></span>
      </div>
      <p className="honest">{t("Perché:")} {e.reason || "–"}<br />{t("Cosa mi farebbe cambiare idea:")} {e.change_mind || "–"}</p>
      <div className="row">
        <input className="text-input grow" value={note} onChange={(x) => setNote(x.target.value)} placeholder={t("Com'è andata e cosa hai imparato")} aria-label={t("Com'è andata")} />
        <div className="pills" role="radiogroup" aria-label={t("Giudizio")}>
          {verdicts.map((v) => <button key={v} type="button" role="radio" aria-checked={verdict === v} className={`pill${verdict === v ? " on" : ""}`} onClick={() => setVerdict(v)}>{t(v)}</button>)}
        </div>
        <button type="button" className="btn primary" disabled={!verdict || save.isPending} onClick={() => save.mutate()}>{t("Salva la revisione")}</button>
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
  if (q.isPending) return <div className="page"><h1>{t("Diario delle decisioni")}</h1><Skeleton height={240} /></div>;
  if (q.isError) return <div className="page"><h1>{t("Diario delle decisioni")}</h1><Empty what={t("Diario non disponibile")} why={q.error.message} /></div>;
  const j = q.data;
  const due = j.entries.filter((e) => e.review_due);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{t("Diario delle decisioni")}</h1>
          <p className="lead">{t("Annota ogni decisione con il perché: dopo almeno 3 mesi l'app ti dice se avevi ragione contro l'indice ({benchmark}).", { benchmark: j.benchmark })}</p></div>
        {!adding && <button type="button" className="btn primary" onClick={() => setAdding(true)}>+ {t("Decisione")}</button>}
      </div>
      {adding && <NewDecision j={j} onDone={() => setAdding(false)} />}
      {j.entries.length === 0 ? (
        !adding && <Empty what={t("Il diario è vuoto")} why={t("Annota ogni decisione (compro, vendo, non compro, aspetto) con il perché e cosa ti farebbe cambiare idea. Anche il perché scritto quando aggiungi un acquisto nel Portafoglio arriva qui.")} />
      ) : (
        <>
          <Card>
            <dl className="kv big">
              <div><dt>{t("Decisioni")}</dt><dd>{fmt(j.summary.decisions, "int")}</dd></div>
              <div><dt>{t("Giudicabili (3 mesi e più)")}</dt><dd>{fmt(j.summary.judged, "int")}</dd></div>
              <div><dt>{t("Avevi ragione")}</dt><dd>{fmt(j.summary.rate, "pct", { digits: 0 })}</dd></div>
              <div><dt>{t("Revisioni scadute")}</dt><dd className={j.summary.due ? "warn" : undefined}>{fmt(j.summary.due, "int")}</dd></div>
            </dl>
            <p className="honest">{t("Ragione = il titolo ha fatto meglio dell'indice ({benchmark}) se avevi comprato o deciso di aspettare, peggio se avevi venduto o deciso di non comprare. Prima di 3 mesi il confronto dice poco.", { benchmark: j.benchmark })}</p>
          </Card>
          {due.length > 0 && (
            <Card title={t("Da rivedere")} right={tn(due.length, "{n} decisione con la data di revisione passata", "{n} decisioni con la data di revisione passata")}>
              {due.map((e) => <ReviewForm key={e.id} e={e} verdicts={j.verdicts} bench={j.benchmark} />)}
            </Card>
          )}
          <Card title={t("Tutte le decisioni")} right={t("dalla più recente")}>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>{t("Data")}</th><th>{t("Titolo")}</th><th>{t("Decisione")}</th><th className="n">{t("Prezzo")}</th><th className="n">{t("Da allora")}</th><th className="n">{t("Contro l'indice")}</th><th>{t("Esito")}</th><th>{t("Perché")}</th><th>{t("La tua revisione")}</th><th aria-label={t("Azioni sulla riga")} /></tr></thead>
                <tbody>
                  {j.entries.map((e) => (
                    <tr key={e.id}>
                      <td>{fmt(e.date, "date_short")}</td>
                      <td><Link to={`/azienda/${encodeURIComponent(e.ticker)}`}><b>{e.ticker}</b></Link></td>
                      <td>{e.action_label}</td>
                      <td className="n">{fmt(e.price, "price", { currency: e.currency })}</td>
                      <td className="n"><Num v={e.ret} kind="pct" change /></td>
                      <td className="n"><Num v={e.excess} kind="pp" /></td>
                      <td>{e.right === true ? <Badge tone="pos">✓ {t("ragione")}</Badge> : e.right === false ? <Badge tone="neg">✗ {t("torto")}</Badge> : <Badge tone="off">{t("presto")}</Badge>}</td>
                      <td className="wrap clip" title={e.reason ?? undefined}>{e.reason}</td>
                      <td className="wrap clip">{e.verdict ? `${t(e.verdict)}${e.review_note ? `: ${e.review_note}` : ""}` : <span className="na">–</span>}</td>
                      <td className="n"><button type="button" className="btn ghost" aria-label={t("Elimina la decisione su {ticker} del {date}", { ticker: e.ticker, date: fmt(e.date, "date_short") })}
                        onClick={() => { if (window.confirm(t("Eliminare la decisione su {ticker} del {date}? Non si può annullare.", { ticker: e.ticker, date: fmt(e.date, "date") }))) del.mutate(e.id); }}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="honest">{t("Esito \"presto\": meno di 3 mesi, o prezzi mancanti per il confronto.")}</p>
            {del.isError && <Notice tone="neg">{t("Non eliminata: {error}", { error: del.error.message })}</Notice>}
          </Card>
        </>
      )}
    </div>
  );
}
