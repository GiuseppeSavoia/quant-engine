// Strategia automatica ORIGINALE: decisioni BUY/SKIP/BLOCKED dell'ultima analisi (sperimentale, ordini solo simulati),
// portafoglio simulato, peso per settore, rischio e prove di stress. Non è il giudizio della scheda azienda.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { fmt } from "../lib/fmt";

type N = number | null;
interface Auto {
  empty: boolean; command: string; as_of?: string; stale?: boolean; run?: string | null; regime?: string; counts?: Record<string, number>;
  orders?: { ticker: string; name: string | null; sector: string | null; weight: N; shares: N; reference_price: N; stop_loss: N; take_profit: N; confidence: N; margin_of_safety: N; order_type: string }[];
  invested?: N; by_sector?: { sector: string; weight: N }[];
  risk?: { volatility: N; beta: N; var_1d: N; cvar_1d: N; effective_positions: N; positions: N } | null;
  stress?: { scenario: string; portfolio: N; benchmark: N }[];
  decisions?: { ticker: string; name: string | null; sector: string | null; action: string; confidence: N; threshold: N; price: N; fair_value: N; margin_of_safety: N; mf_score: N; momentum: N; quality: N; volatility_score: N; reasoning: string }[];
  sectors?: string[];
}
const ORDER: Record<string, string> = { MARKET: "al mercato", LIMIT: "limite", MOC: "in chiusura", LOC: "limite in chiusura" };
const ACTION = (a: string) => a === "BUY" ? <Badge tone="pos">▲ BUY</Badge> : a === "BLOCKED" ? <Badge tone="neg">■ BLOCKED</Badge> : <Badge tone="off">● SKIP</Badge>;

export function AutoStrategyPage() {
  const q = useQuery({ queryKey: ["auto-strategy"], queryFn: () => api<Auto>("/auto-strategy") });
  const [actions, setActions] = useState<string[]>(["BUY", "BLOCKED"]);
  const [sector, setSector] = useState("");
  const [text, setText] = useState("");
  const head = (
    <>
      <h1>Strategia automatica</h1>
      <Notice tone="warn" title="Sperimentale.">BUY, SKIP e BLOCKED sono le decisioni della strategia automatica ORIGINALE (regime di mercato, DCF, multi-factor), con ordini solo simulati: nessun broker collegato. Non sono il giudizio della scheda azienda, che usa criteri diversi, e non sono raccomandazioni di investimento.</Notice>
    </>
  );
  if (q.isPending) return <div className="page">{head}<Skeleton height={300} /></div>;
  if (q.isError) return <div className="page">{head}<Empty what="Non disponibile" why={q.error.message} /></div>;
  const a = q.data;
  if (a.empty) return <div className="page">{head}<Empty what="Nessuna analisi nel database" why={<>Si calcola da Terminale sui dati già scaricati (1-2 minuti): <code>.venv/bin/{a.command}</code></>} /></div>;
  const t = text.trim().toLowerCase();
  const rows = a.decisions!.filter((d) => (!actions.length || actions.includes(d.action)) && (!sector || d.sector === sector)
    && (!t || d.ticker.toLowerCase().includes(t) || (d.name ?? "").toLowerCase().includes(t)));
  return (
    <div className="page">
      {head}
      <Card>
        <dl className="kv big">
          <div><dt>Regime di mercato</dt><dd>{a.regime}</dd></div>
          <div><dt>BUY</dt><dd>{a.counts!.BUY}</dd></div><div><dt>SKIP</dt><dd>{a.counts!.SKIP}</dd></div><div><dt>BLOCKED</dt><dd>{a.counts!.BLOCKED}</dd></div>
          <div><dt>Dati al</dt><dd>{fmt(a.as_of ?? null, "date")}</dd></div>
        </dl>
        <p className="honest">Ultima analisi del {fmt(a.run?.slice(0, 10) ?? null, "date")} {a.run?.slice(11, 16)}.</p>
        {a.stale && <Notice tone="info">Analisi sui dati del {fmt(a.as_of ?? null, "date")}: la strategia non si rifà più a ogni aggiornamento (è sperimentale e non serve al giudizio). Per rifarla sui dati di oggi: <code>.venv/bin/{a.command}</code></Notice>}
      </Card>
      <Card title="Portafoglio simulato" right={<Badge tone="neutral">solo simulato</Badge>}>
        {a.orders!.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun ordine: nessun titolo ha superato i filtri.</p> : (
          <div className="table-wrap">
            <table className="dense">
              <thead><tr><th>Ticker</th><th>Azienda</th><th>Settore</th><th className="n">Peso</th><th className="n">Azioni</th><th className="n">Prezzo $</th><th className="n">Stop-loss $</th><th className="n">Take-profit $</th><th className="n">Confidenza</th><th className="n">Margine DCF</th><th>Tipo</th></tr></thead>
              <tbody>{a.orders!.map((o) => (
                <tr key={o.ticker}><td><Link to={`/azienda/${encodeURIComponent(o.ticker)}`}><b>{o.ticker}</b></Link></td><td className="clip">{o.name}</td><td className="clip">{o.sector}</td>
                  <td className="n">{fmt(o.weight, "pct")}</td><td className="n">{fmt(o.shares, "int")}</td><td className="n">{fmt(o.reference_price, "price")}</td><td className="n">{fmt(o.stop_loss, "price")}</td>
                  <td className="n">{fmt(o.take_profit, "price")}</td><td className="n">{fmt(o.confidence, "score")}</td><td className="n"><Num v={o.margin_of_safety} kind="pct" change /></td><td>{ORDER[o.order_type] ?? o.order_type?.toLowerCase()}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="grid-2">
        <Card title="Peso per settore" right={a.orders!.length ? `investito ${fmt(a.invested, "pct", { digits: 0 })} · liquidità ${fmt(1 - (a.invested ?? 0), "pct", { digits: 0 })}` : undefined}>
          {a.by_sector!.map((s) => (
            <div key={s.sector} className="hbar"><span className="lab">{s.sector}</span><span className="track"><i className="s1" style={{ width: `${Math.round((s.weight ?? 0) * 100)}%` }} /></span><span className="val">{fmt(s.weight, "pct")}</span></div>
          ))}
        </Card>
        {a.risk && (
          <Card title="Rischio del portafoglio simulato" right="pesi di oggi sui rendimenti dell'ultimo anno">
            <dl className="kv">
              <div><dt>Volatilità annua</dt><dd>{fmt(a.risk.volatility, "pct")}</dd></div><div><dt>Beta</dt><dd>{fmt(a.risk.beta, "num")}</dd></div>
              <div><dt>VaR 1 giorno</dt><dd>{fmt(a.risk.var_1d === null ? null : -a.risk.var_1d, "pct")}</dd></div><div><dt>CVaR 1 giorno</dt><dd>{fmt(a.risk.cvar_1d === null ? null : -a.risk.cvar_1d, "pct")}</dd></div>
              <div><dt>Posizioni equivalenti</dt><dd>{fmt(a.risk.effective_positions, "num", { digits: 1 })}</dd></div>
            </dl>
            <p className="honest">VaR: perdita superata nel 5% peggiore dei giorni; CVaR: perdita media in quei giorni. Beta 1 = si muove come il mercato.</p>
            {a.stress!.length > 0 && (
              <>
                <table className="dense" style={{ marginTop: 10 }}>
                  <thead><tr><th>Prova di stress</th><th className="n">Portafoglio simulato</th><th className="n">Benchmark</th></tr></thead>
                  <tbody>{a.stress!.map((x) => <tr key={x.scenario}><td className="wrap">{x.scenario}</td><td className="n"><Num v={x.portfolio} kind="pct" change /></td><td className="n"><Num v={x.benchmark} kind="pct" change /></td></tr>)}</tbody>
                </table>
                <p className="honest">Il portafoglio di oggi nelle crisi passate e con shock ipotetici. Il beta sottostima le crisi: un portafoglio investito a metà perde circa la metà.</p>
              </>
            )}
          </Card>
        )}
      </div>
      <Card title="Tutte le decisioni" right={`${rows.length} titoli su ${a.decisions!.length}, ordinati per confidenza`}>
        <div className="row" style={{ marginBottom: 10 }}>
          <div className="pills" role="group" aria-label="Decisione">
            {["BUY", "SKIP", "BLOCKED"].map((x) => <button key={x} type="button" className={`pill${actions.includes(x) ? " on" : ""}`} aria-pressed={actions.includes(x)}
              onClick={() => setActions(actions.includes(x) ? actions.filter((y) => y !== x) : [...actions, x])}>{x}</button>)}
          </div>
          <select className="select" value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Settore"><option value="">Tutti i settori</option>{a.sectors!.map((s) => <option key={s}>{s}</option>)}</select>
          <input className="text-input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ticker o nome" aria-label="Cerca" />
        </div>
        <div className="table-wrap" style={{ maxHeight: 600 }}>
          <table className="dense">
            <thead><tr><th>Ticker</th><th>Azienda</th><th>Decisione</th><th className="n">Confidenza</th><th className="n">Soglia</th><th className="n">Prezzo $</th><th className="n">Fair value DCF $</th><th className="n">Margine DCF</th><th className="n">Multi-factor</th><th>Motivazione</th></tr></thead>
            <tbody>{rows.map((d) => (
              <tr key={d.ticker}><td><Link to={`/azienda/${encodeURIComponent(d.ticker)}?fn=automatica`}><b>{d.ticker}</b></Link></td><td className="clip">{d.name}</td><td>{ACTION(d.action)}</td>
                <td className="n">{fmt(d.confidence, "score")}</td><td className="n">{fmt(d.threshold, "score")}</td><td className="n">{fmt(d.price, "price")}</td><td className="n">{fmt(d.fair_value, "price")}</td>
                <td className="n"><Num v={d.margin_of_safety} kind="pct" change /></td><td className="n">{fmt(d.mf_score, "score")}</td><td className="wrap">{d.reasoning}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
