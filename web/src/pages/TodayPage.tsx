// Oggi (pagina iniziale): è successo qualcosa alle MIE aziende? Le mie aziende con prezzo e giudizio, avvisi (non visti
// in cima, movimenti di prezzo a parte), notizie, documenti arrivati, movimenti dell'indice, insider, borse.
// Tutto dall'API (servizio today): prezzi = ultime chiusure salvate, nessuna quotazione dal vivo.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { VerdictBadge } from "../components/verdict";
import { GetStarted } from "../components/Onboarding";
import { Spark } from "../components/Spark";
import { Badge, Card, Empty, Num, Skeleton, type Tone } from "../components/ui";
import { api, type Today, type TodayAlert } from "../lib/api";
import { fmt } from "../lib/fmt";
import { LANG, t, tn } from "../lib/i18n";

function when(iso: string | null): string {
  if (!iso) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return fmt(iso, "date_short");
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return fmt(iso, "date_short");
  const hm = d.toLocaleTimeString(LANG === "en" ? "en-US" : "it-IT", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? hm : `${fmt(iso.slice(0, 10), "date_short")} ${hm}`;
}

function AlertList({ items }: { items: TodayAlert[] }) {
  return (
    <div className="alerts">
      {items.map((a) => (
        <div key={a.id} className={`alert${a.unseen ? " new" : ""}`}>
          <span className="dot" title={a.unseen ? t("non visto") : undefined}>{a.unseen ? "●" : ""}</span>
          <span className="tk"><Link to={`/azienda/${encodeURIComponent(a.ticker)}`}>{a.ticker}</Link></span>
          <Badge tone={(a.tone as Tone) || "neutral"}>{a.code}</Badge>
          <span className="msg" title={a.message}>{a.message}</span>
          <span className="age">{when(a.created_at)}</span>
        </div>
      ))}
    </div>
  );
}

export function TodayPage() {
  const qc = useQueryClient();
  const [onlyMine, setOnlyMine] = useState(false);
  const q = useQuery({ queryKey: ["today", onlyMine], queryFn: () => api<Today>(`/today?only_mine=${onlyMine}`) });
  const seen = useMutation({
    mutationFn: (ids: number[]) => api("/alerts/seen", { method: "POST", body: JSON.stringify({ ids }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["today"] }); qc.invalidateQueries({ queryKey: ["my-companies"] }); },
  });
  if (q.isPending) return <div className="page"><h1>{t("Oggi")}</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>{t("Oggi")}</h1><Empty what={t("Pagina non disponibile")} why={q.error.message} /></div>;
  const d = q.data;
  const unseenIds = [...d.alerts, ...d.moves].filter((a) => a.unseen).map((a) => a.id);
  const showMargin = d.mine.some((r) => r.margin !== null);
  const showBuy = d.mine.some((r) => r.buy_below !== null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{t("Oggi")}</h1>
          <p className="lead">{d.source}{d.unseen ? ` · ${tn(d.unseen, "{n} avviso non visto", "{n} avvisi non visti")}` : ""}</p>
        </div>
      </div>

      <div className="grid-2">
        <Card title={t("Le mie aziende")} right={<Link to="/mie-aziende">{t("tutte le colonne")} ›</Link>}>
          {d.mine.length === 0 ? (
            <GetStarted />
          ) : (
            <>
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th className="n">{t("Prezzo")}</th><th className="n">{t("Ultima seduta")}</th><th className="n">{t("1 anno")}</th>
                    {showMargin && <th className="n" title={t("Il tuo fair value contro il prezzo")}>{t("Margine FV")}</th>}
                    {showBuy && <th className="n" title={t("La tua soglia d'acquisto e quanto manca")}>{t("Compra sotto")}</th>}
                    <th>{t("Giudizio")}</th></tr></thead>
                  <tbody>
                    {d.mine.map((r) => (
                      <tr key={r.ticker}>
                        <td>
                          {r.unseen > 0 && <span className="info" title={tn(r.unseen, "{n} avviso non visto", "{n} avvisi non visti")}>● </span>}
                          <Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link>
                          {r.held && <span className="honest" title={t("In portafoglio")}> {t("ptf")}</span>}
                        </td>
                        <td className="nm">{r.name}</td>
                        <td className="n">{fmt(r.last, "price")}</td>
                        <td className="n"><Num v={r.change_pct} kind="pct" change digits={2} /></td>
                        <td className="n spark-cell"><Spark values={r.spark} /><Num v={r.ret_1y} kind="pct" change /></td>
                        {showMargin && <td className="n"><Num v={r.margin} kind="pct" signed /></td>}
                        {showBuy && <td className="n">{r.buy_below !== null ? <>{fmt(r.buy_below, "price")} <Num v={r.buy_gap} kind="pct" signed /></> : <span className="na">–</span>}</td>}
                        <td title={r.rule}>{r.category === "non_valutato" ? <span className="na" title={t("Fuori dall'anagrafica: nessun giudizio")}>–</span> : <VerdictBadge category={r.category} label={r.verdict} />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="honest">{t("Seguite, in portafoglio (ptf) e con una soglia di prezzo.")}</p>
            </>
          )}
        </Card>

        <Card title={t("Avvisi")} right={<Link to="/mie-aziende">{t("storico")} ›</Link>}>
          {d.alerts.length === 0 && d.moves.length === 0 ? (
            <p className="honest" style={{ margin: 0 }}>{t("Nessun avviso attivo. Gli avvisi (prezzo sotto la soglia o il fair value, bilanci nuovi, acquisti degli insider, movimenti forti…) arrivano con ogni aggiornamento dei dati.")}</p>
          ) : (
            <>
              {d.alerts.length > 0 ? <AlertList items={d.alerts.slice(0, 30)} /> : <p className="honest">{t("Nessun avviso su soglie, fair value, bilanci, giudizi o insider.")}</p>}
              {d.moves.length > 0 && (
                <details className="moves">
                  <summary>{t("Movimenti di prezzo")} ({d.moves.length}{d.moves.some((m) => m.unseen) ? `, ${t("{n} non visti", { n: d.moves.filter((m) => m.unseen).length })}` : ""})</summary>
                  <AlertList items={d.moves.slice(0, 30)} />
                </details>
              )}
              {unseenIds.length > 0 && (
                <div className="actions">
                  <button type="button" className="btn" disabled={seen.isPending} onClick={() => seen.mutate(unseenIds)}>{t("Segna tutti come visti")}</button>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title={t("Ultime notizie")} right={d.news_mine ? t("le tue aziende") : t("tutte")}>
          {d.news.length === 0 ? <p className="honest" style={{ margin: 0 }}>{t("Nessuna notizia salvata per ora.")}</p> : (
            <ul className="news">
              {d.news.map((n) => (
                <li key={n.link}>
                  <span className="age">{when(n.published)}</span>
                  <span className="src">{n.source}</span>
                  <a href={n.link} target="_blank" rel="noopener noreferrer">{n.title}</a>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={t("Documenti ufficiali")} right={t("10-Q/10-K e 8-K · {n} giorni", { n: d.documents_days })}>
          <label className="check" style={{ marginBottom: 8 }}>
            <input type="checkbox" checked={onlyMine} disabled={d.mine.length === 0} onChange={(e) => setOnlyMine(e.target.checked)} />{t("Solo le mie aziende")}
          </label>
          {d.documents.length === 0 ? (
            <p className="honest" style={{ margin: 0 }}>{t("Nessun documento negli ultimi {n} giorni (si scaricano con l'aggiornamento dei dati).", { n: d.documents_days })}</p>
          ) : (
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>{t("Data")}</th><th>{t("Ticker")}</th><th>{t("Doc")}</th><th>{t("Cosa")}</th></tr></thead>
                <tbody>
                  {d.documents.map((dcm, i) => (
                    <tr key={`${dcm.ticker}-${dcm.form}-${i}`}>
                      <td>{when(dcm.when)}</td>
                      <td><Link to={`/azienda/${encodeURIComponent(dcm.ticker)}`}><b>{dcm.ticker}</b></Link></td>
                      <td>{dcm.form}</td>
                      <td className="wrap">{dcm.url ? <a className={dcm.important ? "neg" : undefined} href={dcm.url} target="_blank" rel="noopener noreferrer">{dcm.important ? "⚠ " : ""}{dcm.what}</a> : dcm.what}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="grid-halves three">
        {(["up", "down"] as const).map((side) => (
          <Card key={side} title={side === "up" ? t("S&P 500 · in salita") : t("S&P 500 · in discesa")} right={t("ultima seduta")}>
            {d.movers[side].length === 0 ? <p className="honest" style={{ margin: 0 }}>{side === "up" ? t("Nessun titolo in salita.") : t("Nessun titolo in discesa.")}</p> : (
              <table className="dense">
                <tbody>
                  {d.movers[side].map((m) => (
                    <tr key={m.ticker}>
                      <td><Link to={`/azienda/${encodeURIComponent(m.ticker)}`}><b>{m.ticker}</b></Link></td>
                      <td className="nm">{m.name}</td>
                      <td className="n">{fmt(m.last, "price")}</td>
                      <td className="n"><Num v={m.change_pct} kind="pct" change digits={2} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        ))}
        <Card title={t("Acquisti degli insider")} right={t("7 giorni")}>
          {!d.insiders_loaded ? <p className="honest" style={{ margin: 0 }}>{t("Insider non scaricati (aggiornamento dei dati con l'opzione insider).")}</p>
            : d.insiders.length === 0 ? <p className="honest" style={{ margin: 0 }}>{t("Nessun acquisto sul mercato depositato negli ultimi 7 giorni.")}</p> : (
              <table className="dense">
                <thead><tr><th>{t("Ticker")}</th><th className="n">{t("Valore")}</th><th className="n">{t("Insider")}</th></tr></thead>
                <tbody>{d.insiders.map((i) => (
                  <tr key={i.ticker}><td><Link to={`/azienda/${encodeURIComponent(i.ticker)}`}><b>{i.ticker}</b></Link></td>
                    <td className="n">{fmt(i.value, "money", { currency: "USD" })}</td><td className="n">{i.buyers}</td></tr>
                ))}</tbody>
              </table>
            )}
        </Card>
      </div>

      <details className="card details">
        <summary>{t("Borse mondiali")}</summary>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>{t("Borsa")}</th><th className="n">{t("Ora")}</th><th>{t("Stato")}</th><th /><th>{t("Indice")}</th></tr></thead>
            <tbody>
              {d.exchanges.map((b) => (
                <tr key={b.code} title={t("orari {hours} (ora locale)", { hours: b.hours })}>
                  <td>{t(b.name)}{b.holidays_complete ? "" : "*"}</td>
                  <td className="n">{b.local}</td>
                  <td className={b.open ? "pos" : b.state === "PAUSA" ? "warn" : undefined}>
                    {b.open ? `● ${t("aperta")}` : b.state === "PAUSA" ? `◐ ${t("pausa")}` : `○ ${t("chiusa")}`}{b.holiday && !b.open ? ` ${t("per {holiday}", { holiday: b.holiday })}` : ""}
                  </td>
                  <td>{b.open ? t("chiude tra {until}", { until: b.until }) : t("apre tra {until}", { until: b.until })}</td>
                  <td>{b.index}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="honest">{t("Orari della contrattazione continua, ora locale di ogni borsa (passa sulla riga per vederli). * = calendario delle festività parziale.")}</p>
      </details>
    </div>
  );
}
