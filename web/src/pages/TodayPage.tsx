// Oggi (pagina iniziale): è successo qualcosa alle MIE aziende? Le mie aziende con prezzo e giudizio, avvisi (non visti
// in cima, movimenti di prezzo a parte), notizie, documenti arrivati, movimenti dell'indice, insider, borse.
// Tutto dall'API (servizio today): prezzi = ultime chiusure salvate, nessuna quotazione dal vivo.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { VerdictBadge } from "../components/verdict";
import { Badge, Card, Empty, Num, Skeleton, type Tone } from "../components/ui";
import { api, type Today, type TodayAlert } from "../lib/api";
import { fmt } from "../lib/fmt";

function when(iso: string | null): string {
  if (!iso) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return fmt(iso, "date_short");
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return fmt(iso, "date_short");
  const hm = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? hm : `${fmt(iso.slice(0, 10), "date_short")} ${hm}`;
}

function AlertList({ items }: { items: TodayAlert[] }) {
  return (
    <div className="alerts">
      {items.map((a) => (
        <div key={a.id} className={`alert${a.unseen ? " new" : ""}`}>
          <span className="dot" title={a.unseen ? "non visto" : undefined}>{a.unseen ? "●" : ""}</span>
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
  if (q.isPending) return <div className="page"><h1>Oggi</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>Oggi</h1><Empty what="Pagina non disponibile" why={q.error.message} /></div>;
  const t = q.data;
  const unseenIds = [...t.alerts, ...t.moves].filter((a) => a.unseen).map((a) => a.id);
  const showMargin = t.mine.some((r) => r.margin !== null);
  const showBuy = t.mine.some((r) => r.buy_below !== null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Oggi</h1>
          <p className="lead">{t.source}{t.unseen ? ` · ${t.unseen} ${t.unseen === 1 ? "avviso non visto" : "avvisi non visti"}` : ""}</p>
        </div>
      </div>

      <div className="grid-2">
        <Card title="Le mie aziende" right={<Link to="/mie-aziende">tutte le colonne ›</Link>}>
          {t.mine.length === 0 ? (
            <Empty what="Non segui ancora nessuna azienda" why={<>Qui vedrai prezzo, variazione, il tuo margine sul fair value e il giudizio. <Link to="/esplora">Esplora</Link> le aziende o apri una scheda e premi Segui.</>} />
          ) : (
            <>
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>Ticker</th><th>Azienda</th><th className="n">Prezzo</th><th className="n">Ultima seduta</th><th className="n">1 anno</th>
                    {showMargin && <th className="n" title="Il tuo fair value contro il prezzo">Margine FV</th>}
                    {showBuy && <th className="n" title="La tua soglia d'acquisto e quanto manca">Compra sotto</th>}
                    <th>Giudizio</th></tr></thead>
                  <tbody>
                    {t.mine.map((r) => (
                      <tr key={r.ticker}>
                        <td>
                          {r.unseen > 0 && <span className="info" title={`${r.unseen} avvisi non visti`}>● </span>}
                          <Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link>
                          {r.held && <span className="honest" title="In portafoglio"> ptf</span>}
                        </td>
                        <td className="nm">{r.name}</td>
                        <td className="n">{fmt(r.last, "price")}</td>
                        <td className="n"><Num v={r.change_pct} kind="pct" change digits={2} /></td>
                        <td className="n"><Num v={r.ret_1y} kind="pct" change /></td>
                        {showMargin && <td className="n"><Num v={r.margin} kind="pct" signed /></td>}
                        {showBuy && <td className="n">{r.buy_below !== null ? <>{fmt(r.buy_below, "price")} <Num v={r.buy_gap} kind="pct" signed /></> : <span className="na">–</span>}</td>}
                        <td title={r.rule}>{r.category === "non_valutato" ? <span className="na" title="Fuori dall'anagrafica: nessun giudizio">–</span> : <VerdictBadge category={r.category} label={r.verdict} />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="honest">Seguite, in portafoglio (ptf) e con una soglia di prezzo. Il giudizio è una sintesi dei criteri, non una raccomandazione.</p>
            </>
          )}
        </Card>

        <Card title="Avvisi" right={<Link to="/mie-aziende">storico ›</Link>}>
          {t.alerts.length === 0 && t.moves.length === 0 ? (
            <p className="honest" style={{ margin: 0 }}>Nessun avviso attivo. Gli avvisi (prezzo sotto la soglia o il fair value, bilanci nuovi, acquisti degli insider, movimenti forti…) arrivano con ogni aggiornamento dei dati.</p>
          ) : (
            <>
              {t.alerts.length > 0 ? <AlertList items={t.alerts.slice(0, 30)} /> : <p className="honest">Nessun avviso su soglie, fair value, bilanci, giudizi o insider.</p>}
              {t.moves.length > 0 && (
                <details className="moves">
                  <summary>Movimenti di prezzo ({t.moves.length}{t.moves.some((m) => m.unseen) ? `, ${t.moves.filter((m) => m.unseen).length} non visti` : ""})</summary>
                  <AlertList items={t.moves.slice(0, 30)} />
                </details>
              )}
              {unseenIds.length > 0 && (
                <div className="actions">
                  <button type="button" className="btn" disabled={seen.isPending} onClick={() => seen.mutate(unseenIds)}>Segna tutti come visti</button>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Ultime notizie" right={t.news_mine ? "le tue aziende" : "tutte"}>
          {t.news.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessuna notizia salvata per ora.</p> : (
            <ul className="news">
              {t.news.map((n) => (
                <li key={n.link}>
                  <span className="age">{when(n.published)}</span>
                  <span className="src">{n.source}</span>
                  <a href={n.link} target="_blank" rel="noopener noreferrer">{n.title}</a>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Documenti ufficiali" right={`10-Q/10-K e 8-K · ${t.documents_days} giorni`}>
          <label className="check" style={{ marginBottom: 8 }}>
            <input type="checkbox" checked={onlyMine} disabled={t.mine.length === 0} onChange={(e) => setOnlyMine(e.target.checked)} />Solo le mie aziende
          </label>
          {t.documents.length === 0 ? (
            <p className="honest" style={{ margin: 0 }}>Nessun documento negli ultimi {t.documents_days} giorni (si scaricano con l'aggiornamento dei dati).</p>
          ) : (
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Data</th><th>Ticker</th><th>Doc</th><th>Cosa</th></tr></thead>
                <tbody>
                  {t.documents.map((dcm, i) => (
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
          <Card key={side} title={side === "up" ? "S&P 500 · in salita" : "S&P 500 · in discesa"} right="ultima seduta">
            {t.movers[side].length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun titolo {side === "up" ? "in salita" : "in discesa"}.</p> : (
              <table className="dense">
                <tbody>
                  {t.movers[side].map((m) => (
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
        <Card title="Acquisti degli insider" right="7 giorni">
          {!t.insiders_loaded ? <p className="honest" style={{ margin: 0 }}>Insider non scaricati (aggiornamento dei dati con l'opzione insider).</p>
            : t.insiders.length === 0 ? <p className="honest" style={{ margin: 0 }}>Nessun acquisto sul mercato depositato negli ultimi 7 giorni.</p> : (
              <table className="dense">
                <thead><tr><th>Ticker</th><th className="n">Valore</th><th className="n">Insider</th></tr></thead>
                <tbody>{t.insiders.map((i) => (
                  <tr key={i.ticker}><td><Link to={`/azienda/${encodeURIComponent(i.ticker)}`}><b>{i.ticker}</b></Link></td>
                    <td className="n">{fmt(i.value, "money", { currency: "USD" })}</td><td className="n">{i.buyers}</td></tr>
                ))}</tbody>
              </table>
            )}
        </Card>
      </div>

      <details className="card details">
        <summary>Borse mondiali</summary>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Borsa</th><th className="n">Ora</th><th>Stato</th><th /><th>Indice</th></tr></thead>
            <tbody>
              {t.exchanges.map((b) => (
                <tr key={b.code} title={`orari ${b.hours} (ora locale)`}>
                  <td>{b.name}{b.holidays_complete ? "" : "*"}</td>
                  <td className="n">{b.local}</td>
                  <td className={b.open ? "pos" : b.state === "PAUSA" ? "warn" : undefined}>
                    {b.open ? "● aperta" : b.state === "PAUSA" ? "◐ pausa" : "○ chiusa"}{b.holiday && !b.open ? ` per ${b.holiday}` : ""}
                  </td>
                  <td>{b.open ? "chiude" : "apre"} tra {b.until}</td>
                  <td>{b.index}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="honest">Orari della contrattazione continua, ora locale di ogni borsa (passa sulla riga per vederli). * = calendario delle festività parziale.</p>
      </details>
    </div>
  );
}
