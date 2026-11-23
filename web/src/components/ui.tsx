// Pezzi di base del design system (stessi ruoli dei componenti di ui/components.py nella webapp Streamlit).
import type { ReactNode } from "react";
import { fmt, toneOf, type Kind } from "../lib/fmt";

export function Card({ title, right, children, className = "" }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title !== undefined && (
        <h2 className="card-title">
          <span>{title}</span>
          {right !== undefined && <span className="r">{right}</span>}
        </h2>
      )}
      {children}
    </section>
  );
}

export type Tone = "pos" | "neg" | "warn" | "trap" | "neutral" | "info" | "off";

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Notice({ tone = "info", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <div className={`notice ${tone}`} role={tone === "neg" ? "alert" : undefined}>
      <div>{title && <b className={tone}>{title} </b>}{children}</div>
    </div>
  );
}

export function Empty({ what, why, children }: { what: string; why?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{what}</strong>
      {why}
      {children}
    </div>
  );
}

export function Skeleton({ height = 18, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

/** Numero formattato in italiano; `change`: colore e freccia secondo il segno (mai solo il colore). */
export function Num({ v, kind = "num", change = false, ...opt }: {
  v: unknown; kind?: Kind; change?: boolean; digits?: number; currency?: string | null; signed?: boolean;
}) {
  const text = fmt(v, kind, { ...opt, arrow: change || undefined });
  if (text === "–") return <span className="na">–</span>;
  return change ? <span className={toneOf(v)}>{text}</span> : <>{text}</>;
}

export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
