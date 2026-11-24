// Funzione "Grafico" della scheda (GP): prezzo a candele o linea con medie mobili, volumi, le tue linee di riferimento
// (fair value, soglie, scenari), i depositi dei 10-K e le operazioni del backtest; confronto con il benchmark in base
// 100; drawdown dal massimo. SVG fatto in casa come gli altri grafici: asse sulle sedute vere (niente fine settimana),
// dettaglio di ogni barra al passaggio del mouse e con le frecce della tastiera. Dati pronti dall'API.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Card, Empty, Segmented, Skeleton } from "../components/ui";
import { api, type PriceChart } from "../lib/api";
import { fmt } from "../lib/fmt";
import { t } from "../lib/i18n";

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const finite = (v: number | null | undefined): v is number => v !== null && v !== undefined && Number.isFinite(v);

function ticks(lo: number, hi: number, n = 5, log = false): number[] {
  if (log) {
    const out: number[] = [];
    for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
      for (const m of [1, 2, 5]) { const v = m * 10 ** e; if (v >= lo && v <= hi) out.push(v); }
    }
    return out.length >= 2 ? out : [lo, hi];
  }
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function Chart({ c, ma, log, showFilings, showTrades }: { c: PriceChart; ma: boolean; log: boolean; showFilings: boolean; showTrades: boolean }) {
  const [ref, W] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const bench = !!c.bench;
  const n = c.dates.length;
  const pad = { l: 60, r: 12, t: 10 };
  const H1 = 340;
  const H2 = bench ? 0 : 90; // nel confronto niente volumi
  const gap = bench ? 0 : 18;
  const iw = W - pad.l - pad.r;
  const band = iw / Math.max(n, 1);
  const x = (i: number) => pad.l + band * i + band / 2;
  const index = useMemo(() => new Map(c.dates.map((d, i) => [d.slice(0, 10), i])), [c.dates]);
  const nearest = (iso: string) => { // indice della barra che contiene la data (barre aggregate: la prima dopo)
    const key = iso.slice(0, 10);
    if (index.has(key)) return index.get(key)!;
    const i = c.dates.findIndex((d) => d.slice(0, 10) >= key);
    return i >= 0 ? i : null;
  };
  const series: (number | null)[][] = bench ? [c.bench!.stock, c.bench!.benchmark]
    : [c.close, ...(c.candles ? [c.high, c.low] : []), ...(ma ? [c.sma50, c.sma200] : []),
       c.references.filter((r) => r.in_range).map((r) => r.value)];
  const vals = series.flat().filter(finite).filter((v) => !log || v > 0);
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const padY = (hi - lo) * 0.04 || 1;
  lo = log ? lo * 0.97 : lo - padY;
  hi = log ? hi * 1.03 : hi + padY;
  const tr = (v: number) => (log ? Math.log(v) : v);
  const y = (v: number) => pad.t + ((tr(hi) - tr(v)) / (tr(hi) - tr(lo))) * H1;
  const vmax = Math.max(...c.volume.filter(finite), 1);
  const vy0 = pad.t + H1 + gap;
  const vy = (v: number) => vy0 + H2 - (v / vmax) * H2;
  const path = (vs: (number | null)[]) => {
    let d = "";
    let open = false;
    vs.forEach((v, i) => {
      if (!finite(v) || (log && v <= 0)) { open = false; return; }
      d += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      open = true;
    });
    return d;
  };
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(iw / 80))));
  const label = (iso: string) => (c.freq === "M" || n > 400 ? fmt(iso, "date_short").slice(3) : fmt(iso, "date_short"));
  const up = (i: number) => (c.candles && finite(c.open[i]) && finite(c.close[i]) ? c.close[i]! >= c.open[i]! : (c.change[i] ?? 0) >= 0);
  const bodyW = Math.max(1, Math.min(band * 0.7, 12));
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") setHover((h) => Math.min((h ?? -1) + 1, n - 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max((h ?? n) - 1, 0));
    else if (e.key === "Escape") setHover(null);
  };
  const H = vy0 + H2 + 24;
  const h = hover ?? n - 1;
  const cur = c.currency;

  return (
    <div className="chart price" ref={ref}>
      <div className="legend">
        {bench ? <><span><i style={{ background: "var(--series-1)" }} />{c.ticker}</span><span><i style={{ background: "var(--series-2)" }} />{c.benchmark}</span></> : <>
          <span><i style={{ background: "var(--series-1)" }} />{c.candles ? t("Candele (una per {bar})", { bar: c.bar.one }) : t("Chiusura")}</span>
          {ma && <><span><i style={{ background: "var(--series-2)" }} />{t("Media 50 sedute")}</span><span><i style={{ background: "var(--series-3)" }} />{t("Media 200 sedute")}</span></>}
          {showFilings && c.filings.length > 0 && <span><i className="dashed" />{t("Deposito 10-K")}</span>}
          {showTrades && c.trades.length > 0 && <span>▲ ▼ {t("operazioni del backtest")}</span>}
        </>}
      </div>
      <svg width={W} height={H} role="img" tabIndex={0} onKeyDown={onKey} onMouseLeave={() => setHover(null)}
        aria-label={bench ? t("Prezzo di {ticker} contro {benchmark}", { ticker: c.ticker, benchmark: c.benchmark }) : t("Prezzo di {ticker}", { ticker: c.ticker })}>
        {ticks(lo, hi, 5, log).map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" className="ax">{bench ? fmt(t, "num", { digits: 0 }) : fmt(t, "price", { digits: t >= 1000 ? 0 : 2 })}</text>
          </g>
        ))}
        {hover !== null && <rect x={pad.l + band * hover} y={pad.t} width={Math.max(band, 1)} height={vy0 + H2 - pad.t} className="hover-band" />}
        {!bench && showFilings && c.filings.map((f) => { const i = nearest(f); return i === null ? null : <line key={f} x1={x(i)} x2={x(i)} y1={pad.t} y2={pad.t + H1} className="filing" />; })}
        {!bench && c.references.filter((r) => r.in_range && (!log || r.value > 0)).map((r) => (
          <g key={r.label} className={`ref ${r.style}`}>
            <line x1={pad.l} x2={W - pad.r} y1={y(r.value)} y2={y(r.value)} />
            <text x={pad.l + 6} y={y(r.value) - 4}>{r.label} {fmt(r.value, "price")}</text>
          </g>
        ))}
        {bench ? <>
          <path d={path(c.bench!.stock)} className="line s1" />
          <path d={path(c.bench!.benchmark)} className="line s2" />
        </> : c.candles ? c.dates.map((_, i) => {
          if (![c.open[i], c.high[i], c.low[i], c.close[i]].every(finite)) return null;
          const o = c.open[i]!; const cl = c.close[i]!;
          const top = y(Math.max(o, cl)); const bot = y(Math.min(o, cl));
          return (
            <g key={i} className={up(i) ? "up" : "down"}>
              <line x1={x(i)} x2={x(i)} y1={y(c.high[i]!)} y2={y(c.low[i]!)} />
              <rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={Math.max(bot - top, 1)} rx={1} />
            </g>
          );
        }) : <path d={path(c.close)} className="line s1" />}
        {!bench && ma && <><path d={path(c.sma50)} className="line thin s2" /><path d={path(c.sma200)} className="line thin s3" /></>}
        {!bench && showTrades && c.trades.map((trd, k) => {
          const i = nearest(trd.date);
          if (i === null || !finite(trd.price) || (log && trd.price <= 0)) return null;
          const cx = x(i); const cy = y(trd.price);
          const buy = trd.kind === "BUY";
          const d = buy ? `M${cx},${cy - 7} l6,10 h-12 Z` : `M${cx},${cy + 7} l6,-10 h-12 Z`;
          return <path key={k} d={d} className={`trade ${trd.kind}`}><title>{t("{label}: {shares} azioni a {price}", { label: trd.label, shares: fmt(trd.shares, "num", { digits: 1 }), price: fmt(trd.price, "price") })}</title></path>;
        })}
        {/* volumi */}
        {!bench && c.volume.map((v, i) => finite(v) ? (
          <rect key={`v${i}`} x={x(i) - bodyW / 2} y={vy(v)} width={bodyW} height={Math.max(vy0 + H2 - vy(v), 0.5)} className={up(i) ? "vol up" : "vol down"} />
        ) : null)}
        {!bench && <path d={c.vol_ma.map((v, i) => (finite(v) ? `${i && finite(c.vol_ma[i - 1]) ? "L" : "M"}${x(i)},${vy(v)}` : "")).join(" ")} className="line thin volma" />}
        {!bench && <text x={pad.l - 8} y={vy0 + 10} textAnchor="end" className="ax">{t("Volume")}</text>}
        {c.dates.map((d, i) => ((n - 1 - i) % every === 0 ? <text key={d} x={x(i)} y={H - 6} textAnchor="middle" className="ax">{label(d)}</text> : null))}
        {c.dates.map((d, i) => <rect key={`h${d}`} x={pad.l + band * i} y={pad.t} width={Math.max(band, 1)} height={vy0 + H2 - pad.t} fill="transparent" onMouseEnter={() => setHover(i)} />)}
      </svg>
      <div className="tip static" role="status">
        <b>{fmt(c.dates[h], "date")}{c.freq !== "D" ? ` (${c.bar.one})` : ""}</b>
        {bench ? <>
          <span>{c.ticker}<em>{fmt(c.bench!.stock[h], "num", { digits: 1 })}</em></span>
          <span>{c.benchmark}<em>{fmt(c.bench!.benchmark[h], "num", { digits: 1 })}</em></span>
        </> : <>
          {c.candles && <span>{t("Apertura")}<em>{fmt(c.open[h], "price")}</em></span>}
          {c.candles && <span>{t("Massimo")}<em>{fmt(c.high[h], "price")}</em></span>}
          {c.candles && <span>{t("Minimo")}<em>{fmt(c.low[h], "price")}</em></span>}
          <span>{t("Chiusura")}<em>{fmt(c.close[h], "price", { currency: cur })}</em></span>
          <span>{t("Variazione")}<em className={(c.change[h] ?? 0) >= 0 ? "pos" : "neg"}>{fmt(c.change[h], "pct", { digits: 2, arrow: true })}</em></span>
          <span>{t("Volume")}<em>{fmt(c.volume[h], "int")}</em></span>
        </>}
      </div>
    </div>
  );
}

function Drawdown({ c }: { c: PriceChart }) {
  const [ref, W] = useWidth();
  const n = c.drawdown.length;
  const H = 110;
  const pad = { l: 60, r: 12, t: 6 };
  const vals = c.drawdown.filter(finite);
  const lo = Math.min(...vals, -0.01);
  const band = (W - pad.l - pad.r) / Math.max(n, 1);
  const x = (i: number) => pad.l + band * i + band / 2;
  const y = (v: number) => pad.t + (v / lo) * (H - pad.t - 8);
  const pts = c.drawdown.map((v, i) => (finite(v) ? `${x(i)},${y(v)}` : null)).filter(Boolean);
  const area = pts.length ? `M${x(0)},${y(0)} L${pts.join(" L")} L${x(n - 1)},${y(0)} Z` : "";
  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={H} role="img" aria-label={t("Distanza dal massimo del periodo")}>
        {[0, lo / 2, lo].map((t) => (
          <g key={t}><line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className={t === 0 ? "zero" : "grid"} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" className="ax">{fmt(t, "pct", { digits: 0 })}</text></g>
        ))}
        <path d={area} className="dd" />
      </svg>
    </div>
  );
}

export function PricePanel({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState("1A");
  const [style, setStyle] = useState("Auto");
  const [ma, setMa] = useState(true);
  const [log, setLog] = useState(false);
  const [bench, setBench] = useState(false);
  const [filings, setFilings] = useState(true);
  const [trades, setTrades] = useState(true);
  const q = useQuery({
    queryKey: ["price", ticker, period, style, bench],
    queryFn: () => api<PriceChart>(`/companies/${encodeURIComponent(ticker)}/price?period=${period}&style=${style}&bench=${bench}`),
    placeholderData: keepPreviousData,
  });
  if (q.isPending) return <Skeleton height={420} />;
  if (q.isError) return <Empty what={t("Grafico non disponibile")} why={q.error.message} />;
  const c = q.data;
  if (c.empty) return <Empty what={t("Nessun prezzo per questa azienda")} why={t("I prezzi arrivano con l'aggiornamento dei dati.")} />;
  const s = c.summary;
  const toggle = (label: string, on: boolean, set: (v: boolean) => void, disabled = false, help?: string) => (
    <label className="check" title={help}><input type="checkbox" checked={on && !disabled} disabled={disabled} onChange={(e) => set(e.target.checked)} />{label}</label>
  );
  return (
    <div className="financials">
      <div className="toolbar">
        <Segmented label={t("Periodo")} value={period} onChange={setPeriod} options={c.periods.map((p) => ({ value: p, label: t(p) }))} />
        <Segmented label={t("Tipo di grafico")} value={style} onChange={setStyle} options={c.styles.map((p) => ({ value: p, label: t(p) }))} />
        {toggle(t("Medie"), ma, setMa, bench, t("Medie mobili delle chiusure a 50 e 200 sedute"))}
        {toggle(t("Log"), log, setLog, false, t("Scala logaritmica del prezzo"))}
        {toggle(t("vs {benchmark}", { benchmark: c.benchmark || t("indice") }), bench, setBench, !c.benchmark, t("Confronta con {benchmark}, base 100 all'inizio del periodo", { benchmark: c.benchmark }))}
        {toggle(t("Operazioni"), trades, setTrades, bench, t("Acquisti, vendite, stop-loss e take-profit del backtest"))}
        {toggle("10-K", filings, setFilings, bench, t("Date di deposito dei bilanci annuali alla SEC"))}
        {q.isFetching && <span className="honest">{t("aggiorno…")}</span>}
      </div>
      <div className="kpis">
        <div><dt>{t("Rendimento")}</dt><dd className={s.return >= 0 ? "pos" : "neg"}>{fmt(s.return, "pct", { signed: true })}</dd></div>
        <div title={t("il {date}", { date: fmt(s.high_date, "date") })}><dt>{t("Massimo")}</dt><dd>{fmt(s.high, "price", { currency: c.currency })}</dd></div>
        <div title={t("il {date}", { date: fmt(s.low_date, "date") })}><dt>{t("Minimo")}</dt><dd>{fmt(s.low, "price", { currency: c.currency })}</dd></div>
        <div title={t("Deviazione standard dei rendimenti giornalieri (logaritmici) del periodo × √252")}><dt>{t("Volatilità annua")}</dt><dd>{fmt(s.volatility, "pct")}</dd></div>
        <div title={t("Ultima chiusura rispetto al massimo del periodo")}><dt>{t("Dal massimo")}</dt><dd>{fmt(s.from_high, "pct", { signed: true })}</dd></div>
        <div title={t("Peggior discesa delle chiusure dal massimo precedente, nel periodo")}><dt>{t("Drawdown massimo")}</dt><dd className="neg">{fmt(s.max_drawdown, "pct")}</dd></div>
      </div>
      <Card title={bench ? t("{ticker} contro {benchmark}", { ticker: c.ticker, benchmark: c.benchmark }) : t("Prezzo e volumi")} right={bench ? t("base 100 all'inizio del periodo") : c.currency}>
        <Chart c={c} ma={ma && !bench} log={log} showFilings={filings && !bench} showTrades={trades && !bench} />
        <p className="honest">{c.notes.filter((t) => !bench || !t.startsWith("Volumi")).join(" · ")}.</p>
      </Card>
      <Card title={t("Dal massimo")} right={t("distanza dal massimo del periodo")}>
        <Drawdown c={c} />
      </Card>
    </div>
  );
}
