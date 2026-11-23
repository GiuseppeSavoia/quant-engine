// Qualità dei dati: 10-Q/10-K depositati di recente con lo stato del caricamento e i controlli dell'ultima analisi.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Notice, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { fmt } from "../lib/fmt";

interface Quality {
  days: number; has_run: boolean; counts: Record<string, number>;
  filings: { filed: string; ticker: string; name: string; form: string; status: string; loaded_at: string | null; accession: string | null }[];
  summary: { severity: string; check: string; n: number }[];
  issues: { ticker: string; check: string; severity: string; detail: string }[];
}
const STATUS: Record<string, string> = { caricato: "✓ caricato", "da caricare": "● da caricare", "non nei dati SEC": "✗ non nei dati SEC" };
const SEV = (s: string) => s === "errore" ? <Badge tone="neg">✗ errore</Badge> : s === "avviso" ? <Badge tone="warn">! avviso</Badge> : <Badge tone="neutral">● {s}</Badge>;

export function QualityPage() {
  const q = useQuery({ queryKey: ["quality"], queryFn: () => api<Quality>("/quality") });
  const [check, setCheck] = useState("");
  if (q.isPending) return <div className="page"><h1>Qualità dei dati</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>Qualità dei dati</h1><Empty what="Non disponibile" why={q.error.message} /></div>;
  const d = q.data;
  const issues = check ? d.issues.filter((i) => i.check === check) : d.issues;
  return (
    <div className="page">
      <h1>Qualità dei dati</h1>
      <Card title="Bilanci depositati alla SEC" right={`ultimi ${d.days} giorni · ${d.counts["caricato"] ?? 0} caricati · ${d.counts["da caricare"] ?? 0} da caricare · ${d.counts["non nei dati SEC"] ?? 0} non nei dati SEC`}>
        {d.filings.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun 10-Q o 10-K registrato negli ultimi {d.days} giorni: l'indice della SEC si controlla a ogni aggiornamento dei dati e con l'aggiornamento automatico serale.</p> : (
          <>
            <div className="table-wrap" style={{ maxHeight: 420 }}>
              <table className="dense">
                <thead><tr><th>Depositato</th><th>Ticker</th><th>Azienda</th><th>Documento</th><th>Stato</th><th>Caricato il</th><th>Numero SEC</th></tr></thead>
                <tbody>{d.filings.map((f) => (
                  <tr key={`${f.ticker}-${f.accession}`}><td>{fmt(f.filed?.slice(0, 10) ?? null, "date_short")}</td><td><Link to={`/azienda/${encodeURIComponent(f.ticker)}`}><b>{f.ticker}</b></Link></td>
                    <td className="clip">{f.name}</td><td>{f.form}</td><td>{STATUS[f.status] ?? f.status}</td><td>{fmt(f.loaded_at?.slice(0, 10) ?? null, "date_short")}</td><td className="mono">{f.accession}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <p className="honest">I dati XBRL arrivano poco dopo il deposito: «da caricare» si riprova all'aggiornamento successivo. «Non nei dati SEC» capita soprattutto ai documenti depositati insieme da capogruppo e controllate (utility, REIT).</p>
          </>
        )}
      </Card>
      <Card title="Controlli dell'ultima analisi" right={d.issues.length ? `${d.issues.length} segnalazioni` : undefined}>
        {d.issues.length === 0 ? (d.has_run ? <Notice tone="pos">Nessun problema registrato nell'ultima analisi.</Notice>
          : <p className="honest" style={{ margin: 0 }}>Nessuna analisi nel database: i controlli si fanno a ogni aggiornamento dei dati.</p>) : (
          <>
            <table className="dense">
              <thead><tr><th>Gravità</th><th>Controllo</th><th className="n">Titoli</th></tr></thead>
              <tbody>{d.summary.map((s) => <tr key={`${s.severity}-${s.check}`}><td>{SEV(s.severity)}</td><td>{s.check}</td><td className="n">{s.n}</td></tr>)}</tbody>
            </table>
            <div className="row" style={{ margin: "14px 0 8px" }}>
              <select className="select" value={check} onChange={(e) => setCheck(e.target.value)} aria-label="Controllo">
                <option value="">Tutti i controlli</option>{[...new Set(d.issues.map((i) => i.check))].sort().map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="table-wrap" style={{ maxHeight: 480 }}>
              <table className="dense">
                <thead><tr><th>Ticker</th><th>Controllo</th><th>Gravità</th><th>Dettaglio</th></tr></thead>
                <tbody>{issues.map((i, k) => (
                  <tr key={k}><td><Link to={`/azienda/${encodeURIComponent(i.ticker)}`}><b>{i.ticker}</b></Link></td><td>{i.check}</td><td>{SEV(i.severity)}</td><td className="wrap">{i.detail}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
