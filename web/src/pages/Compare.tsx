// Confronta: da 2 a 5 aziende (indirizzo /confronta?t=AAPL,MSFT). In cima giudizio e pilastri di ognuna, poi le
// metriche del catalogo per gruppo con la migliore di ogni riga (▲ in grassetto), poi i grafici nel tempo. Ogni azienda
// tiene lo stesso colore dappertutto. Numeri e "migliore" dal motore (GET /compare, servizio compare).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PeriodChart } from "../components/charts";
import { VerdictBadge } from "../components/verdict";
import { Card, Empty, Num, Segmented, Skeleton } from "../components/ui";
import { api, ApiError, type CompanyRow, type CompareResult } from "../lib/api";
import { fmt, kindOfMetric } from "../lib/fmt";

const MAX = 5;

function Dot({ i }: { i: number }) {
  return <i className="cdot" style={{ background: `var(--series-${i + 1})` }} aria-hidden="true" />;
}

function Picker({ tickers, onChange }: { tickers: string[]; onChange: (t: string[]) => void }) {
  const [q, setQ] = useState("");
  const res = useQuery({
    queryKey: ["search", q], enabled: q.trim().length > 0, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=6`),
  });
  const results = q.trim() ? (res.data?.results ?? []).filter((r) => !tickers.includes(r.ticker)) : [];
  return (
    <div className="picker">
      <div className="chips">
        {tickers.map((t, i) => (
          <span key={t} className="chip"><Dot i={i} />{t}
            <button type="button" aria-label={`Togli ${t}`} onClick={() => onChange(tickers.filter((x) => x !== t))}>✕</button>
          </span>
        ))}
      </div>
      {tickers.length < MAX && (
        <div className="add-company">
          <div className="search"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Aggiungi un'azienda: ticker o nome"
            aria-label="Aggiungi un'azienda al confronto" style={{ paddingLeft: 12 }} /></div>
          {results.length > 0 && (
            <ul className="pick">
              {results.map((r) => (
                <li key={r.ticker}>
                  <span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
                  <button type="button" className="btn" onClick={() => { onChange([...tickers, r.ticker]); setQ(""); }}>Aggiungi</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const tickers = (params.get("t") ?? "").split(",").map((x) => x.trim()).filter(Boolean).slice(0, MAX);
  const years = Number(params.get("anni") ?? 5);
  const setTickers = (t: string[]) => setParams(t.length ? { t: t.join(","), ...(years !== 5 ? { anni: String(years) } : {}) } : {}, { replace: true });
  const q = useQuery({
    queryKey: ["compare", tickers.join(","), years],
    queryFn: () => api<CompareResult>(`/compare?t=${encodeURIComponent(tickers.join(","))}&years=${years}`),
    enabled: tickers.length >= 2, placeholderData: keepPreviousData, retry: false,
  });
  const r = q.data;
  const order = (t: string) => tickers.findIndex((x) => x.toUpperCase() === t.toUpperCase());

  return (
    <div className="page">
      <h1>Confronta</h1>
      <p className="lead">Da 2 a {MAX} aziende affiancate: giudizio, numeri del catalogo e andamento nel tempo.</p>
      <Card><Picker tickers={tickers} onChange={setTickers} /></Card>
      {tickers.length < 2 ? (
        <Empty what="Scegli almeno due aziende" why="Aggiungile qui sopra, oppure selezionale in Esplora o in Le mie aziende e premi «Confronta»." />
      ) : q.isError ? (
        <Empty what="Confronto non disponibile" why={q.error instanceof ApiError ? q.error.message : String(q.error)} />
      ) : !r ? <Skeleton height={320} /> : (
        <>
          <Card title="Giudizio" right="sintesi dei criteri · non è una raccomandazione">
            <div className="table-wrap">
              <table className="dense compare">
                <thead><tr><th />{r.companies.map((c) => (
                  <th key={c.ticker}><Dot i={order(c.ticker)} /><Link to={`/azienda/${encodeURIComponent(c.ticker)}`}>{c.ticker}</Link></th>
                ))}</tr></thead>
                <tbody>
                  <tr><td>Azienda</td>{r.companies.map((c) => <td key={c.ticker} className="wrap">{c.name}</td>)}</tr>
                  <tr><td>Giudizio</td>{r.companies.map((c) => <td key={c.ticker}>{c.category ? <VerdictBadge category={c.category} label={c.verdict ?? undefined} /> : <span className="na">–</span>}</td>)}</tr>
                  {r.companies[0].pillars.map((p, pi) => (
                    <tr key={p.key}>
                      <td>{p.label}</td>
                      {r.companies.map((c) => {
                        const s = c.pillars[pi];
                        if (s.score === null) return <td key={c.ticker} className="off">dati insufficienti</td>;
                        const tone = s.score >= 0.75 ? "pos" : s.score < 0.25 ? "neg" : "";
                        return (
                          <td key={c.ticker} title={s.grade}>
                            <span className="mini"><span className={`bar ${tone}`}><i style={{ width: `${s.score * 100}%` }} /></span>
                              <span>{fmt(s.score, "score")}</span>{r.companies.length <= 3 && <span className="honest">{s.grade}</span>}</span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  <tr><td>Copertura delle prove</td>{r.companies.map((c) => <td key={c.ticker}>{fmt(c.coverage, "pct", { digits: 0 })}</td>)}</tr>
                  <tr><td>Prezzo (chiusura {fmt(r.as_of, "date_short")})</td>{r.companies.map((c) => <td key={c.ticker}>{fmt(c.price, "price", { currency: c.currency })}</td>)}</tr>
                  <tr><td>Tuo fair value (margine)</td>{r.companies.map((c) => (
                    <td key={c.ticker}>{c.fair_value !== null ? <>{fmt(c.fair_value, "price", { currency: c.currency })} <Num v={c.fair_margin} kind="pct" signed /></> : <span className="na">–</span>}</td>
                  ))}</tr>
                  <tr><td>Tua soglia «compra sotto»</td>{r.companies.map((c) => <td key={c.ticker}>{fmt(c.buy_below, "price", { currency: c.currency })}</td>)}</tr>
                </tbody>
              </table>
            </div>
          </Card>

          <p className="honest" style={{ margin: 0 }}>▲ in grassetto = la migliore della riga (per P/E e simili, la più bassa). Ultimi 12 mesi, prezzi dell'ultima chiusura; importi nella valuta di ogni azienda.</p>
          {r.groups.map((g) => (
            <Card key={g.group} title={g.group}>
              <div className="table-wrap">
                <table className="dense compare">
                  <thead><tr><th>Metrica</th>{r.companies.map((c) => <th key={c.ticker} className="n"><Dot i={order(c.ticker)} />{c.ticker}</th>)}</tr></thead>
                  <tbody>
                    {g.rows.map((row) => (
                      <tr key={row.key}>
                        <td title={row.help || undefined}>{row.label}</td>
                        {r.companies.map((c) => {
                          const best = row.best === c.ticker;
                          const cell = <Num v={row.values[c.ticker]} kind={kindOfMetric(row.fmt)} currency={row.fmt === "money" ? c.currency : undefined} />;
                          return <td key={c.ticker} className="n">{best ? <b title="la migliore della riga">▲ {cell}</b> : cell}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}

          <div className="toolbar">
            <span className="honest">Periodo del prezzo</span>
            <Segmented label="Periodo del prezzo" value={String(years)} onChange={(v) => setParams({ t: tickers.join(","), ...(v !== "5" ? { anni: v } : {}) }, { replace: true })}
              options={r.price_years.map((y) => ({ value: String(y), label: `${y} ${y === 1 ? "anno" : "anni"}` }))} />
          </div>
          <div className="grid-halves">
            {r.charts.map((ch) => {
              const names = Object.keys(ch.series);
              return (
                <Card key={ch.key} title={ch.title} right={ch.note}>
                  {names.length === 0 ? <p className="honest">Dati insufficienti per questo grafico.</p> : (
                    <PeriodChart title={ch.title} type="lines" kind={ch.kind} zero={ch.kind === "pct"}
                      labels={ch.dates.map((d) => fmt(d, "date_short"))} colors={names.map(order)}
                      series={names.map((t) => ({ key: t, label: t, values: ch.series[t] }))} />
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
