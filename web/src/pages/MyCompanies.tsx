// Le mie aziende: per ogni azienda seguita giudizio e pilastri, prezzo, il tuo fair value e la tua soglia con i
// margini, gli avvisi. Sopra, gli avvisi da guardare; se non segui nulla, le prime «Candidate» da cui cominciare.
// Tutto dall'API (servizio watch): qui solo disegno, ordinamento della tabella e azioni (segui, togli, visti).
import { GetStarted } from "../components/Onboarding";
import { Spark } from "../components/Spark";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Num, Segmented, Skeleton, type Tone } from "../components/ui";
import { api, type AlertItem, type CompanyRow, type MyCompanies, type WatchRow } from "../lib/api";
import { fmt, type Kind } from "../lib/fmt";
import { t, tn } from "../lib/i18n";

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
  if (v === null || v === undefined) return <span className="na" title={t("{label}: dati insufficienti", { label })}>–</span>;
  const tone = v >= 0.75 ? "pos" : v < 0.25 ? "neg" : "";
  return (
    <span className="mini" title={t("{label}: {v} su 1", { label, v: fmt(v, "score") })}>
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
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Aggiungi un'azienda: ticker o nome")}
          aria-label={t("Aggiungi un'azienda")} style={{ paddingLeft: 12 }} />
      </div>
      {results.length > 0 && (
        <ul className="pick">
          {results.map((r) => (
            <li key={r.ticker}>
              <span><b>{r.ticker}</b> <span className="honest">{r.name}</span></span>
              {following.has(r.ticker) ? <span className="honest">{t("già seguita")}</span> : (
                <button type="button" className="btn" disabled={follow.isPending} onClick={() => follow.mutate(r.ticker)}>☆ {t("Segui")}</button>
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
    <Card title={t("Da guardare")} right={alerts.length ? `${tn(alerts.length, "{n} avviso", "{n} avvisi")}${unseen.length ? ` · ${t("{n} non visti", { n: unseen.length })}` : ""}` : undefined}>
      {alerts.length === 0 ? (
        <p className="honest" style={{ margin: 0 }}>{t("Nessun avviso: niente bilanci nuovi, movimenti forti, prezzi sotto le tue soglie o vicini al tuo fair value.")}</p>
      ) : (
        <div className="alerts">
          {alerts.map((a, i) => {
            const first = a.ticker !== last;
            last = a.ticker;
            const r = verdicts[a.ticker];
            return (
              <div key={`${a.ticker}-${a.code}-${i}`} className={`alert${a.unseen ? " new" : ""}${first && i ? " first" : ""}`}>
                <span className="dot" title={a.unseen ? t("non visto") : undefined}>{a.unseen ? "●" : ""}</span>
                <span className="tk">{first && <Link to={`/azienda/${encodeURIComponent(a.ticker)}`}>{a.ticker}</Link>}</span>
                <span className="vb">{first && r?.category && <VerdictBadge category={r.category} label={r.verdict ?? undefined} />}</span>
                <Badge tone={(a.tone as Tone) || "neutral"}>{a.code}</Badge>
                <span className="msg" title={a.message}>{a.message}</span>
                <span className="age">{a.computed ? t("adesso") : a.created_at ? fmt(a.created_at, "date_short") : ""}</span>
              </div>
            );
          })}
        </div>
      )}
      {unseen.length > 0 && (
        <div className="actions">
          <button type="button" className="btn" disabled={seen.isPending} onClick={() => seen.mutate(unseen)}>{t("Segna come visti ({n})", { n: unseen.length })}</button>
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
      <summary>{t("Storico degli avvisi")}</summary>
      <Segmented label={t("Stato degli avvisi")} value={state} onChange={setState}
        options={[{ value: "attivi", label: t("Attivi") }, { value: "non_visti", label: t("Non visti") }, { value: "chiusi", label: t("Chiusi") }, { value: "tutti", label: t("Tutti") }]} />
      {q.isPending ? <Skeleton height={80} /> : (q.data?.alerts.length ?? 0) === 0 ? (
        <p className="honest">{t("Nessun avviso in questo stato.")}</p>
      ) : (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="dense">
            <thead><tr><th>{t("Data")}</th><th>{t("Ticker")}</th><th>{t("Codice")}</th><th>{t("Messaggio")}</th><th>{t("Stato")}</th><th>{t("Visto")}</th></tr></thead>
            <tbody>
              {q.data!.alerts.map((a) => (
                <tr key={a.id ?? `${a.ticker}-${a.code}`}>
                  <td>{fmt(a.created_at, "date_short")}</td>
                  <td><Link to={`/azienda/${encodeURIComponent(a.ticker)}`}><b>{a.ticker}</b></Link></td>
                  <td><Badge tone={(a.tone as Tone) || "neutral"}>{a.code}</Badge></td>
                  <td className="wrap">{a.message}</td>
                  <td>{a.cleared_at ? t("chiuso il {date}", { date: fmt(a.cleared_at, "date_short") }) : t("attivo")}</td>
                  <td>{a.seen_at ? t("sì, {date}", { date: fmt(a.seen_at, "date_short") }) : <span className="info">● {t("no")}</span>}</td>
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
          <h1>{t("Le mie aziende")}</h1>
          {q.data && q.data.count > 0 && <p className="lead">{tn(q.data.count, "{n} azienda", "{n} aziende")} · {t("prezzi al {date}", { date: fmt(q.data.as_of, "date") })}</p>}
        </div>
        <AddCompany following={following} />
      </div>
      {q.isPending ? <Skeleton height={260} /> : q.isError ? <Empty what={t("Pagina non disponibile")} why={q.error.message} /> : q.data.count === 0 ? (
        <Card title={t("Per cominciare")}><GetStarted /></Card>
      ) : (
        <>
          <Alerts alerts={q.data.alerts} rows={q.data.rows} />
          <Card title={t("Le tue aziende")} right={rows.length >= 2 ? <Link to={`/confronta?t=${rows.slice(0, 5).map((r) => r.ticker).join(",")}`}>{rows.length > 5 ? t("Confronta le prime 5") : t("Confronta tutte")} →</Link> : t("clic sulle intestazioni per ordinare")}>
            <div className="table-wrap">
              <table className="dense watch">
                <thead>
                  <tr>
                    {th("ticker", t("Ticker"))}{th("name", t("Azienda"))}<th>{t("1 anno")}</th>{th("verdict_rank", t("Giudizio"), false, t("Sintesi dei criteri fissi della scheda"))}
                    {PILLARS.map((p) => th(p.key, t(p.label), true))}
                    {NUMBER_COLS.map((c) => th(c.key, t(c.label), true, c.help ? t(c.help) : undefined))}
                    {th("alert_count", t("Avvisi"), true)}<th aria-label={t("Azioni sulla riga")} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.ticker}>
                      <td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td>
                      <td className="nm">{r.name}</td>
                      <td className="spark-cell"><Spark values={r.spark} /></td>
                      <td>{r.category ? <VerdictBadge category={r.category} label={r.verdict ?? undefined} /> : <span className="na">–</span>}</td>
                      {PILLARS.map((p) => <td key={p.key} className="n"><MiniBar v={r[p.key] as number | null} label={t(p.label)} /></td>)}
                      {NUMBER_COLS.map((c) => (
                        <td key={c.key} className="n">
                          <Num v={r[c.key]} kind={c.kind} change={c.change} signed={c.signed}
                            currency={c.kind === "price" && r.currency !== "USD" ? r.currency : undefined} />
                        </td>
                      ))}
                      <td className="n">{r.alert_count ? <Badge tone="warn">{r.alert_count}</Badge> : <span className="na">–</span>}</td>
                      <td className="n">
                        <button type="button" className="btn ghost" title={t("Togli {ticker} dalle tue aziende", { ticker: r.ticker })} aria-label={t("Togli {ticker}", { ticker: r.ticker })}
                          disabled={follow.isPending} onClick={() => follow.mutate({ t: r.ticker, on: false })}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="honest">{rows.some((r) => r.currency !== "USD") ? t("Prezzi nella valuta di ogni azienda.") : t("Prezzi in dollari.")} {t("Il giudizio si legge nella scheda, con le prove una per una.")}</p>
          </Card>
          <History />
        </>
      )}
    </div>
  );
}
