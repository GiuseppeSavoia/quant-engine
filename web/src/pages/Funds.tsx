// Grandi investitori: portafogli dei fondi seguiti dai 13F trimestrali della SEC (servizio data_admin).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty, Notice, Num, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { fmt } from "../lib/fmt";
import { t } from "../lib/i18n";

type N = number | null;
interface Funds {
  empty: boolean; fund: string | null;
  top: { ticker: string; name: string; funds: number; bought: number; value: N }[];
  funds: { cik: string; name: string; report_date: string; value: N }[];
  positions: { issuer: string; ticker: string | null; change: string; weight: N; value: N; shares: N; shares_change: N }[];
}
const CHANGE: Record<string, string> = { nuova: "▲ nuova", aumentata: "▲ aumentata", ridotta: "▼ ridotta", uscita: "✗ uscita", invariata: "■ invariata" };

export function FundsPage() {
  const [fund, setFund] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["funds", fund], placeholderData: keepPreviousData,
    queryFn: () => api<Funds>(`/funds${fund ? `?fund=${encodeURIComponent(fund)}` : ""}`) });
  if (q.isPending) return <div className="page"><h1>{t("Grandi investitori")}</h1><Skeleton height={300} /></div>;
  if (q.isError) return <div className="page"><h1>{t("Grandi investitori")}</h1><Empty what={t("Non disponibile")} why={q.error.message} /></div>;
  const f = q.data;
  return (
    <div className="page">
      <h1>{t("Grandi investitori")}</h1>
      {f.empty ? (
        <Empty what={t("Nessun portafoglio 13F scaricato")} why={t("Si scaricano con l'aggiornamento dei dati reali (anche insider e grandi investitori: la prima volta 5-10 minuti). Con i dati simulati della demo non ci sono. I fondi seguiti sono nella sezione superinvestors di config.yaml.")} />
      ) : (
        <>
          <Notice tone="info" title={t("13F della SEC:")}>{t("posizioni a fine trimestre in azioni USA, depositate fino a 45 giorni dopo: utili per idee e conferme, non per copiare (quando le vedi sono già vecchie).")}</Notice>
          <Card title={t("Le aziende più presenti nei portafogli")} right={t("prime {n} per numero di fondi", { n: f.top.length })}>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th className="n">{t("Fondi")}</th><th className="n">{t("Comprata o aumentata da")}</th><th className="n">{t("Valore complessivo")}</th></tr></thead>
                <tbody>{f.top.map((r) => (
                  <tr key={r.ticker}><td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td><td className="clip">{r.name}</td>
                    <td className="n">{r.funds}</td><td className="n">{r.bought}</td><td className="n">{fmt(r.value, "money", { currency: "USD" })}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
          <Card title={t("Il portafoglio di un fondo")} right={
            <select className="select" value={f.fund ?? ""} onChange={(e) => setFund(e.target.value)} aria-label={t("Fondo")}>
              {f.funds.map((x) => <option key={x.cik} value={x.cik}>{x.name} — {t("al {date}, {value}", { date: fmt(x.report_date, "date"), value: fmt(x.value, "money", { currency: "USD" }) })}</option>)}
            </select>}>
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="dense">
                <thead><tr><th>{t("Emittente (come nel 13F)")}</th><th>{t("Ticker")}</th><th>{t("Rispetto al trimestre prima")}</th><th className="n">{t("Peso")}</th><th className="n">{t("Valore (mln $)")}</th><th className="n">{t("Azioni (mln)")}</th><th className="n">{t("Variazione azioni")}</th></tr></thead>
                <tbody>{f.positions.map((p, i) => (
                  <tr key={i}><td className="clip">{p.issuer}</td><td>{p.ticker ? <Link to={`/azienda/${encodeURIComponent(p.ticker)}`}>{p.ticker}</Link> : <span className="na">–</span>}</td>
                    <td>{t(CHANGE[p.change] ?? p.change)}</td><td className="n">{fmt(p.weight, "pct")}</td><td className="n">{fmt(p.value === null ? null : p.value / 1e6, "num", { digits: 1 })}</td>
                    <td className="n">{fmt(p.shares === null ? null : p.shares / 1e6, "num", { digits: 2 })}</td><td className="n"><Num v={p.shares_change} kind="pct" change /></td></tr>
                ))}</tbody>
              </table>
            </div>
            <p className="honest">{t("Il ticker è ricavato dal nome dell'emittente: per le aziende fuori dall'S&P 500 può mancare.")}</p>
          </Card>
        </>
      )}
    </div>
  );
}
