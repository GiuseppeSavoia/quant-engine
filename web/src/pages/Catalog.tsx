// Catalogo dei componenti (criterio di "fatto" della fase 2): ogni pezzo del design system in un posto solo, da
// guardare in chiaro e in scuro prima di usarlo nelle pagine.
import { useState } from "react";
import { PillarRow, VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Notice, Num, Segmented, Skeleton } from "../components/ui";
import { t } from "../lib/i18n";

const COLORS = ["bg", "surface-1", "surface-2", "text", "text-2", "text-3", "accent", "pos", "neg", "warn", "trap", "info"];

export function CatalogPage() {
  const [seg, setSeg] = useState<"a" | "b" | "c">("a");
  return (
    <div className="page">
      <h1>{t("Componenti")}</h1>
      <p className="lead">{t("Il design system dell'interfaccia: colori, numeri, distintivi, avvisi, tabelle, giudizio.")}</p>
      <Card title={t("Colori")} right={t("token di ui/common.py")}>
        <div className="swatches">
          {COLORS.map((c) => <div className="swatch" key={c}><i style={{ background: `var(--${c})` }} />--{c}</div>)}
        </div>
      </Card>
      <div className="grid-2">
        <Card title={t("Numeri in italiano")}>
          <table className="dense"><tbody>
            <tr><td>{t("Prezzo")}</td><td className="n"><Num v={1950.731} kind="price" currency="USD" /></td></tr>
            <tr><td>{t("Capitalizzazione")}</td><td className="n"><Num v={6.63e10} kind="money" currency="EUR" /></td></tr>
            <tr><td>{t("Variazione")}</td><td className="n"><Num v={-0.0123} kind="pct" change digits={2} /></td></tr>
            <tr><td>{t("Multiplo")}</td><td className="n"><Num v={12.34} kind="mult" /></td></tr>
            <tr><td>{t("Differenza")}</td><td className="n"><Num v={0.012} kind="pp" /></td></tr>
            <tr><td>{t("Mancante")}</td><td className="n"><Num v={null} /></td></tr>
          </tbody></table>
        </Card>
        <Card title={t("Distintivi e controlli")}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
            {["candidata", "aspettare", "neutra", "qualita_dubbia", "evitare", "dati_insufficienti"].map((c) => <VerdictBadge key={c} category={c} />)}
            <Badge tone="info">{t("informazione")}</Badge><Badge tone="off">{t("spento")}</Badge>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <button className="btn primary" type="button">{t("Azione principale")}</button>
            <button className="btn" type="button">{t("Secondaria")}</button>
            <Segmented label={t("Esempio")} value={seg} onChange={setSeg}
              options={[{ value: "a", label: t("Uno") }, { value: "b", label: t("Due") }, { value: "c", label: t("Tre") }]} />
          </div>
        </Card>
      </div>
      <Card title={t("Avvisi e stati")}>
        <div style={{ display: "grid", gap: 8 }}>
          <Notice tone="info">{t("Informazione: le stime sono solo informazione, non entrano nel giudizio.")}</Notice>
          <Notice tone="warn" title={t("Da verificare:")}>{t("bilancio completato dalla fonte di riserva.")}</Notice>
          <Notice tone="neg" title={t("Allarme:")}>{t("Beneish M-score sopra la soglia.")}</Notice>
          <Notice tone="pos">{t("Soglie salvate.")}</Notice>
          <Empty what={t("Nessuna azienda seguita")} why={t("Cerca un'azienda e premi Segui.")} />
          <Skeleton height={20} width="60%" />
        </div>
      </Card>
      <Card title={t("Pilastri del giudizio")}>
        {[0.89, 0.55, 0.12].map((s, i) => (
          <PillarRow key={s} p={{ key: `p${i}`, label: t(["Qualità del business", "Prezzo", "Segnali d'allarme"][i]), score: s, mean: s,
            coverage: 1, grade: t(["Eccellente", "Prezzo pieno", "Molti allarmi"][i]), evidence: [], missing: [] }} />
        ))}
        <PillarRow p={{ key: "insiders", label: t("Chi è dentro"), score: null, mean: null, coverage: 0, grade: "", evidence: [], missing: [] }} />
      </Card>
    </div>
  );
}
