// Scheda azienda: testata con le azioni (segui, soglie, pipeline, esporta), giudizio con le prove, nota di onestà,
// funzioni principali e "Altro" (trimestri, dividendi, insider e fondi, la mia ricerca, metriche, stime, automatica).
import { CompanyActions } from "../components/CompanyActions";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { CompanyHead, VerdictCard } from "../components/verdict";
import { Card, Empty, Notice, Segmented, Skeleton } from "../components/ui";
import { AutoPanel, DividendsPanel, EstimatesPanel, InvestorsPanel, MetricsPanel, OwnershipPanel, ResearchPanel } from "./CompanyMore";
import { FinancialsPanel } from "./Financials";
import { DocumentsPanel, PeersPanel } from "./PeersDocs";
import { PricePanel } from "./PricePanel";
import { ValuationPanel } from "./Valuation";
import { api, ApiError, type Company as CompanyData, type Evidence, type StatementsView } from "../lib/api";
import { fmt } from "../lib/fmt";

const OUTCOME: Record<string, [string, string]> = {
  favorevole: ["✓", "pos"], neutro: ["●", "neutral"], contrario: ["✗", "neg"], mancante: ["–", "off"],
};

function EvidenceTable({ rows }: { rows: Evidence[] }) {
  return (
    <div className="table-wrap">
      <table className="dense">
        <thead><tr><th>Prova</th><th className="n">Valore</th><th>Riferimento</th><th>Esito</th></tr></thead>
        <tbody>
          {rows.map((e) => {
            const [sym, tone] = OUTCOME[e.outcome] ?? ["●", "neutral"];
            return (
              <tr key={e.key} className={e.outcome === "mancante" ? "off" : undefined}>
                <td>{e.label}</td>
                <td className="n">{e.shown || "–"}</td>
                <td className="wrap">{e.reference}</td>
                <td className={tone}>{sym} {e.outcome}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const FUNCTIONS = [{ value: "prove", label: "Prove" }, { value: "grafico", label: "Grafico" }, { value: "bilanci", label: "Bilanci" },
  { value: "valutazione", label: "Valutazione" }, { value: "concorrenti", label: "Concorrenti" },
  { value: "documenti", label: "Notizie e documenti" }] as const;
// funzioni "Altro" (codici della webapp classica: ERN, DVD, HDS, NOTE, DES, EE, AUTO)
const MORE_FUNCTIONS = [{ value: "trimestri", label: "Trimestri" }, { value: "dividendi", label: "Dividendi e riacquisti" },
  { value: "insider", label: "Insider e fondi" }, { value: "ricerca", label: "La mia ricerca" },
  { value: "metriche", label: "Tutte le metriche" }, { value: "stime", label: "Stime degli analisti" },
  { value: "automatica", label: "Strategia automatica (sperimentale)" }, { value: "investitori", label: "Tavolo dei grandi investitori" }] as const;
type More = (typeof MORE_FUNCTIONS)[number]["value"];
const STATEMENT_VIEWS: StatementsView[] = ["annual", "quarterly_ttm", "quarterly_single"];
type Fn = (typeof FUNCTIONS)[number]["value"];

export function CompanyPage() {
  const { ticker = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const more: More | undefined = MORE_FUNCTIONS.find((f) => f.value === params.get("fn"))?.value;
  const fn: Fn | null = more ? null : FUNCTIONS.find((f) => f.value === params.get("fn"))?.value ?? "prove";
  const periodo = params.get("periodo") as StatementsView | null;
  const view: StatementsView = periodo && STATEMENT_VIEWS.includes(periodo) ? periodo : "annual";
  const q = useQuery({
    queryKey: ["company", ticker.toUpperCase()],
    queryFn: () => api<CompanyData>(`/companies/${encodeURIComponent(ticker)}`),
    enabled: !!ticker,
    retry: (n, err) => !(err instanceof ApiError && err.status === 404) && n < 2,
  });
  const t = q.data?.header.ticker;
  useEffect(() => {
    if (t) {
      document.title = `${t} · QuantEngine`;
      try { localStorage.setItem("qe_ultima_azienda", t); } catch { /* niente archiviazione */ }
    }
  }, [t]);

  if (!ticker) {
    return <div className="page"><Empty what="Nessuna azienda aperta" why="Cerca un'azienda nella barra in alto (tasto /) o sceglila dall'elenco." /></div>;
  }
  if (q.isPending) {
    return <div className="page" aria-busy="true"><Skeleton height={150} /><Skeleton height={260} /></div>;
  }
  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="page">
        <Empty what={notFound ? `Azienda non trovata: ${ticker}` : "Scheda non disponibile"}
          why={notFound ? "Controlla il ticker o cercala per nome." : String(q.error.message)}>
          <p><Link to="/">Torna all'elenco delle aziende</Link></p>
        </Empty>
      </div>
    );
  }
  const { header, verdict, verdict_error } = q.data;
  return (
    <div className="page">
      <CompanyHead h={header} />
      <CompanyActions h={header} />
      {verdict ? <VerdictCard v={verdict} /> : (
        <Empty what="Giudizio non calcolato" why={verdict_error ?? "Mancano i dati per giudicare questa azienda."} />
      )}
      <Notice tone="info">
        Il giudizio riassume criteri fissi applicati ai dati: non è una raccomandazione. Nella prova sul passato le
        aziende «Candidata all'acquisto» non hanno battuto l'indice.
      </Notice>
      <div className="row">
        <Segmented label="Funzione della scheda" value={fn ?? ("" as Fn)} options={[...FUNCTIONS]}
          onChange={(v) => setParams(v === "prove" ? {} : { fn: v }, { replace: true })} />
        <select className={`select${more ? " on" : ""}`} value={more ?? ""} aria-label="Altre funzioni della scheda"
          onChange={(e) => setParams(e.target.value ? { fn: e.target.value } : {}, { replace: true })}>
          <option value="">Altro…</option>
          {MORE_FUNCTIONS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </div>
      {fn === "bilanci" && <FinancialsPanel ticker={header.ticker} view={view}
        onView={(v) => setParams(v === "annual" ? { fn: "bilanci" } : { fn: "bilanci", periodo: v }, { replace: true })} />}
      {fn === "grafico" && <PricePanel ticker={header.ticker} />}
      {fn === "valutazione" && <ValuationPanel ticker={header.ticker} />}
      {fn === "concorrenti" && <PeersPanel ticker={header.ticker} />}
      {fn === "documenti" && <DocumentsPanel ticker={header.ticker} />}
      {more === "trimestri" && <FinancialsPanel ticker={header.ticker} view="quarterly_single"
        onView={(v) => setParams(v === "annual" ? { fn: "bilanci" } : { fn: "bilanci", periodo: v }, { replace: true })} />}
      {more === "dividendi" && <DividendsPanel ticker={header.ticker} />}
      {more === "insider" && <OwnershipPanel ticker={header.ticker} />}
      {more === "ricerca" && <ResearchPanel ticker={header.ticker} />}
      {more === "metriche" && <MetricsPanel ticker={header.ticker} />}
      {more === "stime" && <EstimatesPanel ticker={header.ticker} />}
      {more === "automatica" && <AutoPanel ticker={header.ticker} />}
      {more === "investitori" && <InvestorsPanel ticker={header.ticker} />}
      {fn === "prove" && verdict && Object.values(verdict.pillars).map((p) => (
        <Card key={p.key} title={p.label}
          right={`${p.score === null ? "dati insufficienti" : `${fmt(p.score, "score")} ${p.grade}`} · ${p.evidence.length - p.missing.length} prove su ${p.evidence.length}`}>
          <EvidenceTable rows={p.evidence} />
        </Card>
      ))}
    </div>
  );
}
