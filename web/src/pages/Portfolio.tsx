// Portafoglio: riepilogo in euro (cambio compreso), posizioni con il giudizio, composizione, andamento contro il
// benchmark, operazioni (aggiungi, elimina), dividendi e tasse stimate. Calcoli e controlli nel motore (servizio
// portfolio): qui solo moduli, tabelle e grafici. Prezzi e cambi: quelli salvati (un titolo appena inserito ha il
// prezzo dal prossimo aggiornamento dei dati).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PeriodChart } from "../components/charts";
import { VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Notice, Num, Segmented, Skeleton, type Tone } from "../components/ui";
import { api, type PortfolioPage } from "../lib/api";
import { fmt } from "../lib/fmt";

type Tab = "posizioni" | "operazioni" | "dividendi";
const SUFFIX: Record<string, string> = { MI: "EUR", DE: "EUR", F: "EUR", PA: "EUR", AS: "EUR", MC: "EUR", BR: "EUR", LS: "EUR",
  VI: "EUR", HE: "EUR", IR: "EUR", SW: "CHF", L: "GBP", CO: "DKK", ST: "SEK", OL: "NOK", TO: "CAD", T: "JPY", HK: "HKD" };
const guessCurrency = (s: string) => (s.includes(".") ? SUFFIX[s.split(".").pop()!.toUpperCase()] ?? "EUR" : "USD");
const num = (t: string): number | null => { // "1.234,5" o "1234.5" -> 1234,5; vuoto -> null
  const v = t.trim().replace(/\s/g, "");
  if (!v) return null;
  const x = Number(v.includes(",") ? v.replace(/\./g, "").replace(",", ".") : v);
  return Number.isFinite(x) ? x : null;
};

function NewTransaction({ p, onDone }: { p: PortfolioPage; onDone: () => void }) {
  const qc = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({ ticker: "", kind: "BUY", date: today, quantity: "", price: "", currency: "", fx: "", fees: "", note: "", reason: "" });
  const currency = f.currency || guessCurrency(f.ticker);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => api<{ id: number }>("/portfolio/transactions", { method: "POST", body: JSON.stringify({
      ticker: f.ticker.trim().toUpperCase(), kind: f.kind, date: f.date, quantity: num(f.quantity) ?? 0, price: num(f.price) ?? 0,
      currency, fx: currency === "EUR" ? null : num(f.fx), fees: num(f.fees), note: f.note || null,
      reason: f.kind === "DIV" ? null : f.reason || null }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["portfolio"] }); qc.invalidateQueries({ queryKey: ["journal"] }); onDone(); },
  });
  const verb = { BUY: "Aggiungi acquisto", SELL: "Aggiungi vendita", DIV: "Aggiungi dividendo" }[f.kind] ?? "Aggiungi";
  return (
    <Card title="Nuova operazione" right="tutto resta salvato solo per te">
      <div className="form-grid three">
        <label className="field"><span>Simbolo Yahoo</span>
          <input className="text-input" value={f.ticker} onChange={set("ticker")} placeholder="AAPL, ENI.MI, VWCE.DE"
            title="Il suffisso è la borsa (.MI Milano, .DE Xetra, .AS Amsterdam). Nessun suffisso = borsa americana." /></label>
        <label className="field"><span>Tipo</span>
          <select className="select" value={f.kind} onChange={set("kind")}>{p.kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select></label>
        <label className="field"><span>Data</span><input className="text-input" type="date" max={today} value={f.date} onChange={set("date")} /></label>
        <label className="field"><span>{f.kind === "DIV" ? "Quantità (azioni)" : "Quantità"}</span><input className="text-input" inputMode="decimal" value={f.quantity} onChange={set("quantity")} placeholder="es. 10" /></label>
        <label className="field"><span>{f.kind === "DIV" ? `Dividendo per azione (${currency})` : `Prezzo (${currency})`}</span><input className="text-input" inputMode="decimal" value={f.price} onChange={set("price")} placeholder="es. 182,50" /></label>
        <label className="field"><span>Valuta</span>
          <select className="select" value={currency} onChange={set("currency")}>{p.currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        {currency !== "EUR" && <label className="field"><span>Cambio: {currency} per 1 €</span><input className="text-input" inputMode="decimal" value={f.fx} onChange={set("fx")} placeholder="vuoto = chiusura del giorno" /></label>}
        <label className="field"><span>Commissioni ({currency})</span><input className="text-input" inputMode="decimal" value={f.fees} onChange={set("fees")} placeholder="0,00" /></label>
        <label className="field"><span>Nota</span><input className="text-input" value={f.note} onChange={set("note")} placeholder="facoltativa" /></label>
        {f.kind !== "DIV" && <label className="field wide"><span>Perché (va nel diario delle decisioni)</span>
          <textarea rows={2} value={f.reason} onChange={set("reason")} placeholder="La tesi in due righe: cosa ti aspetti e perché" /></label>}
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>{verb}</button>
        <button type="button" className="btn ghost" onClick={onDone}>Annulla</button>
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </Card>
  );
}

export function PortfolioPageView() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState<Tab>("posizioni");
  const q = useQuery({ queryKey: ["portfolio"], queryFn: () => api<PortfolioPage>("/portfolio") });
  const del = useMutation({
    mutationFn: (id: number) => api(`/portfolio/transactions/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio"] }),
  });
  if (q.isPending) return <div className="page"><h1>Portafoglio</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>Portafoglio</h1><Empty what="Portafoglio non disponibile" why={q.error.message} /></div>;
  const p = q.data;
  const s = p.summary;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Portafoglio</h1><p className="lead">Valore e guadagno in euro (cambio compreso), confronto con l'indice, tasse stimate.</p></div>
        {!adding && <button type="button" className="btn primary" onClick={() => setAdding(true)}>+ Operazione</button>}
      </div>
      {adding && <NewTransaction p={p} onDone={() => setAdding(false)} />}
      {p.empty ? (!adding && <Empty what="Il portafoglio è vuoto" why="Inserisci acquisti, vendite e dividendi con «+ Operazione»: l'app calcola valore, guadagno in euro (cambio compreso), confronto con l'indice e tasse stimate." />) : (
        <>
          {s && (
            <Card>
              <dl className="kv big">
                <div><dt>Valore</dt><dd>{fmt(s.value, "money", { currency: "EUR" })}</dd></div>
                <div><dt>Investito</dt><dd>{fmt(s.invested, "money", { currency: "EUR" })}</dd></div>
                <div><dt>Guadagno</dt><dd><Num v={s.gain} kind="money" currency="EUR" change /></dd></div>
                <div><dt>Guadagno %</dt><dd><Num v={s.gain_pct} kind="pct" change /></dd></div>
                <div><dt>Contro {p.benchmark}</dt><dd><Num v={s.vs_benchmark} kind="pp" /></dd></div>
                <div><dt>Realizzato</dt><dd><Num v={s.realized} kind="money" currency="EUR" signed /></dd></div>
                <div><dt>Dividendi netti</dt><dd>{fmt(s.dividends_net, "money", { currency: "EUR" })}</dd></div>
              </dl>
              <p className="honest">In euro: costo al cambio del giorno di ogni operazione, valore al cambio di oggi (chiusure al {fmt(s.prices_at, "date_short")}). Contro {p.benchmark}: gli stessi euro investiti nell'indice negli stessi giorni, differenza in punti sul capitale investito.</p>
            </Card>
          )}
          {(p.notes ?? []).map((n) => <Notice key={n.title + n.text} tone={n.tone as Tone} title={n.title}>{n.text}</Notice>)}
          <div><Segmented label="Sezione" value={tab} onChange={setTab}
            options={[{ value: "posizioni", label: "Posizioni" }, { value: "operazioni", label: `Operazioni (${p.transactions?.length ?? 0})` }, { value: "dividendi", label: "Dividendi e tasse" }]} /></div>
          {tab === "posizioni" && (
            <>
              <Card title="Posizioni" right={`${p.positions?.length ?? 0} titoli · valori in euro`}>
                {(p.positions?.length ?? 0) === 0 ? <p className="honest">Nessuna posizione aperta: le operazioni inserite sono tutte vendite o dividendi.</p> : (
                  <div className="table-wrap">
                    <table className="dense">
                      <thead><tr><th>Titolo</th><th>Nome</th><th className="n">Quantità</th><th className="n">Costo medio</th><th className="n">Prezzo</th><th className="n">Valore €</th><th className="n">Peso</th><th className="n">Guadagno €</th><th className="n">Guadagno %</th><th>Giudizio</th></tr></thead>
                      <tbody>{p.positions!.map((r) => (
                        <tr key={r.ticker}>
                          <td>{r.known ? <Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link> : <b>{r.ticker}</b>}</td>
                          <td className="nm">{r.name}</td>
                          <td className="n">{fmt(r.quantity, "num", { digits: 2 })}</td>
                          <td className="n">{fmt(r.avg_cost, "price", { currency: r.currency })}</td>
                          <td className="n">{fmt(r.price, "price", { currency: r.currency })}</td>
                          <td className="n">{fmt(r.value_eur, "num", { digits: 0 })}</td>
                          <td className="n">{fmt(r.weight, "pct")}</td>
                          <td className="n"><Num v={r.gain_eur} kind="num" digits={0} signed /></td>
                          <td className="n"><Num v={r.gain_pct} kind="pct" change /></td>
                          <td>{r.category ? <VerdictBadge category={r.category} label={r.verdict ?? undefined} /> : <span className="na">–</span>}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
                <p className="honest">Giudizio: quello della scheda dell'azienda (solo per le azioni con dati; non è una raccomandazione). Il costo medio comprende le commissioni.</p>
              </Card>
              <div className="grid-2">
                <Card title="Andamento in euro" right={`contro ${p.benchmark}: stessi euro, stessi giorni`}>
                  {(p.history_missing?.length ?? 0) > 0 ? (
                    <p className="honest">Manca lo storico dei prezzi di {p.history_missing!.join(", ")}: arriva con il prossimo aggiornamento dei dati.</p>
                  ) : !p.history ? <p className="honest">Mancano prezzi o cambi per i giorni delle operazioni.</p> : (
                    <PeriodChart title="Andamento in euro" type="lines" kind="money" currency="EUR" zero={false}
                      labels={p.history.dates.map((d) => fmt(d, "date_short"))}
                      series={[{ key: "value", label: "Il portafoglio", values: p.history.value },
                        ...(p.history.benchmark ? [{ key: "bench", label: `Se avessi comprato ${p.benchmark}`, values: p.history.benchmark }] : []),
                        { key: "invested", label: "Soldi investiti", values: p.history.invested }]} />
                  )}
                </Card>
                <Card title="Composizione" right="per settore e per valuta">
                  {(p.composition?.length ?? 0) === 0 ? <p className="honest">Serve il valore in euro di almeno una posizione.</p> : (
                    <div className="bars">
                      {["Settore", "Valuta"].map((kind) => (
                        <div key={kind}><div className="sub">{kind}</div>
                          {p.composition!.filter((c) => c.kind === kind).map((c) => (
                            <div key={c.name} className="hbar"><span className="lab">{c.name}</span>
                              <span className="track"><i style={{ width: `${c.share * 100}%` }} className={kind === "Valuta" ? "s2" : "s1"} /></span>
                              <span className="val">{fmt(c.share, "pct")}</span></div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </div>
            </>
          )}
          {tab === "operazioni" && (
            <Card title="Operazioni" right="dalla più recente">
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>Data</th><th>Tipo</th><th>Titolo</th><th className="n">Quantità</th><th className="n">Prezzo</th><th>Valuta</th><th className="n">Cambio per 1 €</th><th className="n">Commissioni</th><th>Nota</th><th aria-label="Azioni" /></tr></thead>
                  <tbody>{(p.transactions ?? []).map((t) => (
                    <tr key={t.id}>
                      <td>{fmt(t.date, "date")}</td>
                      <td><Badge tone={t.kind === "BUY" ? "pos" : t.kind === "SELL" ? "neg" : "info"}>{t.kind_label}</Badge></td>
                      <td><b>{t.ticker}</b></td>
                      <td className="n">{fmt(t.quantity, "num", { digits: t.quantity % 1 ? 4 : 0 })}</td>
                      <td className="n">{fmt(t.price, "num", { digits: 2 })}</td>
                      <td>{t.currency}</td>
                      <td className="n">{t.fx ? fmt(t.fx, "num", { digits: 4 }) : <span className="na">–</span>}</td>
                      <td className="n">{fmt(t.fees, "num")}</td>
                      <td className="wrap clip">{t.note}</td>
                      <td className="n"><button type="button" className="btn ghost" aria-label={`Elimina l'operazione su ${t.ticker} del ${fmt(t.date, "date")}`}
                        onClick={() => { if (window.confirm(`${t.kind_label} di ${t.ticker} del ${fmt(t.date, "date")}: l'operazione verrà cancellata e non si può recuperare. Confermi?`)) del.mutate(t.id); }}>✕</button></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="honest">Cambio vuoto = chiusura del giorno.</p>
            </Card>
          )}
          {tab === "dividendi" && (
            <div className="grid-halves">
              <Card title="Dividendi incassati" right={p.tax_note}>
                {(p.dividends?.length ?? 0) === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun dividendo inserito: aggiungilo con «+ Operazione», tipo Dividendo.</p> : (
                  <>
                    <table className="dense">
                      <thead><tr><th>Data</th><th>Titolo</th><th className="n">Lordo €</th><th className="n">Ritenuta USA €</th><th className="n">Imposta IT €</th><th className="n">Netto €</th></tr></thead>
                      <tbody>{p.dividends!.map((d, i) => (
                        <tr key={i}><td>{fmt(d.date, "date_short")}</td><td>{d.ticker}</td><td className="n">{fmt(d.gross_eur, "num")}</td>
                          <td className="n">{fmt(d.us_tax, "num")}</td><td className="n">{fmt(d.it_tax, "num")}</td><td className="n">{fmt(d.net_eur, "num")}</td></tr>
                      ))}</tbody>
                    </table>
                    <p className="honest">Stima: 15% trattenuto negli USA (con il modulo W-8BEN) e 26% in Italia sul resto.</p>
                  </>
                )}
              </Card>
              <Card title="Tasse stimate (26%)" right={p.tax_note}>
                {!p.taxes ? <p className="honest">Nessuna posizione.</p> : (
                  <>
                    <dl className="kv">
                      <div><dt>Plus/minus se vendessi oggi</dt><dd><Num v={p.taxes.unrealized_gain} kind="money" currency="EUR" signed /></dd></div>
                      <div><dt>{(p.taxes.tax_if_sold ?? 0) >= 0 ? "Imposta in più" : "Imposta in meno (credito)"}</dt><dd>{fmt(Math.abs(p.taxes.tax_if_sold ?? 0), "money", { currency: "EUR" })}</dd></div>
                      {p.taxes.carry.length > 0 && <div><dt>Minusvalenze da recuperare</dt><dd>{fmt(p.taxes.carry.reduce((a, c) => a + c.amount, 0), "money", { currency: "EUR" })}</dd></div>}
                    </dl>
                    {p.taxes.by_year.length > 0 ? (
                      <table className="dense" style={{ marginTop: 12 }}>
                        <thead><tr><th>Anno</th><th className="n">Plus/minus €</th><th className="n">Minus usate €</th><th className="n">Imposta €</th></tr></thead>
                        <tbody>{p.taxes.by_year.map((y) => <tr key={y.year}><td>{y.year}</td><td className="n"><Num v={y.net_gain} kind="num" signed /></td><td className="n">{fmt(y.loss_used, "num")}</td><td className="n">{fmt(y.tax, "num")}</td></tr>)}</tbody>
                      </table>
                    ) : <p className="honest">Nessuna vendita inserita: niente plusvalenze realizzate.</p>}
                    {p.taxes.incomplete && <p className="honest">Stima sulle sole posizioni con guadagno noto (manca un prezzo o un cambio).</p>}
                    <p className="honest">Stima, non è consulenza fiscale. I guadagni e le perdite delle azioni si compensano tra loro e con le minusvalenze dei 4 anni prima (solo quelle inserite qui); i guadagni degli ETF no (redditi di capitale).</p>
                  </>
                )}
              </Card>
            </div>
          )}
          {del.isError && <Notice tone="neg">Non eliminata: {del.error.message}</Notice>}
        </>
      )}
    </div>
  );
}
