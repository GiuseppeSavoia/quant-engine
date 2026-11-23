// Azioni della scheda: segui, soglie di prezzo (avvisi PX- e PX+), fase nella pipeline (con cosa manca), esporta.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { API, api, type CompanyPipeline, type Header } from "../lib/api";
import { fmt } from "../lib/fmt";

const num = (t: string): number | null => {
  const v = t.trim().replace(/\s/g, "");
  if (!v) return null;
  const x = Number(v.includes(",") ? v.replace(/\./g, "").replace(",", ".") : v);
  return Number.isFinite(x) && x > 0 ? x : null;
};
const txt = (v: number | null | undefined) => (v ? String(v).replace(".", ",") : "");

function Targets({ h, done }: { h: Header; done: () => void }) {
  const qc = useQueryClient();
  const [buy, setBuy] = useState(txt(h.price_target?.buy_below));
  const [sell, setSell] = useState(txt(h.price_target?.sell_above));
  const refresh = () => { qc.invalidateQueries({ queryKey: ["company", h.ticker] }); qc.invalidateQueries({ queryKey: ["my-companies"] }); done(); };
  const save = useMutation({
    mutationFn: () => api(`/companies/${encodeURIComponent(h.ticker)}/price-target`, { method: "PUT", body: JSON.stringify({ buy_below: num(buy), sell_above: num(sell) }) }),
    onSuccess: refresh,
  });
  const clear = useMutation({ mutationFn: () => api(`/companies/${encodeURIComponent(h.ticker)}/price-target`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <div className="menu" style={{ minWidth: 280 }}>
      <label className="field"><span>Compra sotto ({h.currency})</span><input className="text-input" inputMode="decimal" value={buy} onChange={(e) => setBuy(e.target.value)} placeholder="es. 150" /></label>
      <label className="field"><span>Vendi sopra ({h.currency})</span><input className="text-input" inputMode="decimal" value={sell} onChange={(e) => setSell(e.target.value)} placeholder="es. 220" /></label>
      <p className="honest" style={{ margin: "4px 0" }}>Prezzo di oggi {fmt(h.price, "price", { currency: h.currency })}. Gli avvisi arrivano in Oggi e con la notifica dell'aggiornamento serale.</p>
      <div className="row">
        <button type="button" className="btn primary" disabled={(!num(buy) && !num(sell)) || save.isPending} onClick={() => save.mutate()}>Salva</button>
        {(h.price_target?.buy_below || h.price_target?.sell_above) && <button type="button" className="btn ghost" onClick={() => clear.mutate()}>Togli le soglie</button>}
      </div>
      {save.isError && <span className="neg small">{save.error.message}</span>}
    </div>
  );
}

function PipelineMenu({ ticker, info, done }: { ticker: string; info: CompanyPipeline; done: () => void }) {
  const qc = useQueryClient();
  const [stage, setStage] = useState(info.stage ?? "idea");
  const [note, setNote] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["company-pipeline", ticker] }); qc.invalidateQueries({ queryKey: ["pipeline"] }); done(); };
  const save = useMutation({
    mutationFn: () => api(`/companies/${encodeURIComponent(ticker)}/pipeline`, { method: "PUT", body: JSON.stringify({ stage, note: note || null }) }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => api(`/companies/${encodeURIComponent(ticker)}/pipeline`, { method: "DELETE" }), onSuccess: refresh });
  const missing = info.stages.find((s) => s.key === stage)?.missing ?? [];
  return (
    <div className="menu" style={{ minWidth: 300 }}>
      <fieldset><legend>Fase</legend>
        {info.stages.map((s) => (
          <label key={s.key} className="check"><input type="radio" name={`fase-${ticker}`} checked={stage === s.key} onChange={() => setStage(s.key)} />{s.label}</label>
        ))}
      </fieldset>
      <input className="text-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nota (es. idea da un 13F)" aria-label="Nota" />
      {missing.length > 0 && <p className="honest warn" style={{ margin: "4px 0" }}>Per questa fase manca: {missing.join(", ")}.</p>}
      <div className="row">
        <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>Salva la fase</button>
        {info.stage && <button type="button" className="btn ghost" onClick={() => remove.mutate()}>Togli dalla pipeline</button>}
      </div>
    </div>
  );
}

export function CompanyActions({ h }: { h: Header }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const pipe = useQuery({ queryKey: ["company-pipeline", h.ticker], queryFn: () => api<CompanyPipeline>(`/companies/${encodeURIComponent(h.ticker)}/pipeline`) });
  const follow = useMutation({
    mutationFn: () => api(`/watchlist/${encodeURIComponent(h.ticker)}`, { method: h.followed ? "DELETE" : "PUT" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["company", h.ticker] }); qc.invalidateQueries({ queryKey: ["my-companies"] }); qc.invalidateQueries({ queryKey: ["today"] }); },
  });
  const toggle = (k: string) => (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    const isOpen = (e.currentTarget as HTMLDetailsElement).open;
    setOpen(isOpen ? k : open === k ? null : open);
  };
  const t = h.price_target ?? {};
  const targetLabel = t.buy_below || t.sell_above
    ? `Soglie: ${t.buy_below ? `< ${fmt(t.buy_below, "price")}` : ""}${t.buy_below && t.sell_above ? " · " : ""}${t.sell_above ? `> ${fmt(t.sell_above, "price")}` : ""}`
    : "Soglie di prezzo";
  const enc = encodeURIComponent(h.ticker);
  return (
    <div className="row co-actions">
      <button type="button" className={`btn${h.followed ? " on" : ""}`} aria-pressed={h.followed} disabled={follow.isPending} onClick={() => follow.mutate()}>
        {h.followed ? "★ Seguita" : "☆ Segui"}
      </button>
      <details className="dropdown" open={open === "soglie"} onToggle={toggle("soglie")}>
        <summary className="btn">{targetLabel}</summary>
        {open === "soglie" && <Targets h={h} done={() => setOpen(null)} />}
      </details>
      <details className="dropdown" open={open === "pipe"} onToggle={toggle("pipe")}>
        <summary className="btn">{pipe.data?.label ? `Pipeline: ${pipe.data.label}` : "Pipeline"}</summary>
        {open === "pipe" && pipe.data && <PipelineMenu ticker={h.ticker} info={pipe.data} done={() => setOpen(null)} />}
      </details>
      <details className="dropdown" open={open === "esporta"} onToggle={toggle("esporta")}>
        <summary className="btn">Esporta</summary>
        {open === "esporta" && (
          <div className="menu" style={{ minWidth: 300 }}>
            <a className="btn" href={`${API}/companies/${enc}/export.xlsx`} download>Modello Excel (con le formule)</a>
            <a className="btn" href={`${API}/companies/${enc}/export.pdf`} download>Sintesi in PDF</a>
            <p className="honest" style={{ margin: "4px 0" }}>Excel: DCF con le formule (ipotesi in blu da cambiare), scenari, sensibilità, costo del capitale, LBO, bilanci con la fonte di ogni esercizio. PDF: giudizio, tesi, numeri, concorrenti, valutazione. Qualche secondo per prepararli.</p>
          </div>
        )}
      </details>
    </div>
  );
}
