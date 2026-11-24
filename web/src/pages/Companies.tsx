// Elenco delle aziende (pagina iniziale provvisoria della fase 2): ricerca e accesso alla scheda.
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Empty, Skeleton } from "../components/ui";
import { api, type CompanyRow } from "../lib/api";
import { t } from "../lib/i18n";

export function CompaniesPage() {
  const [filter, setFilter] = useState("");
  const q = useQuery({ queryKey: ["companies"], queryFn: () => api<{ results: CompanyRow[]; total: number }>("/companies?limit=200") });
  const rows = useMemo(() => {
    const f = filter.trim().toUpperCase();
    return (q.data?.results ?? []).filter((r) => !f || r.ticker.toUpperCase().includes(f) || r.name.toUpperCase().includes(f)
      || (r.sector ?? "").toUpperCase().includes(f));
  }, [q.data, filter]);
  return (
    <div className="page">
      <h1>{t("Aziende")}</h1>
      <p className="lead">{t("Apri la scheda di un'azienda per vedere il giudizio, le prove e i numeri chiave.")}</p>
      <Card title={t("Elenco")} right={q.data ? t("{n} su {total}", { n: rows.length, total: q.data.total }) : undefined}>
        <div className="search" style={{ maxWidth: 360, marginBottom: 12 }}>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("Filtra per ticker, nome o settore")}
            aria-label={t("Filtra le aziende")} style={{ paddingLeft: 12 }} />
        </div>
        {q.isPending ? <Skeleton height={220} /> : q.isError ? (
          <Empty what={t("Elenco non disponibile")} why={q.error.message} />
        ) : rows.length === 0 ? (
          <Empty what={t("Nessuna azienda")} why={t("Nessuna azienda corrisponde al filtro.")} />
        ) : (
          <div className="table-wrap">
            <table className="dense">
              <thead><tr><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th>{t("Settore")}</th><th>{t("Universo")}</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.ticker}>
                    <td><Link to={`/azienda/${encodeURIComponent(r.ticker)}`}><b>{r.ticker}</b></Link></td>
                    <td>{r.name}</td><td>{r.sector ?? "–"}</td><td>{r.universe}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
