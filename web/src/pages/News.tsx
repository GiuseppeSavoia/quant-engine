// Notizie: flusso dei feed RSS (dalla copia salvata: i feed li legge la webapp classica, l'API non scarica nulla),
// account X scelti dall'utente con il riquadro ufficiale di X, 8-K depositati alla SEC. Filtri e dati nel motore
// (servizio news, modules/news.py): qui solo controlli ed elenchi.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Card, Empty, Notice, Segmented, Skeleton } from "../components/ui";
import { API, api, type FilingsPage, type NewsStream, type XAccounts } from "../lib/api";
import { fmt } from "../lib/fmt";
import { LANG, t, tn } from "../lib/i18n";

type Section = "flusso" | "x" | "8k";
type View = "all" | "mine" | "ticker";

function when(iso: string | null): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return fmt(iso, "date_short");
  const hm = d.toLocaleTimeString(LANG === "en" ? "en-US" : "it-IT", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return hm;
  return `${d.toLocaleDateString(LANG === "en" ? "en-GB" : "it-IT", LANG === "en" ? { day: "numeric", month: "short" } : { day: "2-digit", month: "2-digit" })} ${hm}`;
}

function qs(p: Record<string, string | string[] | boolean | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === "" || v === false) continue;
    for (const x of Array.isArray(v) ? v : [v]) u.append(k, String(x));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

function TickerLinks({ tickers }: { tickers: string[] }) {
  return <>{tickers.map((t, i) => <span key={t}>{i > 0 && " "}<Link to={`/azienda/${encodeURIComponent(t)}`}>{t}</Link></span>)}</>;
}

function Stream() {
  const [view, setView] = useState<View>("all");
  const [ticker, setTicker] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [lang, setLang] = useState("");
  const [query, setQuery] = useState("");
  // evidenza: le notizie arrivate dopo il giro precedente visto in questa pagina (alla prima apertura nessuna)
  const marks = useRef<{ mark: string | null; seen: string | null }>({ mark: null, seen: null });
  const q = useQuery({
    queryKey: ["news", view, ticker.trim().toUpperCase(), groups, lang, query.trim()],
    queryFn: () => api<NewsStream>(`/news${qs({ view, ticker: view === "ticker" ? ticker.trim() : undefined, group: groups, lang, q: query.trim() })}`),
    refetchInterval: 30_000, placeholderData: (prev) => prev,
  });
  if (q.isPending) return <Skeleton height={320} />;
  if (q.isError) return <Empty what={t("Notizie non disponibili")} why={q.error.message} />;
  const n = q.data;
  const newest = n.items.reduce<string | null>((m, i) => (m === null || i.first_seen > m ? i.first_seen : m), null);
  const mk = marks.current;
  if (mk.seen === null) mk.mark = newest;
  else if (newest !== null && newest > mk.seen) mk.mark = mk.seen;
  if (newest !== null && (mk.seen === null || newest > mk.seen)) mk.seen = newest;
  const isNew = (s: string) => mk.mark !== null && s > mk.mark;
  const nNew = n.items.filter((i) => isNew(i.first_seen)).length;
  const old = n.saved_at ? (Date.now() - new Date(n.saved_at).getTime()) / 60_000 : null;
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <Segmented label={t("Quali notizie")} value={view} onChange={setView}
          options={[{ value: "all", label: t("Tutte") }, { value: "mine", label: t("Le mie aziende") }, { value: "ticker", label: t("Un ticker") }]} />
        {view === "ticker" && <input className="text-input mono" style={{ width: 110 }} value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder={t("ticker")} aria-label={t("Ticker")} />}
        <details className="dropdown">
          <summary className="btn">{groups.length ? t("Fonti: {names}", { names: groups.map((g) => t(g)).join(", ") }) : t("Tutte le fonti")}</summary>
          <div className="menu">
            {n.groups.map((g) => (
              <label key={g} className="check"><input type="checkbox" checked={groups.includes(g)}
                onChange={(e) => setGroups(e.target.checked ? [...groups, g] : groups.filter((x) => x !== g))} />{t(g)}</label>
            ))}
            {groups.length > 0 && <button type="button" className="btn ghost" onClick={() => setGroups([])}>{t("Tutte le fonti")}</button>}
          </div>
        </details>
        <select className="select" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t("Lingua")}>
          <option value="">EN + IT</option><option value="en">{t("Inglese")}</option><option value="it">{t("Italiano")}</option>
        </select>
        <input className="text-input grow" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Cerca nel titolo")} aria-label={t("Cerca nel titolo")} />
      </div>
      <p className="honest">
        {n.saved_at ? t("Copia salvata alle {time}", { time: when(n.saved_at) }) : t("Nessuna copia salvata")} · {tn(n.matched, "{n} notizia", "{n} notizie", { n: fmt(n.matched, "int") })}
        {nNew > 0 && <> · <span className="accent">● {tn(nNew, "{n} nuova", "{n} nuove")}</span></>}
        {view === "mine" && n.followed.length === 0 && ` · ${t("nessuna azienda tua: seguine una o aggiungila al portafoglio")}`}
      </p>
      {!n.enabled ? (
        <Empty what={t("Notizie spente")} why={t("Nella configurazione: news.enabled è false.")} />
      ) : n.saved_at === null ? (
        <Empty what={t("Ancora nessuna notizia")} why={t("I feed si leggono mentre è aperta l'app classica: le prime notizie arrivano entro un paio di minuti.")} />
      ) : n.items.length === 0 ? (
        <Empty what={t("Nessuna notizia con questi filtri")} why={t("Sono tenute le notizie degli ultimi {n} giorni.", { n: fmt(n.keep_days, "int") })} />
      ) : (
        <>
          {old !== null && old > 30 && <Notice tone="warn">{t("La copia ha {age}: i feed si leggono solo mentre è aperta l'app classica.", { age: old > 120 ? t("{n} ore", { n: fmt(old / 60, "int") }) : t("{n} minuti", { n: fmt(old, "int") }) })}</Notice>}
          <div className="table-wrap">
            <table className="dense news-table">
              <thead><tr><th aria-label={t("Nuova")} /><th>{t("Ora")}</th><th>{t("Fonte")}</th><th>{t("Ticker")}</th><th>{t("Titolo della notizia")}</th></tr></thead>
              <tbody>
                {n.items.map((i) => (
                  <tr key={i.link} className={isNew(i.first_seen) ? "is-new" : undefined}>
                    <td className="dot">{isNew(i.first_seen) ? "●" : ""}</td>
                    <td>{when(i.published)}</td>
                    <td className="clip src" title={i.feed}>{i.source}</td>
                    <td className="tk"><TickerLinks tickers={i.tickers} /></td>
                    <td className="title"><a href={i.link} target="_blank" rel="noopener noreferrer" title={i.title}>{i.title}</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {n.matched > n.items.length && <p className="honest">{t("Mostrate le {n} più recenti su {total}: restringi con i filtri.", { n: fmt(n.items.length, "int"), total: fmt(n.matched, "int") })}</p>}
        </>
      )}
    </>
  );
}

function Filings() {
  const [days, setDays] = useState(7);
  const [mine, setMine] = useState(false);
  const [important, setImportant] = useState(false);
  const [codes, setCodes] = useState<string[]>([]);
  const q = useQuery({
    queryKey: ["news-8k", days, mine, important, codes],
    queryFn: () => api<FilingsPage>(`/news/filings${qs({ days, mine, important, item: codes })}`),
    placeholderData: (prev) => prev,
  });
  if (q.isPending) return <Skeleton height={320} />;
  if (q.isError) return <Empty what={t("Documenti non disponibili")} why={q.error.message} />;
  const f = q.data;
  return (
    <>
      <p className="honest" style={{ marginTop: 0 }}>{t("Le notizie ufficiali: gli 8-K che le aziende devono depositare alla SEC entro 4 giorni da ogni evento rilevante (risultati, acquisizioni, dirigenti, debito, revisore…). L'ora è quella di accettazione della SEC. ⚠ = da guardare subito.")}</p>
      <div className="row" style={{ marginBottom: 12 }}>
        <select className="select" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("Periodo")}>
          {[1, 3, 7, 14, 30].map((x) => <option key={x} value={x}>{x === 1 ? t("ultimo giorno") : t("ultimi {n} giorni", { n: x })}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />{t("Solo le mie aziende")}</label>
        <label className="check"><input type="checkbox" checked={important} onChange={(e) => setImportant(e.target.checked)} />{t("Solo ⚠")}</label>
        <details className="dropdown">
          <summary className="btn">{codes.length ? t("Tipo di evento: {n}", { n: codes.length }) : t("Tutti i tipi di evento")}</summary>
          <div className="menu wide">
            {f.item_labels.map((l) => (
              <label key={l.code} className="check"><input type="checkbox" checked={codes.includes(l.code)}
                onChange={(e) => setCodes(e.target.checked ? [...codes, l.code] : codes.filter((x) => x !== l.code))} /><span><b>{l.code}</b> {l.label}</span></label>
            ))}
            {codes.length > 0 && <button type="button" className="btn ghost" onClick={() => setCodes([])}>{t("Tutti i tipi")}</button>}
          </div>
        </details>
      </div>
      <p className="honest">{t("{n} depositi di {companies} aziende", { n: fmt(f.items.length, "int"), companies: fmt(f.companies, "int") })}{mine && f.followed.length === 0 ? ` · ${t("nessuna azienda tua: seguine una o aggiungila al portafoglio")}` : ""}.</p>
      {f.items.length === 0 ? (
        <Empty what={t("Nessun 8-K nel periodo")} why={t("Si scaricano con l'aggiornamento dei dati (anche quello automatico serale).")} />
      ) : (
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>{t("Quando")}</th><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th>{t("Cosa")}</th><th aria-label={t("Documento")} /></tr></thead>
            <tbody>
              {f.items.map((r, k) => (
                <tr key={`${r.ticker}-${r.when}-${k}`} className={r.important ? "warn-row" : undefined}>
                  <td>{when(r.when)}</td>
                  <td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td>
                  <td className="clip">{r.name}</td>
                  <td className="wrap">{r.important && <Badge tone="neg">⚠</Badge>} {r.what}</td>
                  <td className="n">{r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer">{"SEC ↗"}</a> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function prefersDark(): boolean {
  const t = document.documentElement.getAttribute("data-theme");
  if (t) return t === "dark";
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

function XSection() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["news-x"], queryFn: () => api<XAccounts>("/news/x") });
  const add = useMutation({
    mutationFn: () => api<{ accounts: string[] }>("/news/x", { method: "POST", body: JSON.stringify({ account: text }) }),
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["news-x"] }); },
  });
  const del = useMutation({
    mutationFn: (u: string) => api(`/news/x/${encodeURIComponent(u)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news-x"] }),
  });
  if (q.isPending) return <Skeleton height={200} />;
  if (q.isError) return <Empty what={t("Account X non disponibili")} why={q.error.message} />;
  const dark = prefersDark();
  return (
    <>
      <p className="honest" style={{ marginTop: 0 }}>{t("Account X scelti da te, con il riquadro ufficiale di X (gratis, nessuna API, nessun post raccolto o salvato dall'app). Non è in tempo reale (si aggiorna ricaricando la pagina); X può chiedere di essere collegati nel browser e mostrare solo una parte dei post, o nessuno: in quel caso usa il link al profilo.")}</p>
      <form className="row" style={{ marginBottom: 14 }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) add.mutate(); }}>
        <input className="text-input grow" value={text} onChange={(e) => setText(e.target.value)} placeholder={t("@nome, nome o link x.com/nome")} aria-label={t("Aggiungi un account X")} />
        <button type="submit" className="btn primary" disabled={!text.trim() || add.isPending}>{t("Aggiungi")}</button>
        {add.isError && <span className="neg">{add.error.message}</span>}
      </form>
      {q.data.accounts.length === 0 ? (
        <Empty what={t("Nessun account")} why={t("Aggiungine uno qui sopra: l'elenco resta nelle tue preferenze.")} />
      ) : (
        <div className="grid-2">
          {q.data.accounts.map((u) => (
            <Card key={u} title={`@${u}`} right={<span className="row">
              <a href={`https://x.com/${encodeURIComponent(u)}`} target="_blank" rel="noopener noreferrer">{t("apri il profilo")} ↗</a>
              <button type="button" className="btn ghost" onClick={() => del.mutate(u)} aria-label={t("Togli @{account}", { account: u })}>{t("Togli")}</button></span>}>
              <iframe className="x-embed" title={t("Post di @{account} su X", { account: u })} loading="lazy" height={q.data.height + 10}
                src={`${API}/news/x/${encodeURIComponent(u)}/embed?dark=${dark}`}
                sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" />
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

export function NewsPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("sez");
  const section: Section = raw === "x" || raw === "8k" ? raw : "flusso";
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{t("Notizie")}</h1>
          <p className="lead">{t("Titoli da agenzie, giornali, comunicati e banche centrali, i tuoi account X e i documenti ufficiali depositati alla SEC. Solo titolo e link all'originale: non è un flusso professionale (ritardo tipico da 1 a 15 minuti).")}</p></div>
      </div>
      <div style={{ marginBottom: 16 }}>
        <Segmented label={t("Sezione")} value={section} onChange={(v) => setParams(v === "flusso" ? {} : { sez: v }, { replace: true })}
          options={[{ value: "flusso", label: t("Flusso") }, { value: "x", label: "X" }, { value: "8k", label: t("Documenti ufficiali (8-K)") }]} />
      </div>
      <Card>
        {section === "flusso" ? <Stream /> : section === "x" ? <XSection /> : <Filings />}
      </Card>
    </div>
  );
}
