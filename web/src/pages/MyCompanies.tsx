// Le mie aziende: per ogni azienda seguita giudizio e pilastri, prezzo, il tuo fair value e la tua soglia con i
// margini, gli avvisi. Sopra, gli avvisi da guardare; se non segui nulla, le prime «Candidate» da cui cominciare.
// Tutto dall'API (servizio watch): qui solo disegno, ordinamento della tabella e azioni (segui, togli, visti).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Num, Segmented, Skeleton, type Tone } from "../components/ui";
import { api, type AlertItem, type CompanyRow, type MyCompanies, type WatchRow } from "../lib/api";
import { fmt, type Kind } from "../lib/fmt";

type Col = { key: keyof WatchRow; label: string; kind?: Kind; change?: boolean; signed?: boolean; help?: string };
const NUMBER_COLS: Col[] = [
  { key: "price", label: "Prezzo", kind: "price" },
  { key: "ret_1m", label: "1 mese", kind: "pct", change: true },
  { key: "ret_1y", label: "1 anno", kind: "pct", change: true },
  { key: "pe", label: "P/E", kind: "mult" },
  { key: "my_fair_value", label: "Tuo FV", kind: "price", help: "Il fair value che hai salvato (scheda → Valutazione)" },
  { key: "my_margin", label: "Margine sul FV", kind: "pct", signed: true, help: "Tuo fair value / prezzo − 1: positivo = il prezzo è sotto il tuo fair value" },
  { key: "buy_below", label: "Compra sotto", kind: "price", help: "La tua soglia d'acquisto (scheda → Soglie)" },
  { key: "buy_margin", label: "Margine sulla soglia", kind: "pct", signed: true, help: "Soglia / prezzo − 1: positivo = il prezzo è già sotto la soglia" },
  { key: "fcf_yield", label: "Rend. FCF", kind: "pct" },
  { key: "roic", label: "ROIC", kind: "pct" },
];
const PILLARS: { key: keyof WatchRow; label: string }[] = [
  { key: "pillar_quality", label: "Qualità" }, { key: "pillar_solidity", label: "Solidità" },
  { key: "pillar_price", label: "Prezzo" }, { key: "pillar_flags", label: "Allarmi" },
];

function MiniBar({ v, label }: { v: number | null; label: string }) {
  if (v === null || v === undefined) return <span className="na" title={`${label}: dati insufficienti`}>–</span>;
  const tone = v >= 0.75 ? "pos" : v < 0.25 ? "neg" : "";
  return (
    <span className="mini" title={`${label}: ${fmt(v, "score")} su 1`}>
      <span className={`bar ${tone}`}><i style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} /></span>
      <span>{fmt(v, "score")}</span>
    </span>
  );
}

function AddCompany({ following }: { following: Set<string> }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const res = useQuery({
    queryKey: ["search", q], enabled: q.trim().length > 0, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=6`),
  });
  const follow = useMutation({
    mutationFn: (t: string) => api(`/watchlist/${encodeURIComponent(t)}`, { method: "PUT" }),
    onSuccess: () => { setQ(""); qc.invalidateQueries({ queryKey: ["my-companies"] }); },
  });
  const results = q.trim() ? res.data?.results ?? [] : [];
  return (
    <div className="add-company">
      <div className="search">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Aggiungi un'azienda: ticker o nome"
          aria-label="Aggiungi un'azienda" style={{ paddingLeft: 12 }} />
      </div>
      {results.length > 0 && (
        <ul className="pick">
          {results.map((r) => (
            <li key={r.ticker}>
              <span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
              {following.has(r.ticker) ? <span className="honest">già seguita</span> : (
                <button type="button" className="btn" disabled={follow.isPending} onClick={() => follow.mutate(r.ticker)}>☆ Segui</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Alerts({ alerts, rows }: { alerts: AlertItem[]; rows: WatchRow[] }) {
  const qc = useQueryClient();
  const seen = useMutation({
    mutationFn: (ids: number[]) => api("/alerts/seen", { method: "POST", body: JSON.stringify({ ids }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-companies"] }),
  });
  const unseen = alerts.filter((a) => a.unseen && a.id !== null).map((a) => a.id as number);
  const verdicts = Object.fromEntries(rows.map((r) => [r.ticker, r]));
  let last = "";
  return (
    <Card title="Da guardare" right={alerts.length ? `${alerts.length} avvisi${unseen.length ? ` · ${unseen.length} non visti` : ""}` : undefined}>
      {alerts.length === 0 ? (
        <p className="honest" style={{ margin: 0 }}>Nessun avviso: niente bilanci nuovi, movimenti forti, prezzi sotto le tue soglie o vicini al tuo fair value.</p>
      ) : (
        <div className="alerts">
          {alerts.map((a, i) => {
            const first = a.ticker !== last;
            last = a.ticker;
            const r = verdicts[a.ticker];
            return (
              <div key={`${a.ticker}-${a.code}-${i}`} className={`alert${a.unseen ? " new" : ""}${first && i ? " first" : ""}`}>
                <span className="dot" title={a.unseen ? "non visto" : undefined}>{a.unseen ? "●" : ""}</span>
                <span className="tk">{first && <Link to={`/azienda/${encodeURIComponent(a.ticker)}`}>{a.ticker}</Link>}</span>
                <span className="vb">{first && r?.category && <VerdictBadge category={r.category} label={r.verdict ?? undefined} />}</span>
                <Badge tone={(a.tone as Tone) || "neutral"}>{a.code}</Badge>
                <span className="msg" title={a.message}>{a.message}</span>
                <span className="age">{a.computed ? "adesso" : a.created_at ? fmt(a.created_at, "date_short") : ""}</span>
              </div>
            );
          })}
        </div>
      )}
      {unseen.length > 0 && (
        <div className="actions">
          <button type="button" className="btn" disabled={seen.isPending} onClick={() => seen.mutate(unseen)}>Segna come visti ({unseen.length})</button>
        </div>
      )}
    </Card>
  );
}

function History() {
  const [state, setState] = useState<"attivi" | "non_visti" | "chiusi" | "tutti">("attivi");
  const q = useQuery({ queryKey: ["alerts", state], queryFn: () => api<{ alerts: AlertItem[] }>(`/alerts?state=${state}`) });
  return (
    <details className="card details">
      <summary>Storico degli avvisi</summary>
      <Segmented label="Stato degli avvisi" value={state} onChange={setState}
        options={[{ value: "attivi", label: "Attivi" }, { value: "non_visti", label: "Non visti" }, { value: "chiusi", label: "Chiusi" }, { value: "tutti", label: "Tutti" }]} />
      {q.isPending ? <Skeleton height={80} /> : (q.data?.alerts.length ?? 0) === 0 ? (
        <p className="honest">Nessun avviso in questo stato.</p>
      ) : (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="dense">
            <thead><tr><th>Data</th><th>Ticker</th><th>Codice</th><th>Messaggio</th><th>Stato</th><th>Visto</th></tr></thead>
            <tbody>
              {q.data!.alerts.map((a) => (
                <tr key={a.id ?? `${a.ticker}-${a.code}`}>
                  <td>{fmt(a.created_at, "date_short")}</td>
                  <td><Link to={`/azienda/${encodeURIComponent(a.ticker)}`}><b>{a.ticker}</b></Link></td>
                  <td><Badge tone={(a.tone as Tone) || "neutral"}>{a.code}</Badge></td>
                  <td className="wrap">{a.message}</td>
                  <td>{a.cleared_at ? `chiuso il ${fmt(a.cleared_at, "date_short")}` : "attivo"}</td>
                  <td>{a.seen_at ? `sì, ${fmt(a.seen_at, "date_short")}` : <span className="info">● no</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

export function MyCompaniesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["my-companies"], queryFn: () => api<MyCompanies>("/my-companies") });
  const [sort, setSort] = useState<{ key: keyof WatchRow | "verdict_rank"; dir: 1 | -1 }>({ key: "verdict_rank", dir: 1 });
  const follow = useMutation({
    mutationFn: ({ t, on }: { t: string; on: boolean }) => api(`/watchlist/${encodeURIComponent(t)}`, { method: on ? "PUT" : "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-companies"] }),
  });
  const rows = useMemo(() => {
    const list = [...(q.data?.rows ?? [])];
    if (sort.key === "verdict_rank") return sort.dir === 1 ? list : list.reverse(); // già in ordine di giudizio
    const k = sort.key;
    return list.sort((a, b) => {
      const x = a[k] as number | string | null;
      const y = b[k] as number | string | null;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [q.data, sort]);
  const th = (key: keyof WatchRow | "verdict_rank", label: string, num = false, help?: string) => {
    const active = sort.key === key;
    return (
      <th className={num ? "n" : undefined} title={help} aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
        <button type="button" className="sort" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : (num ? -1 : 1) }))}>
          {label}{active ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
        </button>
      </th>
    );
  };
  const following = new Set((q.data?.rows ?? []).map((r) => r.ticker));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Le mie aziende</h1>
          {q.data && q.data.count > 0 && <p className="lead">{q.data.count} {q.data.count === 1 ? "azienda" : "aziende"} · prezzi al {fmt(q.data.as_of, "date")}</p>}
        </div>
        <AddCompany following={following} />
      </div>
      {q.isPending ? <Skeleton height={260} /> : q.isError ? <Empty what="Pagina non disponibile" why={q.error.message} /> : q.data.count === 0 ? (
        <>
          <Empty what="Non segui ancora nessuna azienda"
            why="Qui trovi, per ogni azienda che segui, il giudizio sintetico, il prezzo, il margine sul tuo fair value e sulla tua soglia d'acquisto, e gli avvisi (nuovi bilanci, prezzo sotto la soglia, movimenti forti, margini in calo)." />
          {q.data.suggestions.length > 0 && (
            <Card title="Per cominciare" right="le prime «Candidate» dell'S&P 500 per punteggio">
              <p className="honest" style={{ marginTop: 0 }}>Il giudizio è una sintesi di criteri fissi, non una raccomandazione.</p>
              <div className="suggest">
                {q.data.suggestions.map((s) => (
                  <div key={s.ticker} className="suggest-item">
                    <Link to={`/azienda/${encodeURIComponent(s.ticker)}`}><b>{s.ticker}</b></Link> <span className="honest">{s.name}</span>
                    <button type="button" className="btn" disabled={follow.isPending} onClick={() => follow.mutate({ t: s.ticker, on: true })}>☆ Segui</button>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </>
      ) : (
        <>
          <Alerts alerts={q.data.alerts} rows={q.data.rows} />
          <Card title="Le tue aziende" right={rows.length >= 2 ? <Link to={`/confronta?t=${rows.slice(0, 5).map((r) => r.ticker).join(",")}`}>Confronta {rows.length > 5 ? "le prime 5" : "tutte"} →</Link> : "clic sulle intestazioni per ordinare"}>
            <div className="table-wrap">
              <table className="dense watch">
                <thead>
                  <tr>
                    {th("ticker", "Ticker")}{th("name", "Azienda")}{th("verdict_rank", "Giudizio", false, "Sintesi dei criteri: non è una raccomandazione")}
                    {PILLARS.map((p) => th(p.key, p.label, true))}
                    {NUMBER_COLS.map((c) => th(c.key, c.label, true, c.help))}
                    {th("alert_count", "Avvisi", true)}<th aria-label="Azioni" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.ticker}>
                      <td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td>
                      <td className="nm">{r.name}</td>
                      <td>{r.category ? <VerdictBadge category={r.category} label={r.verdict ?? undefined} /> : <span className="na">–</span>}</td>
                      {PILLARS.map((p) => <td key={p.key} className="n"><MiniBar v={r[p.key] as number | null} label={p.label} /></td>)}
                      {NUMBER_COLS.map((c) => (
                        <td key={c.key} className="n">
                          <Num v={r[c.key]} kind={c.kind} change={c.change} signed={c.signed}
                            currency={c.kind === "price" && r.currency !== "USD" ? r.currency : undefined} />
                        </td>
                      ))}
                      <td className="n">{r.alert_count ? <Badge tone="warn">{r.alert_count}</Badge> : <span className="na">–</span>}</td>
                      <td className="n">
                        <button type="button" className="btn ghost" title={`Togli ${r.ticker} dalle tue aziende`} aria-label={`Togli ${r.ticker}`}
                          disabled={follow.isPending} onClick={() => follow.mutate({ t: r.ticker, on: false })}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="honest">Prezzi {rows.some((r) => r.currency !== "USD") ? "nella valuta di ogni azienda" : "in dollari"}. Il giudizio si legge nella scheda, con le prove una per una.</p>
          </Card>
          <History />
        </>
      )}
    </div>
  );
}
