// Bilanci della scheda (funzioni FA ed ERN): tre viste (esercizi, ultimi 12 mesi, trimestri singoli), quattro grafici,
// azioni in circolazione con asse da zero, tabella delle voci con il link al 10-K/10-Q di ogni colonna e i segni sulle
// celle che non vengono da un deposito ufficiale. Tutti i numeri arrivano pronti dall'API (servizio statements).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PeriodChart } from "../components/charts";
import { Card, Empty, Segmented, Skeleton } from "../components/ui";
import { api, type Statements, type StatementsView } from "../lib/api";
import { fmt } from "../lib/fmt";

function cell(v: number | null, kind: Statements["rows"][number]["kind"]): string {
  if (kind === "pct") return fmt(v, "pct");
  if (kind === "mult") return fmt(v, "mult", { digits: 2 });
  if (kind === "shares_mln") return fmt(v === null ? null : v / 1e6, "num", { digits: 1 });
  return fmt(v === null ? null : v / 1e6, "int");
}

export function FinancialsPanel({ ticker, view, onView }: { ticker: string; view: StatementsView; onView: (v: StatementsView) => void }) {
  const q = useQuery({
    queryKey: ["financials", ticker, view],
    queryFn: () => api<Statements>(`/companies/${encodeURIComponent(ticker)}/financials?view=${view}`),
    placeholderData: keepPreviousData,
  });
  const views = q.data?.views ?? [{ value: "annual", label: "Esercizi" }, { value: "quarterly_ttm", label: "Ultimi 12 mesi a ogni trimestre" },
    { value: "quarterly_single", label: "Trimestri singoli" }];
  const head = (
    <div className="toolbar">
      <Segmented label="Periodo" value={view} onChange={onView} options={views as { value: StatementsView; label: string }[]} />
      {q.isFetching && !q.isPending && <span className="honest">aggiorno…</span>}
    </div>
  );
  if (q.isPending) return <>{head}<Skeleton height={300} /></>;
  if (q.isError) return <>{head}<Empty what="Bilanci non disponibili" why={q.error.message} /></>;
  const s = q.data;
  if (s.empty) return <>{head}<Empty what={view === "quarterly_single" ? "Nessun trimestre singolo per questa azienda" : "Nessun bilancio per questa azienda"} why={s.why} /></>;
  const labels = s.periods.map((p) => p.label);
  const shown = s.periods.slice(s.table_from);
  return (
    <div className="financials">
      {head}
      <div className="grid-halves">
        {s.charts.map((c) => (
          <Card key={c.key} title={c.title} right={c.kind === "money" ? s.currency : undefined}>
            <PeriodChart title={c.title} labels={labels} series={c.series} kind={c.kind} type={c.type} currency={s.currency} />
          </Card>
        ))}
      </div>
      {s.shares && (
        <Card title="Azioni in circolazione" right="asse da zero, milioni">
          {s.shares.summary && <p className="lead" style={{ marginBottom: 8 }}>{s.shares.summary[0].toUpperCase() + s.shares.summary.slice(1)}.</p>}
          <PeriodChart title="Azioni in circolazione" labels={labels} kind="shares" height={180}
            series={[{ key: "shares", label: "Azioni", values: s.shares.values }]} />
          <p className="honest">Scendono con i riacquisti, salgono con aumenti di capitale e piani azionari. L'asse parte da zero: un calo dell'1% resta un calo dell'1%.</p>
        </Card>
      )}
      <Card title="Le voci" right={`milioni di ${s.currency}, salvo dove indicato · ultima versione depositata`}>
        <div className="table-wrap">
          <table className="dense statements">
            <thead>
              <tr>
                <th>Voce</th>
                {shown.map((p) => (
                  <th key={p.label} className="n" title={p.link?.title ?? p.label}>
                    {p.link ? <a href={p.link.url} target="_blank" rel="noopener noreferrer">{p.label} ↗</a> : p.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {s.rows.map((r) => (
                <tr key={r.key}>
                  <td className="lab">{r.label}</td>
                  {r.values.map((v, i) => {
                    const m = r.marks[i] ? s.marks[r.marks[i]] : undefined;
                    const text = cell(v, r.kind);
                    return (
                      <td key={i} className={`n${text === "–" ? " na" : ""}${m ? " marked" : ""}`} title={m?.why}>
                        {text}{m && <sup>{m.symbol}</sup>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {Object.keys(s.marks).length > 0 && (
          <p className="honest">
            {Object.values(s.marks).map((m) => `${m.symbol} ${m.why.toLowerCase()}`).join(" · ")}. I margini e i rapporti calcolati da questi valori ne ereditano i limiti.
          </p>
        )}
        <p className="honest">{s.note}</p>
      </Card>
    </div>
  );
}
