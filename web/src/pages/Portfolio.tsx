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
import { t } from "../lib/i18n";

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
  const verb = { BUY: t("Aggiungi acquisto"), SELL: t("Aggiungi vendita"), DIV: t("Aggiungi dividendo") }[f.kind] ?? t("Aggiungi");
  return (
    <Card title={t("Nuova operazione")} right={t("tutto resta salvato solo per te")}>
      <div className="form-grid three">
        <label className="field"><span>{t("Simbolo Yahoo")}</span>
          <input className="text-input" value={f.ticker} onChange={set("ticker")} placeholder="AAPL, ENI.MI, VWCE.DE"
            title={t("Il suffisso è la borsa (.MI Milano, .DE Xetra, .AS Amsterdam). Nessun suffisso = borsa americana.")} /></label>
        <label className="field"><span>{t("Tipo")}</span>
          <select className="select" value={f.kind} onChange={set("kind")}>{p.kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select></label>
        <label className="field"><span>{t("Data")}</span><input className="text-input" type="date" max={today} value={f.date} onChange={set("date")} /></label>
        <label className="field"><span>{f.kind === "DIV" ? t("Quantità (azioni)") : t("Quantità")}</span><input className="text-input" inputMode="decimal" value={f.quantity} onChange={set("quantity")} placeholder={t("es. 10")} /></label>
        <label className="field"><span>{f.kind === "DIV" ? t("Dividendo per azione ({currency})", { currency }) : t("Prezzo ({currency})", { currency })}</span><input className="text-input" inputMode="decimal" value={f.price} onChange={set("price")} placeholder={t("es. 182,50")} /></label>
        <label className="field"><span>{t("Valuta")}</span>
          <select className="select" value={currency} onChange={set("currency")}>{p.currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        {currency !== "EUR" && <label className="field"><span>{t("Cambio: {currency} per 1 €", { currency })}</span><input className="text-input" inputMode="decimal" value={f.fx} onChange={set("fx")} placeholder={t("vuoto = chiusura del giorno")} /></label>}
        <label className="field"><span>{t("Commissioni ({currency})", { currency })}</span><input className="text-input" inputMode="decimal" value={f.fees} onChange={set("fees")} placeholder="0,00" /></label>
        <label className="field"><span>{t("Nota")}</span><input className="text-input" value={f.note} onChange={set("note")} placeholder={t("facoltativa")} /></label>
        {f.kind !== "DIV" && <label className="field wide"><span>{t("Perché (va nel diario delle decisioni)")}</span>
          <textarea rows={2} value={f.reason} onChange={set("reason")} placeholder={t("La tesi in due righe: cosa ti aspetti e perché")} /></label>}
      </div>
      <div className="actions">
        <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>{verb}</button>
        <button type="button" className="btn ghost" onClick={onDone}>{t("Annulla")}</button>
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
  if (q.isPending) return <div className="page"><h1>{t("Portafoglio")}</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>{t("Portafoglio")}</h1><Empty what={t("Portafoglio non disponibile")} why={q.error.message} /></div>;
  const p = q.data;
  const s = p.summary;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{t("Portafoglio")}</h1><p className="lead">{t("Valore e guadagno in euro (cambio compreso), confronto con l'indice, tasse stimate.")}</p></div>
        {!adding && <button type="button" className="btn primary" onClick={() => setAdding(true)}>+ {t("Operazione")}</button>}
      </div>
      {adding && <NewTransaction p={p} onDone={() => setAdding(false)} />}
      {p.empty ? (!adding && <Empty what={t("Il portafoglio è vuoto")} why={t("Inserisci acquisti, vendite e dividendi con «+ Operazione»: l'app calcola valore, guadagno in euro (cambio compreso), confronto con l'indice e tasse stimate.")} />) : (
        <>
          {s && (
            <Card>
              <dl className="kv big">
                <div><dt>{t("Valore")}</dt><dd>{fmt(s.value, "money", { currency: "EUR" })}</dd></div>
                <div><dt>{t("Investito")}</dt><dd>{fmt(s.invested, "money", { currency: "EUR" })}</dd></div>
                <div><dt>{t("Guadagno")}</dt><dd><Num v={s.gain} kind="money" currency="EUR" change /></dd></div>
                <div><dt>{t("Guadagno %")}</dt><dd><Num v={s.gain_pct} kind="pct" change /></dd></div>
                <div><dt>{t("Contro {benchmark}", { benchmark: p.benchmark })}</dt><dd><Num v={s.vs_benchmark} kind="pp" /></dd></div>
                <div><dt>{t("Realizzato")}</dt><dd><Num v={s.realized} kind="money" currency="EUR" signed /></dd></div>
                <div><dt>{t("Dividendi netti")}</dt><dd>{fmt(s.dividends_net, "money", { currency: "EUR" })}</dd></div>
              </dl>
              <p className="honest">{t("In euro: costo al cambio del giorno di ogni operazione, valore al cambio di oggi (chiusure al {date}). Contro {benchmark}: gli stessi euro investiti nell'indice negli stessi giorni, differenza in punti sul capitale investito.", { date: fmt(s.prices_at, "date_short"), benchmark: p.benchmark })}</p>
            </Card>
          )}
          {(p.notes ?? []).map((n) => <Notice key={n.title + n.text} tone={n.tone as Tone} title={n.title}>{n.text}</Notice>)}
          <div><Segmented label={t("Sezione")} value={tab} onChange={setTab}
            options={[{ value: "posizioni", label: t("Posizioni") }, { value: "operazioni", label: t("Operazioni ({n})", { n: p.transactions?.length ?? 0 }) }, { value: "dividendi", label: t("Dividendi e tasse") }]} /></div>
          {tab === "posizioni" && (
            <>
              <Card title={t("Posizioni")} right={t("{n} titoli · valori in euro", { n: p.positions?.length ?? 0 })}>
                {(p.positions?.length ?? 0) === 0 ? <p className="honest">{t("Nessuna posizione aperta: le operazioni inserite sono tutte vendite o dividendi.")}</p> : (
                  <div className="table-wrap">
                    <table className="dense">
                      <thead><tr><th>{t("Titolo")}</th><th>{t("Nome")}</th><th className="n">{t("Quantità")}</th><th className="n">{t("Costo medio")}</th><th className="n">{t("Prezzo")}</th><th className="n">{t("Valore €")}</th><th className="n">{t("Peso")}</th><th className="n">{t("Guadagno €")}</th><th className="n">{t("Guadagno %")}</th><th>{t("Giudizio")}</th></tr></thead>
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
                <p className="honest">{t("Giudizio: quello della scheda dell'azienda (solo per le azioni con dati). Il costo medio comprende le commissioni.")}</p>
              </Card>
              <div className="grid-2">
                <Card title={t("Andamento in euro")} right={t("contro {benchmark}: stessi euro, stessi giorni", { benchmark: p.benchmark })}>
                  {(p.history_missing?.length ?? 0) > 0 ? (
                    <p className="honest">{t("Manca lo storico dei prezzi di {tickers}: arriva con il prossimo aggiornamento dei dati.", { tickers: p.history_missing!.join(", ") })}</p>
                  ) : !p.history ? <p className="honest">{t("Mancano prezzi o cambi per i giorni delle operazioni.")}</p> : (
                    <PeriodChart title={t("Andamento in euro")} type="lines" kind="money" currency="EUR" zero={false}
                      labels={p.history.dates.map((d) => fmt(d, "date_short"))}
                      series={[{ key: "value", label: t("Il portafoglio"), values: p.history.value },
                        ...(p.history.benchmark ? [{ key: "bench", label: t("Se avessi comprato {benchmark}", { benchmark: p.benchmark }), values: p.history.benchmark }] : []),
                        { key: "invested", label: t("Soldi investiti"), values: p.history.invested }]} />
                  )}
                </Card>
                <Card title={t("Composizione")} right={t("per settore e per valuta")}>
                  {(p.composition?.length ?? 0) === 0 ? <p className="honest">{t("Serve il valore in euro di almeno una posizione.")}</p> : (
                    <div className="bars">
                      {["Settore", "Valuta"].map((kind) => (
                        <div key={kind}><div className="sub">{t(kind)}</div>
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
            <Card title={t("Operazioni")} right={t("dalla più recente")}>
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>{t("Data")}</th><th>{t("Tipo")}</th><th>{t("Titolo")}</th><th className="n">{t("Quantità")}</th><th className="n">{t("Prezzo")}</th><th>{t("Valuta")}</th><th className="n">{t("Cambio per 1 €")}</th><th className="n">{t("Commissioni")}</th><th>{t("Nota")}</th><th aria-label={t("Azioni sulla riga")} /></tr></thead>
                  <tbody>{(p.transactions ?? []).map((tx) => (
                    <tr key={tx.id}>
                      <td>{fmt(tx.date, "date")}</td>
                      <td><Badge tone={tx.kind === "BUY" ? "pos" : tx.kind === "SELL" ? "neg" : "info"}>{tx.kind_label}</Badge></td>
                      <td><b>{tx.ticker}</b></td>
                      <td className="n">{fmt(tx.quantity, "num", { digits: tx.quantity % 1 ? 4 : 0 })}</td>
                      <td className="n">{fmt(tx.price, "num", { digits: 2 })}</td>
                      <td>{tx.currency}</td>
                      <td className="n">{tx.fx ? fmt(tx.fx, "num", { digits: 4 }) : <span className="na">–</span>}</td>
                      <td className="n">{fmt(tx.fees, "num")}</td>
                      <td className="wrap clip">{tx.note}</td>
                      <td className="n"><button type="button" className="btn ghost" aria-label={t("Elimina l'operazione su {ticker} del {date}", { ticker: tx.ticker, date: fmt(tx.date, "date") })}
                        onClick={() => { if (window.confirm(t("{kind} di {ticker} del {date}: l'operazione verrà cancellata e non si può recuperare. Confermi?", { kind: tx.kind_label, ticker: tx.ticker, date: fmt(tx.date, "date") }))) del.mutate(tx.id); }}>✕</button></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="honest">{t("Cambio vuoto = chiusura del giorno.")}</p>
            </Card>
          )}
          {tab === "dividendi" && (
            <div className="grid-halves">
              <Card title={t("Dividendi incassati")} right={p.tax_note}>
                {(p.dividends?.length ?? 0) === 0 ? <p className="honest" style={{ margin: 0 }}>{t("Nessun dividendo inserito: aggiungilo con «+ Operazione», tipo Dividendo.")}</p> : (
                  <>
                    <table className="dense">
                      <thead><tr><th>{t("Data")}</th><th>{t("Titolo")}</th><th className="n">{t("Lordo €")}</th><th className="n">{t("Ritenuta USA €")}</th><th className="n">{t("Imposta IT €")}</th><th className="n">{t("Netto €")}</th></tr></thead>
                      <tbody>{p.dividends!.map((d, i) => (
                        <tr key={i}><td>{fmt(d.date, "date_short")}</td><td>{d.ticker}</td><td className="n">{fmt(d.gross_eur, "num")}</td>
                          <td className="n">{fmt(d.us_tax, "num")}</td><td className="n">{fmt(d.it_tax, "num")}</td><td className="n">{fmt(d.net_eur, "num")}</td></tr>
                      ))}</tbody>
                    </table>
                    <p className="honest">{t("Stima: 15% trattenuto negli USA (con il modulo W-8BEN) e 26% in Italia sul resto.")}</p>
                  </>
                )}
              </Card>
              <Card title={t("Tasse stimate (26%)")} right={p.tax_note}>
                {!p.taxes ? <p className="honest">{t("Nessuna posizione.")}</p> : (
                  <>
                    <dl className="kv">
                      <div><dt>{t("Plus/minus se vendessi oggi")}</dt><dd><Num v={p.taxes.unrealized_gain} kind="money" currency="EUR" signed /></dd></div>
                      <div><dt>{(p.taxes.tax_if_sold ?? 0) >= 0 ? t("Imposta in più") : t("Imposta in meno (credito)")}</dt><dd>{fmt(Math.abs(p.taxes.tax_if_sold ?? 0), "money", { currency: "EUR" })}</dd></div>
                      {p.taxes.carry.length > 0 && <div><dt>{t("Minusvalenze da recuperare")}</dt><dd>{fmt(p.taxes.carry.reduce((a, c) => a + c.amount, 0), "money", { currency: "EUR" })}</dd></div>}
                    </dl>
                    {p.taxes.by_year.length > 0 ? (
                      <table className="dense" style={{ marginTop: 12 }}>
                        <thead><tr><th>{t("Anno")}</th><th className="n">{t("Plus/minus €")}</th><th className="n">{t("Minus usate €")}</th><th className="n">{t("Imposta €")}</th></tr></thead>
                        <tbody>{p.taxes.by_year.map((y) => <tr key={y.year}><td>{y.year}</td><td className="n"><Num v={y.net_gain} kind="num" signed /></td><td className="n">{fmt(y.loss_used, "num")}</td><td className="n">{fmt(y.tax, "num")}</td></tr>)}</tbody>
                      </table>
                    ) : <p className="honest">{t("Nessuna vendita inserita: niente plusvalenze realizzate.")}</p>}
                    {p.taxes.incomplete && <p className="honest">{t("Stima sulle sole posizioni con guadagno noto (manca un prezzo o un cambio).")}</p>}
                    <p className="honest">{t("Stima, non è consulenza fiscale. I guadagni e le perdite delle azioni si compensano tra loro e con le minusvalenze dei 4 anni prima (solo quelle inserite qui); i guadagni degli ETF no (redditi di capitale).")}</p>
                  </>
                )}
              </Card>
            </div>
          )}
          {del.isError && <Notice tone="neg">{t("Non eliminata: {error}", { error: del.error.message })}</Notice>}
        </>
      )}
    </div>
  );
}
