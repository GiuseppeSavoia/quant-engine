// Laboratorio della strategia automatica originale: cambi i parametri e rifai il backtest sugli stessi dati (in coda
// sul server, come i backtest delle strategie). La configurazione salvata non cambia: ogni prova resta registrata con
// la versione del codice e i parametri. Controlli e calcoli nel motore (servizio lab).
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { PeriodChart } from "../components/charts";
import { Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api, type Job } from "../lib/api";
import { fmt } from "../lib/fmt";

type N = number | null;
type Value = number | boolean | string | string[];
interface Param { key: string; label: string; group: string; kind: "float" | "int" | "bool" | "choice" | "sectors"; min: N; max: N; step: N; options: string[]; help: string; value: Value }
interface Options { params: Param[]; presets: { name: string; values: Record<string, Value> }[]; periods: { value: string; label: string }[]; sectors: string[] }
interface Preview { ok: boolean; error: string | null; overrides?: Record<string, Value>; description?: string; start?: string; end?: string }
interface ExpRow { id: number; label: string; name: string; period: string; data_as_of: string; created_at: string; lab: boolean; baseline: boolean; same_data: boolean; total_return: N; sharpe: N }
interface Experiments { as_of: string; rows: ExpRow[]; same_data: number; jobs: Job[] }
interface Compare {
  rows: { id: number; label: string; period: string; data_as_of: string; lab: boolean; total_return: N; excess_return_vs_benchmark: N; cagr: N; sharpe_ratio: N; max_drawdown: N; n_trades: N; total_costs: N; avg_exposure: N; turnover_annual: N; avg_coverage: N; description: string | null; yaml: string | null; created_at: string; seconds: N; git_commit: string | null; git_dirty: boolean; config_hash: string }[];
  curves: { dates: string[]; series: { key: string; values: N[] }[] } | null; ic: { signal: string; values: Record<string, N> }[]; warnings: string[];
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

function Field({ p, value, sectors, onChange }: { p: Param; value: Value; sectors: string[]; onChange: (v: Value) => void }) {
  if (p.kind === "bool") return <label className="check" title={p.help}><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />{p.label}</label>;
  if (p.kind === "choice") return (
    <label className="field" title={p.help}><span>{p.label}</span>
      <select className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>{p.options.map((o) => <option key={o} value={o}>{o}</option>)}</select></label>
  );
  if (p.kind === "sectors") {
    const list = (value as string[]) ?? [];
    return (
      <div className="field" title={p.help}><span>{p.label}</span>
        <details className="dropdown"><summary className="btn">{list.length ? `${list.length} settori` : "Nessun settore"}</summary>
          <div className="menu">{sectors.map((s) => <label key={s} className="check"><input type="checkbox" checked={list.includes(s)}
            onChange={(e) => onChange(e.target.checked ? [...list, s] : list.filter((x) => x !== s))} />{s}</label>)}</div></details></div>
    );
  }
  return (
    <label className="field" title={p.help}><span>{p.label}</span>
      <input className="text-input" type="number" min={p.min ?? undefined} max={p.max ?? undefined} step={p.step ?? undefined} value={Number(value)}
        onChange={(e) => onChange(p.kind === "int" ? Math.round(Number(e.target.value)) : Number(e.target.value))} /></label>
  );
}

function Runner({ o }: { o: Options }) {
  const qc = useQueryClient();
  const base = useMemo(() => Object.fromEntries(o.params.map((p) => [p.key, p.value])), [o]);
  const [values, setValues] = useState<Record<string, Value>>(base);
  const [period, setPeriod] = useState("all");
  const [name, setName] = useState("");
  const [research, setResearch] = useState(false);
  const body = JSON.stringify({ values, period, name, with_research: research });
  const debounced = useDebounced(body, 300);
  const preview = useQuery({ queryKey: ["lab-preview", debounced], placeholderData: keepPreviousData,
    queryFn: () => api<Preview>("/lab/preview", { method: "POST", body: debounced }) });
  const run = useMutation({
    mutationFn: () => api<Job>("/lab/run", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lab-experiments"] }),
  });
  const groups = useMemo(() => {
    const g = new Map<string, Param[]>();
    for (const p of o.params) g.set(p.group, [...(g.get(p.group) ?? []), p]);
    return [...g.entries()];
  }, [o]);
  const pv = preview.data;
  return (
    <>
      <Card title="Punti di partenza">
        <div className="row">
          <button type="button" className="btn" onClick={() => { setValues(base); setName(""); }}>Configurazione attuale</button>
          {o.presets.map((p) => <button key={p.name} type="button" className="btn" onClick={() => { setValues({ ...base, ...p.values }); setName(p.name); }}>{p.name}</button>)}
        </div>
      </Card>
      <Card title="Parametri">
        {groups.map(([g, ps]) => {
          const body = (
            <div className="lab-grid">{ps.map((p) => <Field key={p.key} p={p} value={values[p.key]} sectors={o.sectors} onChange={(v) => setValues({ ...values, [p.key]: v })} />)}</div>
          );
          return g.startsWith("Pesi dei fattori")
            ? <details key={g} className="details" style={{ margin: "10px 0" }}><summary>{g}</summary>{body}</details>
            : <div key={g} style={{ margin: "10px 0" }}><div className="sub">{g}</div>{body}</div>;
        })}
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field"><span>Nome dell'esperimento (facoltativo)</span><input className="text-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></label>
          <label className="field" title="Scegli le varianti sulla prima metà, poi verifica la migliore sulla seconda: è la prova che non hai adattato i parametri ai dati."><span>Periodo</span>
            <select className="select" value={period} onChange={(e) => setPeriod(e.target.value)}>{o.periods.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select></label>
        </div>
        <label className="check"><input type="checkbox" checked={research} onChange={(e) => setResearch(e.target.checked)} />Anche la ricerca dei segnali (raddoppia il tempo)</label>
        {pv && (pv.ok
          ? <Notice tone="info" title="Modifiche:">{pv.description} · backtest dal {fmt(pv.start?.slice(0, 10) ?? null, "date")} al {fmt(pv.end?.slice(0, 10) ?? null, "date")}: con lo storico lungo servono 1-2 minuti.</Notice>
          : <Notice tone="warn" title="Da correggere:">{pv.error}</Notice>)}
        <div className="actions">
          <button type="button" className="btn primary" disabled={!pv?.ok || run.isPending} onClick={() => run.mutate()}>Esegui il backtest</button>
          {run.data && run.data.state !== "done" && <span className="honest"><span className="spinner" aria-hidden="true" /> {run.data.state === "queued" ? `In coda (posizione ${run.data.position})` : "In corso"}: il risultato compare qui sotto.</span>}
          {run.isError && <span className="neg">{run.error.message}</span>}
        </div>
      </Card>
    </>
  );
}

function Comparison({ ids, onDeleted }: { ids: number[]; onDeleted: (id: number) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["lab-compare", ids], enabled: ids.length > 0, placeholderData: keepPreviousData,
    queryFn: () => api<Compare>(`/lab/compare?${ids.map((i) => `id=${i}`).join("&")}`) });
  const del = useMutation({
    mutationFn: (id: number) => api(`/lab/experiments/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => { onDeleted(id); qc.invalidateQueries({ queryKey: ["lab-experiments"] }); qc.invalidateQueries({ queryKey: ["lab-compare"] }); },
  });
  if (ids.length === 0) return <Empty what="Nessun esperimento scelto" why="Scegline da uno a quattro qui sopra per confrontarli." />;
  if (q.isPending) return <Skeleton height={300} />;
  if (q.isError) return <Empty what="Confronto non disponibile" why={q.error.message} />;
  const c = q.data;
  const label = (k: string) => k === "benchmark" ? "Benchmark (compra e tieni)" : c.rows.find((r) => `e${r.id}` === k)?.label ?? k;
  return (
    <>
      <Card title="Esperimenti a confronto">
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Esperimento</th><th className="n">Rendimento</th><th className="n">vs benchmark</th><th className="n">All'anno</th><th className="n">Sharpe</th><th className="n">Perdita max</th><th className="n">Operazioni</th><th className="n">Costi</th><th className="n">Esposizione</th><th className="n">Rotazione</th><th className="n">Copertura</th><th>Periodo</th></tr></thead>
            <tbody>{c.rows.map((r) => (
              <tr key={r.id}><td>{r.label}</td><td className="n"><Num v={r.total_return} kind="pct" change /></td><td className="n">{fmt(r.excess_return_vs_benchmark, "pp")}</td>
                <td className="n"><Num v={r.cagr} kind="pct" change /></td><td className="n">{fmt(r.sharpe_ratio, "num")}</td><td className="n">{fmt(r.max_drawdown, "pct")}</td>
                <td className="n">{fmt(r.n_trades, "int")}</td><td className="n">{fmt(r.total_costs, "money", { currency: "USD" })}</td><td className="n">{fmt(r.avg_exposure, "pct")}</td>
                <td className="n">{fmt(r.turnover_annual, "mult")}</td><td className="n">{fmt(r.avg_coverage, "pct")}</td><td>{r.period}</td></tr>
            ))}</tbody>
          </table>
        </div>
        <Notice tone="info" title="Cautela:">più varianti si provano sugli stessi dati, più la migliore è anche la più fortunata. Una differenza piccola (qualche punto di rendimento, 0,1-0,2 di Sharpe) non dimostra nulla: va confermata sulla seconda metà o su dati che il laboratorio non ha visto.</Notice>
        {c.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
        {c.curves && <PeriodChart type="lines" kind="money" zero={false} title="Valore del portafoglio" height={320} labels={c.curves.dates.map((d) => fmt(d, "date_short"))}
          series={c.curves.series.map((s) => ({ key: s.key, label: label(s.key), values: s.values }))} />}
      </Card>
      {c.ic.length > 0 && (
        <Card title="IC dei segnali a 21 giorni">
          <table className="dense">
            <thead><tr><th>Segnale</th>{Object.keys(c.ic[0].values).map((k) => <th key={k} className="n">{k}</th>)}</tr></thead>
            <tbody>{c.ic.map((r) => <tr key={r.signal}><td>{r.signal}</td>{Object.entries(r.values).map(([k, v]) => <td key={k} className="n">{fmt(v, "num", { digits: 3, signed: true })}</td>)}</tr>)}</tbody>
          </table>
        </Card>
      )}
      {c.rows.map((r) => (
        <details key={r.id} className="details card" style={{ padding: 16 }}>
          <summary>{r.label}</summary>
          {!r.lab ? <p>Strategia a regole provata nella pagina Strategie: si modifica lì, non nel Laboratorio.</p> : (
            <>
              <p>{r.description}</p>
              {r.yaml && <><p className="honest">Modifiche rispetto alla configurazione attuale (per adottarle vanno copiate in config.yaml):</p><pre className="code">{r.yaml}</pre></>}
            </>
          )}
          <p className="honest">Creato il {fmt(r.created_at?.slice(0, 10) ?? null, "date")} in {fmt(r.seconds, "int")} secondi con i dati al {fmt(r.data_as_of, "date")} · versione del codice {r.git_commit ?? "–"}{r.git_dirty ? " (con modifiche non salvate)" : ""} · configurazione {r.config_hash}</p>
          <button type="button" className="btn ghost" onClick={() => { if (window.confirm(`Eliminare ${r.label}?`)) del.mutate(r.id); }}>Elimina questo esperimento</button>
        </details>
      ))}
    </>
  );
}

export function LabPage() {
  const opts = useQuery({ queryKey: ["lab-options"], queryFn: () => api<Options>("/lab/options") });
  const qc = useQueryClient();
  const exps = useQuery({ queryKey: ["lab-experiments"], queryFn: () => api<Experiments>("/lab/experiments"),
    refetchInterval: (q) => ((q.state.data?.jobs.length ?? 0) > 0 ? 3000 : false) });
  const [picked, setPicked] = useState<number[] | null>(null);
  const seen = useRef<number | null>(null);
  const rows = exps.data?.rows ?? [];
  // un esperimento nuovo (appena finito) entra da solo nel confronto
  const newest = rows[0]?.id ?? null;
  useEffect(() => {
    if (newest === null) return;
    if (seen.current !== null && newest > seen.current) { setPicked((p) => [newest, ...(p ?? [])].slice(0, 4)); qc.invalidateQueries({ queryKey: ["lab-compare"] }); }
    seen.current = newest;
  }, [newest, qc]);
  const chosen = picked ?? rows.filter((r) => r.same_data).slice(0, 3).map((r) => r.id);
  return (
    <div className="page">
      <h1>Laboratorio della strategia</h1>
      <p className="lead">Cambia i parametri della strategia automatica originale e rifai il backtest sugli stessi dati. La configurazione salvata non cambia; ogni prova resta registrata con la versione del codice e i parametri.</p>
      {opts.isPending ? <Skeleton height={300} /> : opts.isError ? <Empty what="Laboratorio non disponibile" why={opts.error.message} /> : <Runner o={opts.data} />}
      <h2>Confronto</h2>
      {exps.isPending ? <Skeleton height={160} /> : exps.isError ? <Empty what="Esperimenti non disponibili" why={exps.error.message} /> : rows.length === 0 ? (
        <Empty what="Nessun esperimento ancora" why="Scegli un punto di partenza o cambia i parametri, poi premi «Esegui il backtest»." />
      ) : (
        <>
          {exps.data.jobs.length > 0 && <Notice tone="info" title="In lavorazione:"><span className="spinner" aria-hidden="true" /> {exps.data.jobs.map((j) => j.label).join(", ")}</Notice>}
          <Card title="Esperimenti" right={`${exps.data.same_data} sugli stessi dati (al ${fmt(exps.data.as_of, "date")}) · scegline fino a 4`}>
            <div className="table-wrap" style={{ maxHeight: 280 }}>
              <table className="dense">
                <thead><tr><th aria-label="Scelto" /><th>Esperimento</th><th>Dati al</th><th className="n">Rendimento</th><th className="n">Sharpe</th></tr></thead>
                <tbody>{rows.map((r) => {
                  const on = chosen.includes(r.id);
                  return (
                    <tr key={r.id} className={on ? "me" : undefined}>
                      <td><input type="checkbox" checked={on} disabled={!on && chosen.length >= 4} aria-label={`Confronta ${r.label}`}
                        onChange={(e) => setPicked(e.target.checked ? [...chosen, r.id] : chosen.filter((x) => x !== r.id))} /></td>
                      <td>{r.label}{r.baseline ? " · configurazione attuale" : ""}{!r.lab ? " · strategia" : ""}</td><td>{fmt(r.data_as_of, "date_short")}</td>
                      <td className="n"><Num v={r.total_return} kind="pct" change /></td><td className="n">{fmt(r.sharpe, "num")}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          </Card>
          <Comparison ids={chosen} onDeleted={(id) => setPicked(chosen.filter((x) => x !== id))} />
        </>
      )}
    </div>
  );
}
