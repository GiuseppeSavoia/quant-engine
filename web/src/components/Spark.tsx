// Minigrafico dell'ultimo anno (chiusure settimanali): una linea sottile, verde se l'anno chiude in rialzo, rossa se
// in ribasso; niente assi (il numero accanto dice quanto). Accessibile: l'etichetta riporta la variazione.
import { fmt } from "../lib/fmt";
import { t } from "../lib/i18n";

export function Spark({ values, width = 72, height = 20 }: { values?: (number | null)[] | null; width?: number; height?: number }) {
  const v = (values ?? []).filter((x): x is number => x !== null && Number.isFinite(x));
  if (v.length < 2) return <span className="na">–</span>;
  const lo = Math.min(...v), hi = Math.max(...v);
  const span = hi - lo || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * (width - 2) + 1).toFixed(1)},${(height - 1 - ((x - lo) / span) * (height - 2)).toFixed(1)}`);
  const chg = v[v.length - 1] / v[0] - 1;
  return (
    <svg className={`spark ${chg >= 0 ? "up" : "down"}`} width={width} height={height} role="img" aria-label={t("Un anno: {chg}", { chg: fmt(chg, "pct", { signed: true }) })}>
      <polyline points={pts.join(" ")} fill="none" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
