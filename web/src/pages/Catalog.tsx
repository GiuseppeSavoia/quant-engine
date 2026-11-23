// Catalogo dei componenti (criterio di "fatto" della fase 2): ogni pezzo del design system in un posto solo, da
// guardare in chiaro e in scuro prima di usarlo nelle pagine.
import { useState } from "react";
import { PillarRow, VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Notice, Num, Segmented, Skeleton } from "../components/ui";

const COLORS = ["bg", "surface-1", "surface-2", "text", "text-2", "text-3", "accent", "pos", "neg", "warn", "trap", "info"];

export function CatalogPage() {
  const [seg, setSeg] = useState<"a" | "b" | "c">("a");
  return (
    <div className="page">
      <h1>Componenti</h1>
      <p className="lead">Il design system dell'interfaccia: colori, numeri, distintivi, avvisi, tabelle, giudizio.</p>
      <Card title="Colori" right="token di ui/common.py">
        <div className="swatches">
          {COLORS.map((c) => <div className="swatch" key={c}><i style={{ background: `var(--${c})` }} />--{c}</div>)}
        </div>
      </Card>
      <div className="grid-2">
        <Card title="Numeri in italiano">
          <table className="dense"><tbody>
            <tr><td>Prezzo</td><td className="n"><Num v={1950.731} kind="price" currency="USD" /></td></tr>
            <tr><td>Capitalizzazione</td><td className="n"><Num v={6.63e10} kind="money" currency="EUR" /></td></tr>
            <tr><td>Variazione</td><td className="n"><Num v={-0.0123} kind="pct" change digits={2} /></td></tr>
            <tr><td>Multiplo</td><td className="n"><Num v={12.34} kind="mult" /></td></tr>
            <tr><td>Differenza</td><td className="n"><Num v={0.012} kind="pp" /></td></tr>
            <tr><td>Mancante</td><td className="n"><Num v={null} /></td></tr>
          </tbody></table>
        </Card>
        <Card title="Distintivi e controlli">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
            {["candidata", "aspettare", "neutra", "qualita_dubbia", "evitare", "dati_insufficienti"].map((c) => <VerdictBadge key={c} category={c} />)}
            <Badge tone="info">informazione</Badge><Badge tone="off">spento</Badge>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <button className="btn primary" type="button">Azione principale</button>
            <button className="btn" type="button">Secondaria</button>
            <Segmented label="Esempio" value={seg} onChange={setSeg}
              options={[{ value: "a", label: "Uno" }, { value: "b", label: "Due" }, { value: "c", label: "Tre" }]} />
          </div>
        </Card>
      </div>
      <Card title="Avvisi e stati">
        <div style={{ display: "grid", gap: 8 }}>
          <Notice tone="info">Informazione: le stime sono solo informazione, non entrano nel giudizio.</Notice>
          <Notice tone="warn" title="Da verificare:">bilancio completato dalla fonte di riserva.</Notice>
          <Notice tone="neg" title="Allarme:">Beneish M-score sopra la soglia.</Notice>
          <Notice tone="pos">Soglie salvate.</Notice>
          <Empty what="Nessuna azienda seguita" why="Cerca un'azienda e premi Segui." />
          <Skeleton height={20} width="60%" />
        </div>
      </Card>
      <Card title="Pilastri del giudizio">
        {[0.89, 0.55, 0.12].map((s, i) => (
          <PillarRow key={s} p={{ key: `p${i}`, label: ["Qualità del business", "Prezzo", "Segnali d'allarme"][i], score: s, mean: s,
            coverage: 1, grade: ["Eccellente", "Prezzo pieno", "Molti allarmi"][i], evidence: [], missing: [] }} />
        ))}
        <PillarRow p={{ key: "insiders", label: "Chi è dentro", score: null, mean: null, coverage: 0, grade: "", evidence: [], missing: [] }} />
      </Card>
    </div>
  );
}
