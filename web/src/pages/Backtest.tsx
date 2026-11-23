// Backtest della strategia automatica originale e ricerca di segnali e fattori: risultati già calcolati da main.py
// (l'interfaccia non lancia calcoli di main.py: se mancano, dice il comando da terminale). Dati dal servizio lab.
import { useQuery } from "@tanstack/react-query";
import { PeriodChart } from "../components/charts";
import { Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { fmt } from "../lib/fmt";

type N = number | null;
interface Curves { dates: string[]; series: { key: string; values: N[] }[] }
interface BacktestPage {
  commands: Record<"backtest" | "research" | "factors", string>;
  strategy: null | {
    computed_at: string | null; params: Record<string, unknown>;
    metrics: { total_return: N; sharpe_ratio: N; max_drawdown: N; n_trades: N; total_costs: N; avg_coverage: N; min_coverage: N };
    benchmark: { total_return: N; sharpe_ratio: N }; curves: Curves;
    years: { year: number; strategy: N; benchmark: N }[]; coverage: Curves | null;
  };
  factors: null | { horizon: number; ic: { factor: string; ic_mean: N; ic_t: N }[]; weights: { factor: string; weight: N }[];
    composite: { period: string; ic_mean: N; ic_t: N; ic_hit_rate: N; spread_annual: N }[] };
  signals: null | { signal: string; horizon: number; ic_mean: N; ic_t: N; ic_hit_rate: N; ic_sector_neutral: N; spread_annual: N; monotonicity: N; verdict: string }[];
}

const signed3 = (v: N) => fmt(v, "num", { digits: 3, signed: true });

function Missing({ what, cmd }: { what: string; cmd: string }) {
  return <Empty what={what} why={<>Si calcola da Terminale, senza scaricare dati: <code>.venv/bin/{cmd}</code></>} />;
}

export function BacktestPage() {
  const q = useQuery({ queryKey: ["backtest"], queryFn: () => api<BacktestPage>("/backtest") });
  if (q.isPending) return <div className="page"><h1>Backtest e ricerca</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>Backtest e ricerca</h1><Empty what="Non disponibile" why={q.error.message} /></div>;
  const b = q.data;
  const s = b.strategy;
  const series = (c: Curves, keys: Record<string, string>) => c.series.filter((x) => keys[x.key]).map((x) => ({ key: x.key, label: keys[x.key], values: x.values }));
  return (
    <div className="page">
      <h1>Backtest e ricerca</h1>
      <p className="lead">La strategia automatica originale del progetto rifatta dal primo giorno configurato a oggi, con i soli dati noti a ogni data, e la misura di quanto i suoi segnali e i fattori classici hanno anticipato i rendimenti.</p>
      {!s ? <Missing what="Nessun backtest nel database" cmd={b.commands.backtest} /> : (
        <>
          <Card title="Strategia automatica originale" right={`dal ${fmt(String(s.params.start ?? ""), "date_short")} al ${fmt(String(s.params.end ?? ""), "date_short")} · calcolato il ${fmt(s.computed_at?.slice(0, 10) ?? null, "date")}`}>
            <dl className="kv big">
              <div><dt>Rendimento</dt><dd><Num v={s.metrics.total_return} kind="pct" change /></dd></div>
              <div><dt>Benchmark</dt><dd><Num v={s.benchmark.total_return} kind="pct" change /></dd></div>
              <div><dt>Differenza</dt><dd>{fmt(s.metrics.total_return !== null && s.benchmark.total_return !== null ? s.metrics.total_return - s.benchmark.total_return : null, "pp")}</dd></div>
              <div><dt>Sharpe (strategia / benchmark)</dt><dd>{fmt(s.metrics.sharpe_ratio, "num")} / {fmt(s.benchmark.sharpe_ratio, "num")}</dd></div>
              <div><dt>Perdita massima</dt><dd>{fmt(s.metrics.max_drawdown, "pct")}</dd></div>
              <div><dt>Operazioni</dt><dd>{fmt(s.metrics.n_trades, "int")}</dd></div>
              <div><dt>Costi</dt><dd>{fmt(s.metrics.total_costs, "money", { currency: "USD" })}</dd></div>
            </dl>
            <p className="honest">Ribilanciamento ogni {fmt(Number(s.params.rebalance_days), "int")} giorni, {fmt(Number(s.params.n_tickers), "int")} titoli. Ordini solo simulati: è la strategia sperimentale, non il giudizio della scheda azienda.</p>
            <PeriodChart type="lines" kind="money" zero={false} title="Valore del portafoglio" height={300} labels={s.curves.dates.map((d) => fmt(d, "date_short"))}
              series={series(s.curves, { strategia: "Strategia", benchmark: "Benchmark (compra e tieni)" })} />
            <PeriodChart type="lines" kind="pct" title="Quota investita" height={150} labels={s.curves.dates.map((d) => fmt(d, "date_short"))}
              series={series(s.curves, { esposizione: "Esposizione" })} />
          </Card>
          <div className="grid-2">
            <Card title="Anno per anno" right={s.years.some((y) => y.benchmark !== null) ? `batte il benchmark in ${s.years.filter((y) => y.strategy !== null && y.benchmark !== null && y.strategy > y.benchmark).length} anni su ${s.years.length}` : undefined}>
              <PeriodChart type="bars" kind="pct" title="Rendimento nell'anno" labels={s.years.map((y) => String(y.year))}
                series={[{ key: "s", label: "Strategia", values: s.years.map((y) => y.strategy) }, { key: "b", label: "Benchmark", values: s.years.map((y) => y.benchmark) }]} />
              <p className="honest">Il primo e l'ultimo anno possono essere parziali.</p>
            </Card>
            {s.coverage && (
              <Card title="Copertura dell'universo" right={`media ${fmt(s.metrics.avg_coverage, "pct")} · minimo ${fmt(s.metrics.min_coverage, "pct")}`}>
                <PeriodChart type="lines" kind="pct" zero={false} title="Membri dell'indice con un prezzo" labels={s.coverage.dates.map((d) => fmt(d, "date_short"))}
                  series={series(s.coverage, { share: "Copertura" })} />
                <Notice tone="warn" title="Survivorship bias:">i titoli senza prezzo sono ex membri delistati che Yahoo non ha più (in genere aziende acquisite o fallite). Per eliminarlo servono i prezzi dei delistati (fornitore a pagamento).</Notice>
              </Card>
            )}
          </div>
        </>
      )}

      <h2>Ricerca dei fattori classici</h2>
      {!b.factors ? <Missing what="Ricerca dei fattori non ancora fatta" cmd={b.commands.factors} /> : (
        <>
          <Card title={`IC medio a ${b.factors.horizon} giorni`} right="positivo: il fattore ha ragione; negativo: funziona al contrario">
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Fattore</th><th className="n">IC medio</th><th className="n">t</th><th /></tr></thead>
                <tbody>{b.factors.ic.map((r) => (
                  <tr key={r.factor}><td>{r.factor}</td><td className="n"><span className={(r.ic_mean ?? 0) >= 0 ? "pos" : "neg"}>{signed3(r.ic_mean)}</span></td>
                    <td className="n">{fmt(r.ic_t, "num", { digits: 1, signed: true })}</td>
                    <td style={{ width: "40%" }}><span className="icbar"><i style={{ width: `${Math.min(100, Math.abs(r.ic_mean ?? 0) * 1000)}%`, background: (r.ic_mean ?? 0) >= 0 ? "var(--series-1)" : "var(--neg)" }} /></span></td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
          <div className="grid-2">
            <Card title="Pesi della combinazione" right="scelti solo sulla prima metà">
              {b.factors.weights.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun fattore con IC positivo nella prima metà.</p> : (
                <table className="dense"><tbody>{b.factors.weights.map((w) => <tr key={w.factor}><td>{w.factor}</td><td className="n">{fmt(w.weight, "pct")}</td></tr>)}</tbody></table>
              )}
            </Card>
            <Card title="La combinazione nelle due metà" right={`orizzonte ${b.factors.horizon} giorni`}>
              <table className="dense">
                <thead><tr><th>Periodo</th><th className="n">IC medio</th><th className="n">t</th><th className="n">Date con IC &gt; 0</th><th className="n">Spread annuo Q5-Q1</th></tr></thead>
                <tbody>{b.factors.composite.map((c) => (
                  <tr key={c.period}><td>{c.period}</td><td className="n">{signed3(c.ic_mean)}</td><td className="n">{fmt(c.ic_t, "num", { signed: true })}</td>
                    <td className="n">{fmt(c.ic_hit_rate, "pct")}</td><td className="n"><Num v={c.spread_annual} kind="pct" change /></td></tr>
                ))}</tbody>
              </table>
              <Notice tone="info" title="Cautela:">conta solo la seconda metà: i pesi sono stati scelti senza guardarla. Per provarla come strategia: Laboratorio, punto di partenza «Fattori: pesi dalla ricerca».</Notice>
            </Card>
          </div>
        </>
      )}

      <h2>Ricerca dei segnali della strategia</h2>
      {!b.signals ? <Missing what="Nessuna ricerca dei segnali nel database" cmd={b.commands.research} /> : (
        <Card>
          <Notice tone="info" title="Come leggerlo:">IC = correlazione di rango tra punteggio e rendimento futuro (positivo: il segnale ha ragione, negativo: funziona al contrario). Serve |t| ≥ 3 per parlare di segnale solido.</Notice>
          <div className="table-wrap">
            <table className="dense">
              <thead><tr><th>Segnale</th><th className="n">Orizzonte (gg)</th><th className="n">IC medio</th><th className="n">t</th><th className="n">Date con IC &gt; 0</th><th className="n">IC nei settori</th><th className="n">Spread annuo Q5-Q1</th><th className="n">Monotonia</th><th>Giudizio</th></tr></thead>
              <tbody>{b.signals.map((r) => (
                <tr key={`${r.signal}-${r.horizon}`} className={Math.abs(r.ic_t ?? 0) >= 3 ? "me" : undefined}>
                  <td>{r.signal}</td><td className="n">{r.horizon}</td><td className="n"><span className={(r.ic_mean ?? 0) >= 0 ? "pos" : "neg"}>{signed3(r.ic_mean)}</span></td>
                  <td className="n">{fmt(r.ic_t, "num", { signed: true })}</td><td className="n">{fmt(r.ic_hit_rate, "pct")}</td><td className="n">{signed3(r.ic_sector_neutral)}</td>
                  <td className="n"><Num v={r.spread_annual} kind="pct" change /></td><td className="n">{fmt(r.monotonicity, "num")}</td><td>{r.verdict}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
