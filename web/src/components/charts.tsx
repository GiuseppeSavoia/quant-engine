// Grafici del design system in SVG, senza librerie: barre raggruppate e linee per periodo.
// Regole (skill dataviz): colori di serie in ordine fisso (--series-1...), segni sottili, barre con 2 px di spazio
// e angoli arrotondati verso il valore, zero sempre visibile (asse onesto), griglia appena visibile, legenda per 2+
// serie, dettaglio al passaggio del mouse (e da tastiera: frecce sul grafico) con tutti i valori del periodo.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { fmt } from "../lib/fmt";

export interface Series { key: string; label: string; values: (number | null)[] }
type Kind = "money" | "pct" | "shares" | "num" | "mult";

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Estremi e tacche "tonde" (1, 2, 2,5, 5 × 10^n) che includono sempre lo zero. */
function niceScale(values: number[], ticks = 4, zero = true): { lo: number; hi: number; step: number } {
  let lo = zero ? Math.min(0, ...values) : Math.min(...values);
  let hi = zero ? Math.max(0, ...values) : Math.max(...values);
  if (lo === hi) hi = lo + 1;
  const raw = (hi - lo) / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  return { lo, hi, step };
}

/** Tacche di una scala logaritmica: 1, 2, 5 × 10^n (più fitte se l'intervallo è stretto, solo potenze di 10 se è
 * molto largo). */
function logTicks(min: number, max: number): number[] {
  const pick = (ms: number[]) => {
    const out: number[] = [];
    for (let k = Math.floor(Math.log10(min)); k <= Math.ceil(Math.log10(max)); k++)
      for (const m of ms) { const v = Number((m * 10 ** k).toPrecision(12)); if (v >= min && v <= max) out.push(v); }
    return out;
  };
  let t = pick([1, 2, 5]);
  if (t.length < 3) t = pick([1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9]);
  if (t.length > 7) t = pick([1]);
  return t;
}

const tickText = (v: number, kind: Kind) =>
  kind === "pct" ? fmt(v, "pct", { digits: 0 }) : kind === "shares" ? fmt(v / 1e6, "num", { digits: 0 })
    : kind === "num" ? fmt(v, "num", { digits: Math.abs(v) < 10 ? 1 : 0 }) : kind === "mult" ? fmt(v, "mult", { digits: 0 })
    : fmt(v, "money", { digits: 0 });
const valueText = (v: number | null, kind: Kind, currency?: string) =>
  kind === "pct" ? fmt(v, "pct") : kind === "shares" ? `${fmt(v === null ? null : v / 1e6, "num", { digits: 1 })} mln`
    : kind === "num" ? fmt(v, "num") : kind === "mult" ? fmt(v, "mult") : fmt(v, "money", { currency });

export function Legend({ series, colors }: { series: Series[]; colors?: number[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {series.map((s, i) => <span key={s.key}><i style={{ background: `var(--series-${(colors?.[i] ?? i) + 1})` }} />{s.label}</span>)}
    </div>
  );
}

export function PeriodChart({ labels, series, kind, type = "bars", currency, height = 240, title, zero = true, colors, log = false, marker }: {
  labels: string[]; series: Series[]; kind: Kind; type?: "bars" | "lines"; currency?: string; height?: number; title: string;
  /** asse da zero (sempre per le barre; per le linee di prezzi normalizzati o multipli si può togliere) */
  zero?: boolean;
  /** indice del colore di ogni serie (per tenere lo stesso colore a un'azienda in più grafici) */
  colors?: number[];
  /** scala logaritmica (solo linee di valori positivi, es. valore di un portafoglio con 1 = inizio) */
  log?: boolean;
  /** indice di un periodo da segnare con una linea verticale tratteggiata (es. la metà di un backtest) */
  marker?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 56, r: 8, t: 8, b: 26 };
  const isLog = log && type === "lines";
  const values = series.flatMap((s) => s.values).filter((v): v is number => v !== null && Number.isFinite(v) && (!isLog || v > 0));
  let lo: number, hi: number;
  const ticks: number[] = [];
  if (isLog) {
    const vmin = values.length ? Math.min(...values) : 1;
    const vmax = values.length ? Math.max(...values) : 10;
    const span = Math.max(Math.log10(vmax) - Math.log10(vmin), 0.05);
    lo = Math.log10(vmin) - span * 0.04;
    hi = Math.log10(vmax) + span * 0.04;
    ticks.push(...logTicks(10 ** lo, 10 ** hi));
  } else {
    const sc = niceScale(values.length ? values : [0], 4, type === "bars" || zero);
    lo = sc.lo;
    hi = sc.hi;
    for (let v = lo; v <= hi + sc.step / 2; v += sc.step) ticks.push(Number(v.toPrecision(12)));
  }
  const color = (si: number) => `var(--series-${(colors?.[si] ?? si) + 1})`;
  const W = width;
  const H = height;
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const n = labels.length;
  const band = iw / Math.max(n, 1);
  const y = (v: number) => pad.t + ((hi - (isLog ? Math.log10(v) : v)) / (hi - lo)) * ih;
  const x = (i: number) => pad.l + band * i + band / 2;
  const every = Math.ceil(n / Math.max(1, Math.floor(iw / 64))); // etichette dei periodi senza sovrapporsi
  const groupW = Math.min(band * 0.78, 18 * series.length + 2 * (series.length - 1));
  const barW = Math.max(2, (groupW - 2 * (series.length - 1)) / Math.max(series.length, 1));

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") setHover((h) => Math.min((h ?? -1) + 1, n - 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max((h ?? n) - 1, 0));
    else if (e.key === "Escape") setHover(null);
  };

  return (
    <div className="chart" ref={ref}>
      <Legend series={series} colors={colors} />
      <svg width={W} height={H} role="img" className={n > 40 ? "dense-x" : undefined} aria-label={`${title}: ${series.map((s) => s.label).join(", ")}`}
        tabIndex={0} onKeyDown={onKey} onMouseLeave={() => setHover(null)} onBlur={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className={t === 0 && !isLog ? "zero" : "grid"} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" className="ax">{tickText(t, kind)}</text>
          </g>
        ))}
        {marker !== undefined && marker >= 0 && marker < n && (
          <line x1={x(marker)} x2={x(marker)} y1={pad.t} y2={pad.t + ih} className="marker" />
        )}
        {hover !== null && <rect x={pad.l + band * hover} y={pad.t} width={band} height={ih} className="hover-band" />}
        {type === "bars" && series.map((s, si) => s.values.map((v, i) => {
          if (v === null || !Number.isFinite(v)) return null;
          const bx = x(i) - groupW / 2 + si * (barW + 2);
          const y0 = y(0);
          const y1 = y(v);
          const h = Math.max(Math.abs(y1 - y0), 1);
          const top = Math.min(y0, y1);
          const r = Math.min(3, barW / 2, h);
          // angoli arrotondati solo verso il valore (la base resta piatta sullo zero)
          const d = v >= 0
            ? `M${bx},${top + h} V${top + r} Q${bx},${top} ${bx + r},${top} H${bx + barW - r} Q${bx + barW},${top} ${bx + barW},${top + r} V${top + h} Z`
            : `M${bx},${top} V${top + h - r} Q${bx},${top + h} ${bx + r},${top + h} H${bx + barW - r} Q${bx + barW},${top + h} ${bx + barW},${top + h - r} V${top} Z`;
          return <path key={`${s.key}-${i}`} d={d} style={{ fill: color(si) }} opacity={hover === null || hover === i ? 1 : 0.45} />;
        }))}
        {type === "lines" && series.map((s, si) => {
          const segs: string[] = [];
          let cur = "";
          s.values.forEach((v, i) => {
            if (v === null || !Number.isFinite(v) || (isLog && v <= 0)) { if (cur) segs.push(cur); cur = ""; return; }
            cur += `${cur ? "L" : "M"}${x(i)},${y(v)} `;
          });
          if (cur) segs.push(cur);
          return (
            <g key={s.key} style={{ color: color(si) }}>
              {segs.map((d, k) => <path key={k} d={d} className="line" />)}
              {s.values.map((v, i) => v === null || !Number.isFinite(v) || (isLog && v <= 0) || (n > 40 && hover !== i) ? null :
                <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 5 : 3.5} className="dot" />)}
            </g>
          );
        })}
        {labels.map((l, i) => (n - 1 - i) % every === 0 ? ( // l'ultimo periodo ha sempre l'etichetta
          <text key={l} x={x(i)} y={H - 8} textAnchor="middle" className="ax">{l}</text>
        ) : null)}
        {labels.map((l, i) => (
          <rect key={`hit-${l}`} x={pad.l + band * i} y={pad.t} width={band} height={ih + pad.b} fill="transparent"
            onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: Math.min(Math.max(x(hover) - 90, 0), W - 190), top: 30 }} role="status">
          <b>{labels[hover]}</b>
          {series.map((s, si) => (
            <span key={s.key}><i style={{ background: color(si) }} />{s.label}<em>{valueText(s.values[hover], kind, currency)}</em></span>
          ))}
        </div>
      )}
    </div>
  );
}
