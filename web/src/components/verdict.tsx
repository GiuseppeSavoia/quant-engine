// Testata dell'azienda e riquadro del giudizio (stesse regole di ui/components.verdict_card_html e
// ui/pages/azienda/scheda._head: tono per categoria, barra 0-1, "solo contesto" per Chi è dentro, prove disponibili).
import { Link } from "react-router-dom";
import type { Header, Pillar, Verdict } from "../lib/api";
import { fmt, kindOfMetric } from "../lib/fmt";
import { Badge, Card, Notice, Num, type Tone } from "./ui";
import { t, tn } from "../lib/i18n";

export const VERDICT_STYLE: Record<string, [string, Tone, string]> = {
  candidata: ["✓", "pos", "Candidata"],
  aspettare: ["◐", "warn", "Aspettare"],
  neutra: ["●", "neutral", "Neutra"],
  qualita_dubbia: ["!", "trap", "Dubbia"],
  evitare: ["✗", "neg", "Evitare"],
  dati_insufficienti: ["?", "off", "Dati insuff."],
};

export function VerdictBadge({ category, label }: { category: string; label?: string }) {
  const [sym, tone, short] = VERDICT_STYLE[category] ?? ["●", "neutral", category];
  return <Badge tone={tone} title={label}>{sym} {t(short)}</Badge>;
}

export function PillarRow({ p }: { p: Pillar }) {
  const total = p.evidence.length;
  const available = total - p.missing.length;
  const context = p.key === "insiders";
  const cov = total && available < total ? <span className="cov">{tn(available, "{n} prova su {total}", "{n} prove su {total}", { total })}</span> : null;
  if (p.score === null) {
    return (
      <div className="pillar"><span className="lab">{p.label}</span><span className="off">{t("dati insufficienti")}</span>
        {context && <Badge>{t("solo contesto")}</Badge>}{cov}</div>
    );
  }
  const x = Math.max(0, Math.min(1, p.score));
  const tone = x >= 0.75 ? "pos" : x < 0.25 ? "neg" : "";
  return (
    <div className="pillar">
      <span className="lab">{p.label}</span>
      <span className={`bar ${tone}`} role="img" aria-label={t("{score} su 1", { score: fmt(x, "score") })}><i style={{ width: `${x * 100}%` }} /></span>
      <span className="sc">{fmt(x, "score")}</span>
      <span className="gr">{p.grade}</span>
      {context && <Badge>{t("solo contesto")}</Badge>}
      {cov}
    </div>
  );
}

export function VerdictCard({ v }: { v: Verdict }) {
  const [sym, tone] = VERDICT_STYLE[v.category] ?? ["●", "neutral"];
  const right = [v.profile_label, v.as_of ? t("al {date}", { date: fmt(v.as_of, "date_short") }) : ""].filter(Boolean).join(" · ");
  return (
    <Card title={t("Verdetto")} right={<span title={v.disclaimer}>{right}</span>} className="verdict">
      <div className="grid-2">
        <div>
          <p className={`label ${tone}`}><span aria-hidden="true">{sym} </span>{v.label}</p>
          <p className="rule">{v.rule}</p>
          <div style={{ display: "grid", gap: 6, marginBottom: 10 }}>
            {v.alarms.map((a) => <Notice key={a} tone="neg" title={t("Allarme:")}>{a}</Notice>)}
            {v.checks.map((c) => <Notice key={c} tone="warn" title={t("Da verificare:")}>{c}</Notice>)}
            {v.data_notes.map((c) => <Notice key={c} tone="warn" title={t("Dati di riserva:")}>{c}</Notice>)}
          </div>
          {Object.values(v.pillars).map((p) => <PillarRow key={p.key} p={p} />)}
        </div>
        <div>
          {v.changes.length > 0 && (
            <>
              <div className="sub">{t("Cosa farebbe cambiare il giudizio")}</div>
              <ul className="plain">{v.changes.slice(0, 4).map((c) => <li key={c}>{c}</li>)}</ul>
            </>
          )}
          {v.data_issues.length > 0 && <p className="honest">{t("Dati: {issues}", { issues: v.data_issues.join("; ") })}</p>}
          <p className="honest">{t("Criteri fissi applicati ai dati, non un segnale provato:")} <Link to="/limiti">{t("come funziona e limiti ›")}</Link></p>
        </div>
      </div>
    </Card>
  );
}

export function CompanyHead({ h, actions }: { h: Header; actions?: React.ReactNode }) {
  const meta = [h.sector, h.sub_industry, h.universe !== "S&P 500" ? h.universe : null].filter(Boolean).join(" · ");
  const src = [h.price_date ? t("chiusura {date}", { date: fmt(h.price_date, "date_short") }) : null,
    h.statement_date ? t("{kind} al {date}", { kind: h.statement_kind, date: fmt(h.statement_date, "date_short") }) : null].filter(Boolean).join(" · ");
  const { low, high } = h.range_52w;
  const pos = low !== null && high !== null && h.price !== null && high > low ? ((h.price - low) / (high - low)) * 100 : null;
  const pt = h.price_target;
  return (
    <Card>
      <div className="head">
        <div className="id">
          <h1 className="nm">{h.name || h.ticker}</h1>
          {h.name && <span className="tk">{h.ticker}</span>}
          {meta && <span className="meta">{meta}</span>}
        </div>
        <div className="px">
          <span className="v">{fmt(h.price, "price", { currency: h.currency })}</span>
          {h.change !== null && <span className="chg"><Num v={h.change} kind="pct" change digits={2} /></span>}
          {src && <span className="src">{src}</span>}
          {actions}
        </div>
        <dl className="kv">
          <div><dt>{t("Cap.")}</dt><dd><Num v={h.market_cap.value} kind="money" currency={h.currency} /></dd></div>
          {h.key_metrics.map((m) => (
            <div key={m.key}><dt>{m.label}</dt><dd><Num v={m.value} kind={kindOfMetric(m.kind)} /></dd></div>
          ))}
          <div><dt>{t("1 anno")}</dt><dd><Num v={h.return_1y.value} kind="pct" change /></dd></div>
          <div><dt>{t("52 sett.")}</dt><dd>
            {fmt(low, "price")}<span className="range" aria-hidden="true">{pos !== null && <i style={{ left: `${pos}%` }} />}</span>{fmt(high, "price")}
          </dd></div>
          <div><dt>{t("Tuo fair value")}</dt><dd>{h.fair_value !== null
            ? <>{fmt(h.fair_value, "price")} <Num v={h.fair_value_margin} kind="pct" digits={0} signed /></>
            : <span className="na">–</span>}</dd></div>
          <div><dt>{t("Soglie")}</dt><dd>{pt.buy_below || pt.sell_above
            ? [pt.buy_below ? t("compra sotto {price}", { price: fmt(pt.buy_below, "price") }) : "", pt.sell_above ? t("vendi sopra {price}", { price: fmt(pt.sell_above, "price") }) : ""].filter(Boolean).join(" · ")
            : <span className="na">–</span>}</dd></div>
        </dl>
      </div>
    </Card>
  );
}
