// Editor delle condizioni sulle metriche (Esplora e costruttore delle strategie): righe metrica · condizione · valore
// nelle unità della metrica (percentuali in %, "tra i migliori" in quota). Il motore controlla di nuovo le regole: qui
// solo l'aiuto mentre si scrive.
import type { MetricMeta, Rule } from "../lib/api";
import { t } from "../lib/i18n";

export type RuleDraft = { id: number; metric: string; op: string; text: string };
export type Op = { value: string; label: string; rank: boolean };

export function parseNumber(text: string): number | null {
  const t = text.trim().replace(/\s|%/g, "");
  if (!t) return null;
  const x = Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t);
  return Number.isFinite(x) ? x : null;
}

export function unitOf(m: MetricMeta | undefined, op: string): [string, number] {
  if (!m) return ["", 1];
  if (op === "top_sector" || op === "top_index") return ["%", 100];
  return [m.unit, m.factor];
}

export function draftsFrom(rules: Rule[], metrics: Map<string, MetricMeta>, start: number): RuleDraft[] {
  return rules.map((r, i) => {
    const [, factor] = unitOf(metrics.get(r.metric), r.op);
    return { id: start + i, metric: r.metric, op: r.op, text: String(Number((r.value * factor).toPrecision(6))).replace(".", ",") };
  });
}

/** Regole valide nelle unità del motore; per le altre il motivo da mostrare accanto alla riga. */
export function parseDrafts(drafts: RuleDraft[], metrics: Map<string, MetricMeta>) {
  return drafts.map((r) => {
    const m = metrics.get(r.metric);
    const [, factor] = unitOf(m, r.op);
    const x = parseNumber(r.text);
    const rank = r.op === "top_sector" || r.op === "top_index";
    let error = "";
    if (!m) error = t("scegli una metrica");
    else if (x === null) error = t("scrivi un numero");
    else if (rank && !m.better) error = t("questa metrica non ha un verso migliore: niente classifica");
    else if (rank && !(x > 0 && x <= 100)) error = t("la quota dei migliori va tra 0 e 100%");
    return { draft: r, error, rule: error ? null : ({ metric: r.metric, op: r.op, value: (x as number) / factor } as Rule) };
  });
}

/** Metriche raggruppate per gruppo, nell'ordine del catalogo. */
export function groupMetrics(metrics: MetricMeta[]): [string, MetricMeta[]][] {
  const g = new Map<string, MetricMeta[]>();
  for (const m of metrics) g.set(m.group, [...(g.get(m.group) ?? []), m]);
  return [...g.entries()];
}

export function RuleRows({ drafts, setDrafts, metrics, groups, ops }: {
  drafts: RuleDraft[]; setDrafts: (d: RuleDraft[]) => void; metrics: Map<string, MetricMeta>;
  groups: [string, MetricMeta[]][]; ops: Op[];
}) {
  return (
    <>
      {parseDrafts(drafts, metrics).map(({ draft, error }) => {
        const m = metrics.get(draft.metric);
        const [unit] = unitOf(m, draft.op);
        const set = (patch: Partial<RuleDraft>) => setDrafts(drafts.map((x) => (x.id === draft.id ? { ...x, ...patch } : x)));
        return (
          <div key={draft.id} className="rule">
            <select className="select" value={draft.metric} onChange={(e) => set({ metric: e.target.value })} aria-label={t("Metrica")}>
              {groups.map(([g, ms]) => <optgroup key={g} label={g}>{ms.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</optgroup>)}
            </select>
            <select className="select" value={draft.op} onChange={(e) => set({ op: e.target.value })} aria-label={t("Condizione")}>
              {ops.map((o) => <option key={o.value} value={o.value} disabled={o.rank && !m?.better}>{o.label}</option>)}
            </select>
            <input className="num-input" inputMode="decimal" value={draft.text} placeholder={t("valore")} aria-label={t("Valore")}
              aria-invalid={!!error && draft.text !== ""} onChange={(e) => set({ text: e.target.value })} />
            <span className="unit">{unit}</span>
            <button type="button" className="btn ghost" aria-label={t("Togli questa condizione")} onClick={() => setDrafts(drafts.filter((x) => x.id !== draft.id))}>✕</button>
            {error && draft.text !== "" && <span className="warn small">{error}</span>}
          </div>
        );
      })}
    </>
  );
}
