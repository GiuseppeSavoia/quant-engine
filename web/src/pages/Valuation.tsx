// Valutazione a leve (funzione DCF della scheda): leve per scenario modificabili, risultato ricalcolato dal motore
// mentre si scrive (POST /valuation, niente calcoli qui), controlli di coerenza, "è plausibile?", simulazione,
// leve che contano di più, anno per anno; "Salva nel diario" aggiorna anche il tuo fair value.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api, ApiError, type LeverMeta, type Valuation } from "../lib/api";
import { fmt } from "../lib/fmt";
import { t } from "../lib/i18n";

type Draft = Record<string, Record<string, string>>;

const SCENARIO_ORDER = ["Pessimista", "Base", "Ottimista"];
const decimals = (m: LeverMeta) => (m.key === "margin_years" ? 0 : m.pct ? 1 : 2);

function toText(v: number, m: LeverMeta): string {
  const x = m.pct ? v * 100 : v;
  return String(Number(x.toFixed(decimals(m) + 1))).replace(".", ",");
}

/** "12,5" / "12.5" -> 12.5; null se non è un numero. */
function parse(text: string): number | null {
  const t = text.trim().replace(/\s|%/g, "");
  if (!t) return null;
  const norm = t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t;
  const x = Number(norm);
  return Number.isFinite(x) ? x : null;
}

function draftFrom(v: Valuation): { levers: Draft; probs: Record<string, string> } {
  const levers: Draft = {};
  for (const [name, lv] of Object.entries(v.levers)) {
    levers[name] = Object.fromEntries(v.lever_meta.map((m) => [m.key, toText(lv[m.key], m)]));
  }
  const probs = Object.fromEntries(Object.entries(v.probabilities).map(([n, p]) => [n, String(Math.round(p * 1000) / 10).replace(".", ",")]));
  return { levers, probs };
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function Tornado({ v }: { v: Valuation }) {
  const sim = v.simulation;
  const mid = v.scenarios.Base?.per_share;
  if (!sim || mid === null || mid === undefined || !sim.swing.length) return null;
  const vals = sim.swing.flatMap((s) => [s.low, s.high]).filter((x): x is number => x !== null).concat(mid);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pos = (x: number) => ((x - lo) / (hi - lo || 1)) * 100;
  return (
    <div className="tornado" role="img" aria-label={t("Quanto cambia il valore muovendo una leva alla volta")}>
      {sim.swing.map((s) => (
        <div className="row" key={s.lever}>
          <span className="lab">{s.label}</span>
          <span className="track">
            {s.low !== null && <i className="to-low" style={{ left: `${Math.min(pos(s.low), pos(mid))}%`, width: `${Math.abs(pos(mid) - pos(s.low))}%` }}
              title={t("verso il pessimista: {price}", { price: fmt(s.low, "price", { currency: v.currency }) })} />}
            {s.high !== null && <i className="to-high" style={{ left: `${Math.min(pos(s.high), pos(mid))}%`, width: `${Math.abs(pos(s.high) - pos(mid))}%` }}
              title={t("verso l'ottimista: {price}", { price: fmt(s.high, "price", { currency: v.currency }) })} />}
            <b style={{ left: `${pos(mid)}%` }} />
          </span>
          <span className="vals">{fmt(s.low, "price", { digits: 0 })} – {fmt(s.high, "price", { digits: 0 })}</span>
        </div>
      ))}
      <div className="legend"><i className="to-low" /> {t("verso il pessimista")} <i className="to-high" /> {t("verso l'ottimista")}
        <span>· {t("linea = caso base {price}", { price: fmt(mid, "price", { currency: v.currency }) })}</span></div>
    </div>
  );
}

function Histogram({ v }: { v: Valuation }) {
  const h = v.simulation?.histogram;
  if (!h || !h.counts.length) return null;
  const max = Math.max(...h.counts);
  const x0 = h.edges[0];
  const x1 = h.edges[h.edges.length - 1];
  const W = 400;
  const H = 120;
  const sx = (x: number) => ((x - x0) / (x1 - x0 || 1)) * W;
  const price = v.price >= x0 && v.price <= x1 ? sx(v.price) : null;
  return (
    <figure className="hist">
      <svg viewBox={`0 0 ${W} ${H + 18}`} role="img" aria-label={t("Distribuzione dei valori simulati")}>
        {h.counts.map((c, i) => {
          const x = sx(h.edges[i]);
          const w = Math.max(sx(h.edges[i + 1]) - x - 1, 1);
          const bh = (c / (max || 1)) * H;
          return (
            <rect key={i} x={x} y={H - bh} width={w} height={bh} rx={1.5} className={h.edges[i + 1] <= v.price ? "below" : "above"}>
              <title>{t("{from} – {to}: {n} casi", { from: fmt(h.edges[i], "price", { digits: 0 }), to: fmt(h.edges[i + 1], "price", { digits: 0 }), n: c })}</title>
            </rect>
          );
        })}
        {price !== null && <line x1={price} x2={price} y1={0} y2={H} className="price" />}
        <text x={0} y={H + 14} className="ax">{fmt(x0, "price", { digits: 0 })}</text>
        <text x={W} y={H + 14} className="ax" textAnchor="end">{fmt(x1, "price", { digits: 0, currency: v.currency })}</text>
        {price !== null && <text x={price + 4} y={10} className="ax">{t("prezzo")}</text>}
      </svg>
      <figcaption>
        {t("Nel {pct} dei {n} casi il valore supera il prezzo di oggi; mediana {median}. Le leve variano in modo indipendente: nella realtà crescita e margini spesso si muovono insieme.",
          { pct: fmt(v.simulation?.prob_above_price, "pct", { digits: 0 }), n: fmt(v.simulation?.n, "int"), median: fmt(v.simulation?.p50, "price", { digits: 0, currency: v.currency }) })}
      </figcaption>
    </figure>
  );
}

export function ValuationPanel({ ticker }: { ticker: string }) {
  const qc = useQueryClient();
  const start = useQuery({
    queryKey: ["valuation", ticker],
    queryFn: () => api<Valuation>(`/companies/${encodeURIComponent(ticker)}/valuation`),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [probs, setProbs] = useState<Record<string, string>>({});
  const [story, setStory] = useState("");

  useEffect(() => {
    if (start.data && draft === null) {
      const d = draftFrom(start.data);
      setDraft(d.levers);
      setProbs(d.probs);
    }
  }, [start.data, draft]);

  const meta = start.data?.lever_meta ?? [];
  const scenarios = start.data ? SCENARIO_ORDER.filter((n) => n in start.data.levers) : [];

  // Corpo della richiesta: SOLO le celle cambiate rispetto alla partenza (le altre restano al valore esatto del
  // motore, non a quello arrotondato mostrato), nelle unità del modello; null se una cella non è un numero.
  const init = useMemo(() => (start.data ? draftFrom(start.data) : null), [start.data]);
  const body = useMemo(() => {
    if (!draft || !init) return null;
    const levers: Record<string, Record<string, number>> = {};
    for (const n of scenarios) {
      for (const m of meta) {
        const text = draft[n]?.[m.key] ?? "";
        const x = parse(text);
        if (x === null) return null;
        if (text !== init.levers[n][m.key]) (levers[n] ??= {})[m.key] = m.pct ? x / 100 : x;
      }
    }
    let probabilities: Record<string, number> | undefined;
    const changedProbs = scenarios.some((n) => (probs[n] ?? "") !== init.probs[n]);
    for (const n of scenarios) {
      const x = parse(probs[n] ?? "");
      if (x === null || x < 0) return null;
      if (changedProbs) (probabilities ??= {})[n] = x / 100;
    }
    return JSON.stringify({ levers, ...(probabilities ? { probabilities } : {}) });
  }, [draft, probs, init, meta, scenarios]);
  const initialBody = JSON.stringify({ levers: {} });

  const debounced = useDebounced(body, 350);
  const edited = debounced !== null && debounced !== initialBody;
  const recalc = useQuery({
    queryKey: ["valuation", ticker, debounced],
    queryFn: () => api<Valuation>(`/companies/${encodeURIComponent(ticker)}/valuation`, { method: "POST", body: debounced! }),
    enabled: edited,
    placeholderData: keepPreviousData,
    retry: false,
  });

  const save = useMutation({
    mutationFn: () => api<{ fair_value: number | null }>(`/companies/${encodeURIComponent(ticker)}/valuation/save`, {
      method: "POST", body: JSON.stringify({ ...JSON.parse(body ?? initialBody ?? "{}"), story }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["company", ticker.toUpperCase()] });
      qc.invalidateQueries({ queryKey: ["valuation", ticker] });
    },
  });

  if (start.isPending || (start.data && draft === null)) return <Skeleton height={320} />;
  if (start.isError) {
    const msg = start.error instanceof ApiError && start.error.status === 422 ? start.error.message : t("Errore: {message}", { message: start.error.message });
    return <Empty what={t("Valutazione non disponibile")} why={msg} />;
  }
  const v = edited && recalc.data ? recalc.data : start.data!;
  const cur = v.currency;
  const total = Object.values(v.probabilities).reduce((a, b) => a + b, 0) || 1;
  const margin = v.expected !== null && v.price ? v.expected / v.price - 1 : null;
  const checks = Object.entries(v.scenarios).flatMap(([n, r]) => r.checks.map((c) => ({ n, ...c })));
  const invalid = body === null;

  return (
    <div className="valuation">
      <Card title={t("Risultato della tua valutazione")} className="result-card"
        right={recalc.isFetching ? t("ricalcolo…") : v.saved && !edited ? t("riparte dall'ultima valutazione salvata") : t("ipotesi di partenza dai bilanci")}>
        <dl className="kv big">
          <div><dt>{t("Valore per azione")}</dt><dd>{fmt(v.expected, "price", { currency: cur })}</dd></div>
          <div><dt>{t("Margine sul prezzo")}</dt><dd><Num v={margin} kind="pct" change /></dd></div>
          {v.simulation && <div><dt>{t("Intervallo 10°–90°")}</dt><dd>{fmt(v.simulation.p10, "price", { digits: 0 })} – {fmt(v.simulation.p90, "price", { digits: 0, currency: cur })}</dd></div>}
          {v.simulation && <div><dt>{t("Sopra il prezzo")}</dt><dd>{fmt(v.simulation.prob_above_price, "pct", { digits: 0 })}</dd></div>}
          {scenarios.map((n) => (
            <div key={n}><dt>{t(n)} {fmt((v.probabilities[n] ?? 0) / total, "pct", { digits: 0 })}</dt><dd>{fmt(v.scenarios[n]?.per_share, "price", { currency: cur })}</dd></div>
          ))}
          <div><dt>{t("Prezzo")}</dt><dd>{fmt(v.price, "price", { currency: cur })}</dd></div>
          <div><dt>{t("Crescita implicita")}</dt><dd>{fmt(v.implied_growth, "pct")}</dd></div>
        </dl>
        <p className="honest">
          {t("Valore = media dei tre scenari pesata con le probabilità; intervallo dalla simulazione.")} {v.notes.join(" ")}{" "}
          {v.implied_growth !== null ? t("Crescita implicita: crescita dei ricavi (anni 1-5) che giustifica il prezzo di oggi.")
            : t("Crescita implicita: nessuna tra −10% e +60% giustifica il prezzo di oggi.")}
        </p>
      </Card>

      {(invalid || recalc.isError || checks.length > 0) && (
        <div style={{ display: "grid", gap: 6 }}>
          {invalid && <Notice tone="warn" title={t("Leve non valide:")}>{t("ogni cella deve contenere un numero (probabilità non negative).")}</Notice>}
          {recalc.isError && <Notice tone="neg" title={t("Ricalcolo non riuscito:")}>{recalc.error.message}</Notice>}
          {checks.map((c, i) => <Notice key={i} tone={c.level === "errore" ? "neg" : "warn"} title={`${t(c.n)}:`}>{c.message}</Notice>)}
        </div>
      )}

      <Card title={t("Le leve")} right={t("percentuali in %, il resto come numero")}>
        <div className="table-wrap">
          <table className="dense levers">
            <thead><tr><th>{t("Leva")}</th>{scenarios.map((n) => <th key={n} className="n">{t(n)}</th>)}</tr></thead>
            <tbody>
              {meta.map((m) => (
                <tr key={m.key}>
                  <td>{m.label}{m.pct ? " (%)" : ""}</td>
                  {scenarios.map((n) => {
                    const val = draft![n]?.[m.key] ?? "";
                    return (
                      <td key={n} className="n">
                        <input inputMode="decimal" value={val} aria-label={`${m.label}, ${t(n)}`} aria-invalid={parse(val) === null}
                          onChange={(e) => setDraft((d) => ({ ...d!, [n]: { ...d![n], [m.key]: e.target.value } }))} />
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr className="probs">
                <td>{t("Probabilità (%)")}</td>
                {scenarios.map((n) => (
                  <td key={n} className="n">
                    <input inputMode="decimal" value={probs[n] ?? ""} aria-label={t("Probabilità, {scenario}", { scenario: t(n) })} aria-invalid={parse(probs[n] ?? "") === null}
                      onChange={(e) => setProbs((p) => ({ ...p, [n]: e.target.value }))} />
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={() => { const d = draftFrom(start.data!); setDraft(d.levers); setProbs(d.probs); }}>
            {t("Torna alle ipotesi di partenza")}
          </button>
        </div>
        {v.setup_notes.length > 0 && <ul className="plain honest">{v.setup_notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      </Card>

      <div className="grid-2">
        <Card title={t("Leve che contano di più")} right={t("una alla volta dal caso base")}>
          <Tornado v={v} />
        </Card>
        <Card title={t("Simulazione")} right={v.simulation ? t("{n} valutazioni", { n: fmt(v.simulation.n, "int") }) : undefined}>
          {v.simulation ? <Histogram v={v} /> : <Empty what={t("Simulazione non disponibile")} why={t("Servono i tre scenari.")} />}
        </Card>
      </div>

      <Card title={t("È plausibile?")} right={t("il caso base confrontato con le aziende dello stesso settore")}>
        {v.plausibility.length === 0 ? <Empty what={t("Confronto non disponibile")} why={t("Meno di 5 aziende dello stesso settore con dati sufficienti.")} /> : (
          <div className="table-wrap">
            <table className="dense">
              <thead><tr><th>{t("Leva")}</th><th className="n">{t("La tua ipotesi")}</th><th className="n">{t("Settore 10°")}</th><th className="n">{t("Mediana")}</th><th className="n">{t("Settore 90°")}</th><th className="n">{t("Posizione")}</th><th>{t("Giudizio")}</th></tr></thead>
              <tbody>
                {v.plausibility.map((r) => {
                  const k = r.kind === "num" ? "num" : "pct";
                  return (
                    <tr key={r.lever}>
                      <td>{r.lever}</td>
                      <td className="n">{fmt(r.value, k)}</td><td className="n">{fmt(r.p10, k)}</td><td className="n">{fmt(r.p50, k)}</td>
                      <td className="n">{fmt(r.p90, k)}</td><td className="n">{fmt(r.percentile, "pct", { digits: 0 })}</td>
                      <td className="wrap">{r.verdict}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <details className="card details">
        <summary>{t("Anno per anno (caso base)")}</summary>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>{t("Anno")}</th><th className="n">{t("Crescita")}</th><th className="n">{t("Ricavi")}</th><th className="n">{t("Margine")}</th><th className="n">EBIT</th><th className="n">{t("Reinvestimento")}</th><th className="n">FCFF</th><th className="n">{t("Costo cap.")}</th><th className="n">{t("Valore attuale")}</th><th className="n">ROIC</th></tr></thead>
            <tbody>
              {v.year_table.map((r) => (
                <tr key={String(r.anno)}>
                  <td>{fmt(r.anno, "int")}</td><td className="n">{fmt(r.crescita, "pct")}</td><td className="n">{fmt(r.ricavi, "money")}</td>
                  <td className="n">{fmt(r.margine, "pct")}</td><td className="n">{fmt(r.ebit, "money")}</td><td className="n">{fmt(r.reinvestimento, "money")}</td>
                  <td className="n">{fmt(r.fcff, "money")}</td><td className="n">{fmt(r.costo_capitale, "pct")}</td>
                  <td className="n">{fmt(r.valore_attuale, "money")}</td><td className="n">{fmt(r.roic, "pct")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="honest">{t("Il {pct} del valore viene dal valore finale (dopo il decimo anno).", { pct: fmt(v.scenarios.Base?.terminal_share, "pct", { digits: 0 }) })}</p>
      </details>

      <Card title={t("Salva nel diario")} right={t("aggiorna anche il tuo fair value")}>
        <label className="field">
          <span>{t("La storia dietro i numeri")}</span>
          <textarea rows={3} value={story} onChange={(e) => setStory(e.target.value)} maxLength={5000}
            placeholder={t("Perché questa crescita, questo margine, questo rischio…")} />
        </label>
        <div className="actions">
          <button type="button" className="btn primary" disabled={invalid || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? t("Salvo…") : t("Salva la valutazione")}
          </button>
          {save.isSuccess && <span className="pos">{t("Salvata: fair value {price}.", { price: fmt(save.data.fair_value, "price", { currency: cur }) })}</span>}
          {save.isError && <span className="neg">{t("Non salvata: {message}", { message: save.error.message })}</span>}
        </div>
      </Card>
    </div>
  );
}
