// Funzioni della scheda "Concorrenti" (RV) e "Notizie e documenti" (CF). Dati pronti dall'API (servizio company).
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Num, Skeleton } from "../components/ui";
import { api, type Documents, type Peers } from "../lib/api";
import { fmt, kindOfMetric } from "../lib/fmt";

export function PeersPanel({ ticker }: { ticker: string }) {
  const q = useQuery({ queryKey: ["peers", ticker], queryFn: () => api<Peers>(`/companies/${encodeURIComponent(ticker)}/peers`) });
  if (q.isPending) return <Skeleton height={260} />;
  if (q.isError) return <Empty what="Concorrenti non disponibili" why={q.error.message} />;
  const p = q.data;
  if (p.empty) return <Empty what="Nessun gruppo di confronto" why={p.why} />;
  const others = p.rows.filter((r) => !r.me).slice(0, 4).map((r) => r.ticker);
  return (
    <div className="financials">
      <Card title={`Le più grandi di «${p.group}»`} right={<Link to={`/confronta?t=${[ticker, ...others].join(",")}`}>Confronta con le prime {others.length} →</Link>}>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Ticker</th><th>Azienda</th>{p.columns.map((c) => <th key={c.key} className="n">{c.label}</th>)}</tr></thead>
            <tbody>
              {p.rows.map((r) => (
                <tr key={r.ticker} className={r.me ? "me" : undefined}>
                  <td>{r.me ? <b>{r.ticker}</b> : <Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link>}</td>
                  <td className="nm">{r.name}</td>
                  {p.columns.map((c) => (
                    <td key={c.key} className="n"><Num v={r.values[c.key]} kind={kindOfMetric(c.fmt)} change={c.key === "ret_1y"} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`${ticker} contro le mediane del gruppo`}>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Metrica</th><th className="n">{ticker}</th><th className="n">Mediana del gruppo</th><th>Rispetto al gruppo</th></tr></thead>
            <tbody>
              {p.versus.map((v) => {
                const col = p.columns.find((c) => c.key === v.key)!;
                let verdict: JSX.Element | string = "–";
                if (v.own !== null && v.median !== null && col.better) {
                  const better = col.better === "high" ? v.own > v.median : v.own < v.median;
                  verdict = v.own === v.median ? "in linea" : <span className={better ? "pos" : "neg"}>{better ? "▲ meglio" : "▼ peggio"}</span>;
                }
                return (
                  <tr key={v.key}>
                    <td>{col.label}</td>
                    <td className="n"><b><Num v={v.own} kind={kindOfMetric(col.fmt)} /></b></td>
                    <td className="n"><Num v={v.median} kind={kindOfMetric(col.fmt)} /></td>
                    <td>{verdict}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="honest">"Meglio" e "peggio" seguono il verso di ogni metrica (per P/E e simili, più basso è meglio); dimensione e rendimento non hanno un verso.</p>
      </Card>
    </div>
  );
}

function when(iso: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return fmt(iso, "date_short"); // solo la data: niente ora inventata dal fuso
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return fmt(iso, "date_short");
  const today = new Date();
  const hm = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? hm : `${fmt(iso.slice(0, 10), "date_short")} ${hm}`;
}

export function DocumentsPanel({ ticker }: { ticker: string }) {
  const q = useQuery({ queryKey: ["documents", ticker], queryFn: () => api<Documents>(`/companies/${encodeURIComponent(ticker)}/documents`) });
  if (q.isPending) return <Skeleton height={260} />;
  if (q.isError) return <Empty what="Documenti non disponibili" why={q.error.message} />;
  const d = q.data;
  return (
    <div className="financials">
      <Card title="Ultime notizie" right={`titoli che citano ${ticker}`}>
        {!d.news_enabled ? <p className="honest">Notizie spente nella configurazione.</p> : d.news.length === 0 ? (
          <p className="honest" style={{ margin: 0 }}>Nessuna notizia recente che citi {ticker}. Le notizie arrivano da feed pubblici con un ritardo tipico di 1-15 minuti.</p>
        ) : (
          <ul className="news">
            {d.news.map((n) => (
              <li key={n.link}>
                <span className="age">{when(n.published)}</span>
                <span className="src">{n.source}</span>
                <a href={n.link} target="_blank" rel="noopener noreferrer">{n.title}</a>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Documenti depositati" right={d.filings_total ? `${d.filings_total} · 10-K, 10-Q, 8-K dalla SEC` : undefined}>
        {d.filings.length === 0 ? <Empty what="Nessun deposito in archivio" why={d.why_no_filings} /> : (
          <>
            <p className="honest" style={{ marginTop: 0 }}>⚠ = da guardare subito (revisore, bilanci non affidabili, fallimento, svalutazioni…). Il link apre il documento sul sito della SEC.</p>
            <div className="table-wrap tall">
              <table className="dense">
                <thead><tr><th>Depositato</th><th>Documento</th><th>Cosa</th><th aria-label="Link" /></tr></thead>
                <tbody>
                  {d.filings.map((f, i) => (
                    <tr key={`${f.url}-${i}`} className={f.important ? "warn-row" : undefined}>
                      <td>{when(f.when)}</td>
                      <td>{f.important ? <Badge tone="neg">⚠ {f.form}</Badge> : <Badge>{f.form}</Badge>}</td>
                      <td className="wrap">{f.what}</td>
                      <td>{f.url ? <a href={f.url} target="_blank" rel="noopener noreferrer">apri ↗</a> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {d.filings_total > d.filings.length && <p className="honest">Mostrati i {d.filings.length} più recenti.</p>}
          </>
        )}
      </Card>
    </div>
  );
}
