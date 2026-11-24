// Strategie a regole: catalogo standard, grandi investitori, le mie. Per ognuna: regole in chiaro, cautele, backtest
// sulle due metà (in coda sul server: risponde subito e la pagina segue lo stato), valore nel tempo, anni, cosa terrebbe
// oggi, confronto di tutte; costruttore senza codice. Calcoli e controlli nel motore (servizio strategies): qui solo
// controlli, tabelle e grafici.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PeriodChart } from "../components/charts";
import { draftsFrom, groupMetrics, parseDrafts, RuleRows, type RuleDraft } from "../components/rules";
import { Badge, Card, Empty, Notice, Num, Segmented, Skeleton } from "../components/ui";
import {
  api, type ExploreCatalog, type Job, type StrategiesOverview, type StrategyBuilder, type StrategyDefinition,
  type StrategyHoldings, type StrategyItem, type StrategyPage, type StrategyPreview,
} from "../lib/api";
import { fmt } from "../lib/fmt";
import { LANG, t, tn } from "../lib/i18n";

const GROUP_ORDER = ["Di base", "Grandi investitori", "Mie"];
const active = (j: Job | null | undefined) => !!j && (j.state === "queued" || j.state === "running");

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

function JobLine({ job }: { job: Job }) {
  if (job.state === "error") return <Notice tone="neg" title={t("Backtest non riuscito:")}>{job.error}</Notice>;
  const text = job.state === "running"
    ? t("Backtest in corso dalle {time}: di solito 1-3 minuti con i dati veri. Puoi cambiare pagina, continua sul server.",
      { time: new Date(job.started_at ?? job.queued_at).toLocaleTimeString(LANG === "en" ? "en-US" : "it-IT", { hour: "2-digit", minute: "2-digit" }) })
    : t("Backtest in coda (posizione {n}): parte appena finisce quello prima.", { n: job.position });
  return <Notice tone="info" title={job.state === "running" ? t("In corso:") : t("In coda:")}><span className="spinner" aria-hidden="true" /> {text}</Notice>;
}

// ---------------------------------------------------------------- risultati
function Report({ page }: { page: StrategyPage }) {
  const rep = page.report!;
  const labels = { strategy: t(page.name), baseline: t(page.baseline_name), benchmark: rep.benchmark } as Record<string, string>;
  const dates = rep.curves.dates.map((d) => fmt(d, "date_short"));
  const mid = rep.curves.mid ? rep.curves.dates.findIndex((d) => d >= rep.curves.mid!) : -1;
  const s = rep.summary;
  return (
    <>
      {s && (
        <Notice tone={s.better ? "pos" : "neutral"} title={t("In sintesi:")}>
          {s.better ? t("nella seconda metà la strategia batte l'indice a pesi uguali sia nel rendimento sia nel rapporto rendimento/rischio") : t("nella seconda metà la strategia non batte in modo netto l'indice a pesi uguali")}
          {" "}{t("({cagr} l'anno contro {base}; Sharpe {sharpe} contro {bsharpe}).", { cagr: fmt(s.cagr, "pct", { signed: true }), base: fmt(s.baseline_cagr, "pct", { signed: true }), sharpe: fmt(s.sharpe, "num"), bsharpe: fmt(s.baseline_sharpe, "num") })}
          {s.bench_cagr !== null && ` ${t("{bench}: {cagr} l'anno.", { bench: rep.benchmark, cagr: fmt(s.bench_cagr, "pct", { signed: true }) })}`}
        </Notice>
      )}
      <Card title={t("Rendimento e rischio per periodo")} right={t("stessa curva divisa in due metà")}>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>{t("Periodo")}</th><th>{t("Cosa")}</th><th className="n">{t("Rendimento")}</th><th className="n">{t("All'anno")}</th><th className="n">{t("Volatilità")}</th><th className="n">Sharpe</th><th className="n">{t("Perdita massima")}</th></tr></thead>
            <tbody>
              {rep.periods.map((r) => (
                <tr key={`${r.period}-${r.what}`} className={r.what === "Strategia" ? "me" : undefined}>
                  <td>{r.period}</td><td>{r.what === "Strategia" ? <b>{t(page.name)}</b> : t(r.what)}</td>
                  <td className="n"><Num v={r.total_return} kind="pct" change /></td>
                  <td className="n"><Num v={r.cagr} kind="pct" change /></td>
                  <td className="n">{fmt(r.annual_volatility, "pct")}</td>
                  <td className="n">{fmt(r.sharpe_ratio, "num")}</td>
                  <td className="n">{fmt(r.max_drawdown, "pct")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="grid-2">
        <Card title={t("Valore nel tempo")} right={t("1 = inizio · scala logaritmica")}>
          <PeriodChart type="lines" kind="num" log zero={false} title={t("Valore nel tempo")} labels={dates} height={280}
            marker={mid >= 0 ? mid : undefined}
            series={rep.curves.series.map((x) => ({ key: x.key, label: labels[x.key] ?? x.key, values: x.values }))} />
          <p className="honest">{t("La linea tratteggiata divide le due metà. Se hai scelto i parametri guardando la prima, la seconda è la prova più onesta.")}</p>
        </Card>
        <Card title={t("Anno per anno")} right={t("il primo e l'ultimo anno possono essere parziali")}>
          <PeriodChart type="bars" kind="pct" title={t("Rendimento per anno")} labels={rep.years.map((y) => String(y.year))} height={280}
            series={[
              { key: "strategy", label: t(page.name), values: rep.years.map((y) => y.strategy) },
              { key: "baseline", label: t(page.baseline_name), values: rep.years.map((y) => y.baseline) },
              { key: "benchmark", label: rep.benchmark, values: rep.years.map((y) => y.benchmark) },
            ]} />
        </Card>
      </div>
      <Card>
        <dl className="kv big">
          <div><dt>{t("Rotazione annua")}</dt><dd>{fmt(rep.facts.turnover_annual, "mult")}</dd></div>
          <div><dt>{t("Costi totali")}</dt><dd>{fmt(rep.facts.total_costs, "money", { currency: "USD" })}</dd></div>
          <div><dt>{t("Esposizione media")}</dt><dd>{fmt(rep.facts.avg_exposure, "pct")}</dd></div>
          <div><dt>{t("Copertura dei dati")}</dt><dd>{fmt(rep.facts.avg_coverage, "pct")}</dd></div>
        </dl>
        <p className="honest">{t("Rotazione: quante volte all'anno si ricompra l'intero portafoglio (più è alta, più costi). Costi: commissioni e slippage su 100.000 iniziali. Copertura: quota dei membri dell'indice con prezzi (gli ex membri delistati mancano: survivorship bias).")}</p>
        <Notice tone="info" title={t("Cautela:")}>{t("Configurazioni diverse provate finora sugli stessi dati (Laboratorio e Strategie): {n}. Più se ne provano, più la migliore è anche la più fortunata.", { n: fmt(rep.variants, "int") })}</Notice>
        {rep.significance && <Notice tone={(rep.significance.dsr ?? 0) >= 0.95 ? "pos" : "neutral"} title={t("Sharpe corretto per i tentativi:")}>{rep.significance.text}</Notice>}
      </Card>
    </>
  );
}

function Holdings({ name }: { name: string }) {
  const q = useQuery({ queryKey: ["strategy-holdings", name], queryFn: () => api<StrategyHoldings>(`/strategies/holdings?name=${encodeURIComponent(name)}`) });
  const h = q.data;
  return (
    <Card title={t("Cosa terrebbe oggi")} right={h && h.rows.length ? tn(h.rows.length, "{n} titolo · investito {inv}", "{n} titoli · investito {inv}", { n: fmt(h.rows.length, "int"), inv: fmt(h.invested, "pct", { digits: 0 }) }) : undefined}>
      {q.isPending ? <Skeleton height={160} /> : q.isError ? <Empty what={t("Non disponibile")} why={q.error.message} /> : h!.rows.length === 0 ? (
        <p className="honest" style={{ margin: 0 }}>{t("Nessun titolo passa i filtri oggi.")}</p>
      ) : (
        <>
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="dense">
              <thead><tr><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th>{t("Settore")}</th><th className="n">{t("Peso")}</th><th className="n">{t("Punteggio")}</th></tr></thead>
              <tbody>
                {h!.rows.map((r) => (
                  <tr key={r.ticker}>
                    <td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td>
                    <td className="clip">{r.name ?? "–"}</td><td className="clip">{r.sector ?? "–"}</td>
                    <td className="n">{fmt(r.weight, "pct")}</td><td className="n">{fmt(r.score, "score")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="honest">{t("Non è un consiglio: è ciò che le regole selezionano con i dati di oggi. Punteggio 0-1 della classifica (1 = la migliore).")}</p>
        </>
      )}
    </Card>
  );
}

function Overview({ onPick }: { onPick: (n: string) => void }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["strategies-overview"], queryFn: () => api<StrategiesOverview>("/strategies/overview"),
  });
  const runAll = useMutation({
    mutationFn: () => api("/strategies/run-missing", { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["strategies-overview"] }); qc.invalidateQueries({ queryKey: ["strategies"] }); },
  });
  if (q.isPending) return <Card title={t("Tutte le strategie a confronto")}><Skeleton height={200} /></Card>;
  if (q.isError) return <Card title={t("Tutte le strategie a confronto")}><Empty what={t("Confronto non disponibile")} why={q.error.message} /></Card>;
  const o = q.data;
  const queued = new Set(o.jobs.map((j) => j.label));
  const waiting = o.missing.filter((n) => !queued.has(n));
  return (
    <Card title={t("Tutte le strategie a confronto")} right={t("ordinate per rendimento nella seconda metà")}>
      {o.rows.length > 0 && (
        <>
          <Notice tone="info" title={t("Cautela:")}>{t("Con tante strategie provate sugli stessi dati, la prima è anche la più fortunata: conta di più chi regge in entrambe le metà e il confronto con l'indice a pesi uguali.")}</Notice>
          <div className="table-wrap">
            <table className="dense">
              <thead><tr><th>{t("Strategia")}</th><th>{t("Tipo")}</th><th className="n">{t("Dal")}</th><th className="n">{t("All'anno (tutto)")}</th><th className="n">{t("1a metà")}</th><th className="n">{t("2a metà")}</th><th className="n">Sharpe</th><th className="n">{t("Perdita massima")}</th><th className="n">{t("Rotazione")}</th></tr></thead>
              <tbody>
                {o.rows.map((r) => (
                  <tr key={r.name}>
                    <td><button type="button" className="linklike" onClick={() => onPick(r.name)}>{t(r.name)}</button>{r.stale && <span className="na" title={t("Backtest con dati di un giorno precedente")}> ·</span>}</td>
                    <td>{r.mine ? t("Mia") : t(r.group)}</td><td className="n">{r.since}</td>
                    <td className="n"><Num v={r.all} kind="pct" change /></td>
                    <td className="n"><Num v={r.first} kind="pct" change /></td>
                    <td className="n"><Num v={r.second} kind="pct" change /></td>
                    <td className="n">{fmt(r.sharpe, "num")}</td><td className="n">{fmt(r.drawdown, "pct")}</td>
                    <td className="n">{fmt(r.turnover, "mult", { digits: 1 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {o.jobs.length > 0 && <Notice tone="info" title={t("In lavorazione:")}><span className="spinner" aria-hidden="true" /> {t("{n} backtest in coda o in corso ({labels}).", { n: o.jobs.length, labels: o.jobs.map((j) => j.label).join(", ") })}</Notice>}
      {waiting.length > 0 && (
        <div className="row" style={{ marginTop: 10 }}>
          <span className="honest">{t("{n} strategie senza backtest con i dati di oggi: ognuna richiede 1-3 minuti.", { n: fmt(waiting.length, "int") })}</span>
          <button type="button" className="btn" disabled={runAll.isPending} onClick={() => runAll.mutate()}>{t("Calcola le {n} mancanti", { n: fmt(waiting.length, "int") })}</button>
        </div>
      )}
    </Card>
  );
}

function Results({ items, name, onPick, onEdit }: {
  items: StrategyItem[]; name: string; onPick: (n: string) => void; onEdit: (d: StrategyDefinition, editing: string | null) => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["strategy", name], placeholderData: keepPreviousData,
    queryFn: () => api<StrategyPage>(`/strategies/detail?name=${encodeURIComponent(name)}`),
  });
  const run = useMutation({
    mutationFn: () => api<Job>("/strategies/run", { method: "POST", body: JSON.stringify({ name }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategy", name] }); qc.invalidateQueries({ queryKey: ["strategies-overview"] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
    },
  });
  const del = useMutation({
    mutationFn: () => api(`/strategies/mine?name=${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["strategies"] }); qc.invalidateQueries({ queryKey: ["strategies-overview"] }); onPick(items[0].name); },
  });
  const p = q.data;
  const job = p?.job;
  const groups = GROUP_ORDER.map((g) => [g, items.filter((i) => i.group === g)] as const).filter(([, xs]) => xs.length);
  const other = items.filter((i) => !GROUP_ORDER.includes(i.group));

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <select className="select" style={{ minWidth: 280 }} value={name} onChange={(e) => onPick(e.target.value)} aria-label={t("Strategia")}>
          {groups.map(([g, xs]) => <optgroup key={g} label={t(g)}>{xs.map((i) => <option key={i.name} value={i.name}>{t(i.name)}</option>)}</optgroup>)}
          {other.length > 0 && <optgroup label={t("Altre")}>{other.map((i) => <option key={i.name} value={i.name}>{t(i.name)}</option>)}</optgroup>}
        </select>
        {p?.mine && p.definition && (
          <>
            <button type="button" className="btn" onClick={() => onEdit(p.definition!, p.name)}>{t("Modifica")}</button>
            <button type="button" className="btn" onClick={() => onEdit({ ...p.definition!, name: t("{name} (copia)", { name: t(p.name) }) }, null)}>{t("Duplica")}</button>
            <button type="button" className="btn ghost" onClick={() => { if (window.confirm(t("Eliminare \"{name}\"? I backtest già fatti restano nel database.", { name: t(p.name) }))) del.mutate(); }}>{t("Elimina")}</button>
          </>
        )}
        {p && !p.mine && p.definition && <button type="button" className="btn" onClick={() => onEdit({ ...p.definition!, name: t("{name} (mia)", { name: t(p.name) }) }, null)}>{t("Duplica e modifica")}</button>}
        {p && !p.definition && <span className="honest">{t("Si modifica nel Laboratorio.")}</span>}
      </div>
      {q.isPending ? <Skeleton height={300} /> : q.isError ? <Empty what={t("Strategia non disponibile")} why={q.error.message} /> : (
        <>
          <Card title={t(p!.name)} right={<Badge tone={p!.mine ? "info" : "neutral"}>{p!.mine ? t("mia") : t(p!.group).toLowerCase()}</Badge>}>
            {p!.description && <p className="lead" style={{ marginTop: 0 }}>{p!.description}</p>}
            {p!.investor && (
              <>
                <details className="details" style={{ marginBottom: 10 }}>
                  <summary>{t("L'investitore e la traduzione in regole")}</summary>
                  <dl className="sheet">
                    <dt>{t("Chi")}</dt><dd>{p!.investor.who}</dd><dt>{t("Fonte")}</dt><dd>{p!.investor.source}</dd>
                    <dt>{t("Il metodo originale")}</dt><dd>{p!.investor.original}</dd>
                    <dt>{t("Come è tradotto qui, e cosa manca")}</dt><dd>{p!.investor.translation}</dd>
                  </dl>
                </details>
                <Notice tone="info" title={t("Cautela:")}>{t("Le regole catturano solo la parte misurabile del metodo: i risultati veri di questi investitori venivano anche da giudizio, concentrazione, tempismo e mercati diversi dall'S&P 500 di oggi. Parametri fissati prima di guardare i risultati.")}</Notice>
              </>
            )}
            {p!.rules.length > 0 && (
              <dl className="sheet">{p!.rules.map((r) => <div key={r.label} className="contents"><dt>{r.label}</dt><dd>{r.text}</dd></div>)}</dl>
            )}
            {p!.watchlist_only && <Notice tone="warn" title={t("Selezione a posteriori:")}>{t("Usa le aziende che segui OGGI: le hai scelte sapendo come sono andate, quindi il backtest è ottimista (le perdenti del passato non sono nella lista).")}</Notice>}
          </Card>
          {job && job.state !== "done" && <JobLine job={job} />}
          {run.isError && <Notice tone="neg">{t("Non messo in coda: {msg}", { msg: run.error.message })}</Notice>}
          {p!.report === null ? (
            !active(job) && (
              <Empty what={t("Backtest non ancora fatto con i dati di oggi")} why={t("Dal primo giorno configurato a oggi, con i soli dati noti a ogni data: di solito 1-3 minuti (la prima volta qualcosa in più, per l'indice a pesi uguali).")}>
                <button type="button" className="btn primary" disabled={run.isPending} onClick={() => run.mutate()}>{t("Esegui il backtest")}</button>
              </Empty>
            )
          ) : (
            <>
              {p!.report.stale && (
                <div className="row" style={{ marginBottom: 10 }}>
                  <span className="honest">{t("Backtest con i dati al {date}: da allora sono arrivati prezzi e bilanci nuovi (di solito cambia poco).", { date: fmt(p!.report.data_as_of, "date") })}</span>
                  {!active(job) && <button type="button" className="btn" disabled={run.isPending} onClick={() => run.mutate()}>{t("Ricalcola")}</button>}
                </div>
              )}
              <Report page={p!} />
            </>
          )}
          <Holdings name={p!.name} />
        </>
      )}
      <Overview onPick={onPick} />
    </>
  );
}

// ---------------------------------------------------------------- costruttore
function Builder({ start, editing, onSaved, onCancel }: {
  start: StrategyDefinition | null; editing: string | null; onSaved: (name: string) => void; onCancel: () => void;
}) {
  const qc = useQueryClient();
  const opts = useQuery({ queryKey: ["strategy-builder"], queryFn: () => api<StrategyBuilder>("/strategies/builder") });
  const cat = useQuery({ queryKey: ["explore-catalog"], queryFn: () => api<ExploreCatalog>("/explore/catalog") });
  const excluded = useMemo(() => new Set(opts.data?.excluded ?? []), [opts.data]);
  const metricList = useMemo(() => (cat.data?.metrics ?? []).filter((m) => !excluded.has(m.key)), [cat.data, excluded]);
  const metrics = useMemo(() => new Map(metricList.map((m) => [m.key, m])), [metricList]);
  const groups = useMemo(() => groupMetrics(metricList), [metricList]);
  const [def, setDef] = useState<StrategyDefinition | null>(null);
  const [drafts, setDrafts] = useState<RuleDraft[]>([]);
  const [nextId, setNextId] = useState(1);
  const [source, setSource] = useState("");

  // definizione di partenza (nuova, da modificare o da duplicare), una volta pronte le opzioni
  useEffect(() => {
    if (!opts.data || !cat.data || def) return;
    const d = start ?? opts.data.default;
    setDef({ ...opts.data.default, ...d, universe: { ...opts.data.default.universe, ...d.universe }, portfolio: { ...opts.data.default.portfolio, ...d.portfolio } });
    setDrafts(draftsFrom(d.filters ?? [], metrics, 1));
    setNextId(1 + (d.filters?.length ?? 0));
  }, [opts.data, cat.data, def, start, metrics]);

  const parsed = parseDrafts(drafts, metrics);
  const definition = def ? { ...def, filters: parsed.flatMap((x) => (x.rule ? [x.rule] : [])) } : null;
  const debounced = useDebounced(JSON.stringify({ definition }), 350);
  const preview = useQuery({
    queryKey: ["strategy-preview", debounced], enabled: !!definition, placeholderData: keepPreviousData,
    queryFn: () => api<StrategyPreview>("/strategies/preview", { method: "POST", body: debounced }),
  });
  const save = useMutation({
    mutationFn: () => api<{ name: string }>("/strategies/mine", { method: "POST", body: JSON.stringify({ definition, editing }) }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["strategies"] }); qc.invalidateQueries({ queryKey: ["strategy"] });
      qc.invalidateQueries({ queryKey: ["strategies-overview"] }); qc.invalidateQueries({ queryKey: ["strategy-holdings"] });
      onSaved(r.name);
    },
  });

  if (opts.isPending || cat.isPending || !def) return <Skeleton height={320} />;
  if (opts.isError) return <Empty what={t("Costruttore non disponibile")} why={opts.error.message} />;
  if (cat.isError) return <Empty what={t("Costruttore non disponibile")} why={cat.error.message} />;
  const o = opts.data;
  const set = (patch: Partial<StrategyDefinition>) => setDef({ ...def, ...patch });
  const setP = (patch: Partial<StrategyDefinition["portfolio"]>) => setDef({ ...def, portfolio: { ...def.portfolio, ...patch } });
  const setU = (patch: Partial<StrategyDefinition["universe"]>) => setDef({ ...def, universe: { ...def.universe, ...patch } });
  const preset = o.presets.find((x) => x.name === source);
  const saved = o.saved.find((x) => `salvata:${x.name}` === source);
  const loadFilters = () => {
    const rules = preset?.rules ?? saved?.rules ?? [];
    setDrafts(draftsFrom(rules, metrics, nextId)); setNextId(nextId + rules.length);
    if (saved) setU({ sectors: saved.sectors.filter((s) => o.sectors.includes(s)) });
  };
  const pv = preview.data;

  return (
    <>
      <Card>
        <p className="honest" style={{ marginTop: 0 }}>{editing ? t("Stai modificando \"{name}\".", { name: t(editing) }) : t("Nuova strategia: universo, filtri, classifica, portafoglio. Il backtest userà a ogni data solo i dati noti allora.")}</p>
        <div className="form-grid">
          <label className="field"><span>{t("Nome")}</span><input className="text-input" value={def.name} onChange={(e) => set({ name: e.target.value })} maxLength={120} /></label>
          <label className="field"><span>{t("Descrizione (facoltativa)")}</span><input className="text-input" value={def.description ?? ""} onChange={(e) => set({ description: e.target.value })} /></label>
        </div>
      </Card>
      <Card title={t("1. Universo")}>
        <div className="row">
          <details className="dropdown">
            <summary className="btn">{def.universe.sectors.length ? t("Settori: {n}", { n: def.universe.sectors.length }) : t("Tutti i settori")}</summary>
            <div className="menu">
              {o.sectors.map((s) => (
                <label key={s} className="check"><input type="checkbox" checked={def.universe.sectors.includes(s)}
                  onChange={(e) => setU({ sectors: e.target.checked ? [...def.universe.sectors, s] : def.universe.sectors.filter((x) => x !== s) })} />{s}</label>
              ))}
              {def.universe.sectors.length > 0 && <button type="button" className="btn ghost" onClick={() => setU({ sectors: [] })}>{t("Tutti i settori")}</button>}
            </div>
          </details>
          <label className="check" title={t("La lista è quella di oggi: nel backtest le hai scelte col senno di poi, quindi è ottimista.")}>
            <input type="checkbox" checked={def.universe.watchlist_only} onChange={(e) => setU({ watchlist_only: e.target.checked })} />{t("Solo le aziende che seguo")}
          </label>
        </div>
        {def.universe.watchlist_only && <Notice tone="warn" title={t("Selezione a posteriori:")}>{t("la lista è quella di oggi: nel backtest le hai scelte col senno di poi, quindi il risultato è ottimista.")}</Notice>}
      </Card>
      <Card title={t("2. Filtri")} right={t("ogni condizione va rispettata; un dato mancante non passa")}>
        <div className="row" style={{ marginBottom: 10 }}>
          <select className="select" value={source} onChange={(e) => setSource(e.target.value)} aria-label={t("Parti da una ricerca di Esplora")}>
            <option value="">{t("Parti da una ricerca di Esplora…")}</option>
            <optgroup label={t("Ricerche pronte")}>{o.presets.map((x) => <option key={x.name} value={x.name}>{t(x.name)}</option>)}</optgroup>
            {o.saved.length > 0 && <optgroup label={t("Le tue ricerche")}>{o.saved.map((x) => <option key={x.name} value={`salvata:${x.name}`}>{x.name}</option>)}</optgroup>}
          </select>
          <button type="button" className="btn" disabled={!preset && !saved} onClick={loadFilters}>{t("Usa questi filtri")}</button>
        </div>
        <RuleRows drafts={drafts} setDrafts={setDrafts} metrics={metrics} groups={groups} ops={cat.data.ops} />
        <div className="row">
          <button type="button" className="btn" onClick={() => { setDrafts([...drafts, { id: nextId, metric: "roic", op: ">=", text: "" }]); setNextId(nextId + 1); }}>{t("＋ Aggiungi un filtro")}</button>
          {drafts.length > 0 && <button type="button" className="btn ghost" onClick={() => setDrafts([])}>{t("Togli tutti")}</button>}
        </div>
        <p className="honest">{t("Il voto della tua checklist non si può usare: esiste solo da oggi, nel backtest sarebbe un dato dal futuro.")}</p>
      </Card>
      <Card title={t("3. Classifica")} right={t("la media pesata delle posizioni in classifica")}>
        <p className="honest" style={{ marginTop: 0 }}>{t("Tra le aziende che passano i filtri si tengono le prime N per punteggio (per P/E e simili, più basso = meglio). Senza metriche si tengono le più grandi.")}</p>
        {def.ranking.map((r, i) => (
          <div key={i} className="rule">
            <select className="select" value={r.metric} aria-label={t("Metrica della classifica")}
              onChange={(e) => set({ ranking: def.ranking.map((x, k) => (k === i ? { ...x, metric: e.target.value } : x)) })}>
              {groupMetrics(o.rankable.map((m) => ({ ...m, fmt: "", better: "high", help: "", unit: "", factor: 1 }))).map(([g, ms]) => (
                <optgroup key={g} label={t(g)}>{ms.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</optgroup>
              ))}
            </select>
            <input type="range" min={0} max={1} step={0.05} value={r.weight} aria-label={t("Peso")}
              onChange={(e) => set({ ranking: def.ranking.map((x, k) => (k === i ? { ...x, weight: Number(e.target.value) } : x)) })} />
            <span className="unit">{t("peso {w}", { w: fmt(r.weight, "num") })}</span>
            <button type="button" className="btn ghost" aria-label={t("Togli questa metrica")} onClick={() => set({ ranking: def.ranking.filter((_, k) => k !== i) })}>✕</button>
          </div>
        ))}
        <button type="button" className="btn" onClick={() => set({ ranking: [...def.ranking, { metric: "roic", weight: 1 }] })}>{t("＋ Aggiungi una metrica alla classifica")}</button>
      </Card>
      <Card title={t("4. Portafoglio")}>
        <div className="form-grid">
          <label className="field"><span>{t("Numero di titoli")}</span><input className="text-input" type="number" min={1} max={600} value={def.portfolio.positions}
            onChange={(e) => setP({ positions: Math.max(1, Math.min(600, Number(e.target.value) || 1)) })} /></label>
          <label className="field"><span>{t("Pesi")}</span>
            <select className="select" value={def.portfolio.weighting} onChange={(e) => setP({ weighting: e.target.value })}>
              {o.weightings.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select></label>
          <label className="field"><span>{t("Ribilanciamento (più spesso = segue meglio le regole ma costa di più)")}</span>
            <select className="select" value={def.portfolio.rebalance_days} onChange={(e) => setP({ rebalance_days: Number(e.target.value) })}>
              {o.rebalance.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select></label>
          <span />
          <label className="field"><span>{t("Peso massimo per titolo: {v}", { v: fmt(def.portfolio.max_weight, "pct", { digits: 0 }) })}</span>
            <input type="range" min={0.01} max={1} step={0.01} value={def.portfolio.max_weight} onChange={(e) => setP({ max_weight: Number(e.target.value) })} /></label>
          <label className="field"><span>{t("Peso massimo per settore: {v}", { v: fmt(def.portfolio.max_sector, "pct", { digits: 0 }) })}</span>
            <input type="range" min={0.05} max={1} step={0.05} value={def.portfolio.max_sector} onChange={(e) => setP({ max_sector: Number(e.target.value) })} /></label>
        </div>
      </Card>
      {pv && (pv.ok
        ? <Notice tone="info" title={t("Anteprima:")}>{t("oggi passano i filtri {passing} aziende su {universe}; la strategia ne terrebbe {held}.", { passing: fmt(pv.passing ?? 0, "int"), universe: fmt(pv.universe ?? 0, "int"), held: fmt(pv.held ?? 0, "int") })}</Notice>
        : <Notice tone="warn" title={t("Da completare:")}>{pv.error}</Notice>)}
      <div className="actions">
        <button type="button" className="btn primary" disabled={!def.name.trim() || save.isPending} onClick={() => save.mutate()}>{t("Salva la strategia")}</button>
        <button type="button" className="btn ghost" onClick={onCancel}>{t("Annulla")}</button>
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </>
  );
}

export function StrategiesPage() {
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  // solo questo elenco leggero si rilegge mentre ci sono backtest in coda; quando uno finisce (o fallisce) si
  // ricaricano la scheda e il confronto, che costano di più
  const list = useQuery({
    queryKey: ["strategies"], queryFn: () => api<{ items: StrategyItem[]; jobs: Job[] }>("/strategies"),
    refetchInterval: (query) => ((query.state.data?.jobs.length ?? 0) > 0 ? 3000 : false),
  });
  const running = useRef<string[]>([]);
  const jobKeys = (list.data?.jobs ?? []).map((j) => `${j.key}:${j.state}`).join("|");
  useEffect(() => {
    const now = jobKeys ? jobKeys.split("|") : [];
    if (running.current.some((k) => !now.includes(k))) {
      qc.invalidateQueries({ queryKey: ["strategy"] });
      qc.invalidateQueries({ queryKey: ["strategies-overview"] });
    }
    running.current = now;
  }, [jobKeys, qc]);
  const [draft, setDraft] = useState<{ def: StrategyDefinition | null; editing: string | null; n: number }>({ def: null, editing: null, n: 0 });
  const building = params.get("vista") === "crea";
  const items = list.data?.items ?? [];
  const wanted = params.get("s");
  const name = wanted && items.some((i) => i.name === wanted) ? wanted : items[0]?.name;
  const pick = (n: string) => setParams({ s: n }, { replace: false });
  const edit = (def: StrategyDefinition | null, editing: string | null) => {
    setDraft({ def, editing, n: draft.n + 1 });
    setParams({ vista: "crea", ...(name ? { s: name } : {}) });
  };
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{t("Strategie")}</h1>
          <p className="lead">{t("Strategie a regole provate sul passato con i soli dati noti a ogni data: standard, dei grandi investitori o create da te. Un backtest non è una previsione.")}</p></div>
        {!building && <button type="button" className="btn primary" onClick={() => edit(null, null)}>{t("+ Nuova strategia")}</button>}
      </div>
      <div style={{ marginBottom: 16 }}>
        <Segmented label={t("Vista")} value={building ? "crea" : "risultati"}
          onChange={(v) => (v === "crea" ? edit(null, null) : setParams(name ? { s: name } : {}))}
          options={[{ value: "risultati", label: t("Risultati") }, { value: "crea", label: t("Crea o modifica") }]} />
      </div>
      {list.isPending ? <Skeleton height={300} /> : list.isError ? <Empty what={t("Strategie non disponibili")} why={list.error.message} /> : building ? (
        <Builder key={draft.n} start={draft.def} editing={draft.editing}
          onSaved={(n) => setParams({ s: n })} onCancel={() => setParams(name ? { s: name } : {})} />
      ) : name ? (
        <Results items={items} name={name} onPick={pick} onEdit={edit} />
      ) : <Empty what={t("Nessuna strategia")} />}
    </div>
  );
}
