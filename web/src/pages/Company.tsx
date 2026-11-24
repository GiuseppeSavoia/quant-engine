// Scheda azienda: testata con le azioni (segui, soglie, pipeline, esporta), giudizio con le prove, nota di onestà,
// funzioni principali e "Altro" (trimestri, dividendi, insider e fondi, la mia ricerca, metriche, stime, automatica).
import { CompanyActions } from "../components/CompanyActions";
import { rememberCompany } from "../components/CommandPalette";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { CompanyHead, VerdictCard } from "../components/verdict";
import { Card, Empty, Skeleton } from "../components/ui";
import { AutoPanel, DividendsPanel, EstimatesPanel, InvestorsPanel, MetricsPanel, OwnershipPanel, ResearchPanel } from "./CompanyMore";
import { FinancialsPanel } from "./Financials";
import { DocumentsPanel, PeersPanel } from "./PeersDocs";
import { PricePanel } from "./PricePanel";
import { ValuationPanel } from "./Valuation";
import { api, ApiError, type Company as CompanyData, type Evidence, type StatementsView } from "../lib/api";
import { fmt } from "../lib/fmt";
import { t, tn } from "../lib/i18n";

const OUTCOME: Record<string, [string, string]> = {
  favorevole: ["✓", "pos"], neutro: ["●", "neutral"], contrario: ["✗", "neg"], mancante: ["–", "off"],
};

function EvidenceTable({ rows }: { rows: Evidence[] }) {
  return (
    <div className="table-wrap">
      <table className="dense">
        <thead><tr><th>{t("Prova")}</th><th className="n">{t("Valore")}</th><th>{t("Riferimento")}</th><th>{t("Esito")}</th></tr></thead>
        <tbody>
          {rows.map((e) => {
            const [sym, tone] = OUTCOME[e.outcome] ?? ["●", "neutral"];
            return (
              <tr key={e.key} className={e.outcome === "mancante" ? "off" : undefined}>
                <td>{e.label}</td>
                <td className="n">{e.shown || "–"}</td>
                <td className="wrap">{e.reference}</td>
                <td className={tone}>{sym} {t(e.outcome)}</td>
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
  const moreRef = useRef<HTMLDetailsElement>(null);
  const fn: Fn | null = more ? null : FUNCTIONS.find((f) => f.value === params.get("fn"))?.value ?? "prove";
  const periodo = params.get("periodo") as StatementsView | null;
  const view: StatementsView = periodo && STATEMENT_VIEWS.includes(periodo) ? periodo : "annual";
  const q = useQuery({
    queryKey: ["company", ticker.toUpperCase()],
    queryFn: () => api<CompanyData>(`/companies/${encodeURIComponent(ticker)}`),
    enabled: !!ticker,
    retry: (n, err) => !(err instanceof ApiError && err.status === 404) && n < 2,
  });
  const tk = q.data?.header.ticker;
  useEffect(() => {
    if (tk) {
      document.title = `${tk} · QuantEngine`;
      try { localStorage.setItem("qe_ultima_azienda", tk); } catch { /* niente archiviazione */ }
      rememberCompany(tk);
    }
  }, [tk]);

  if (!ticker) {
    return <div className="page"><Empty what={t("Nessuna azienda aperta")} why={t("Cerca un'azienda nella barra in alto (tasto /) o sceglila dall'elenco.")} /></div>;
  }
  if (q.isPending) {
    return <div className="page" aria-busy="true"><Skeleton height={150} /><Skeleton height={260} /></div>;
  }
  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="page">
        <Empty what={notFound ? t("Azienda non trovata: {ticker}", { ticker }) : t("Scheda non disponibile")}
          why={notFound ? t("Controlla il ticker o cercala per nome.") : String(q.error.message)}>
          <p><Link to="/">{t("Torna all'elenco delle aziende")}</Link></p>
        </Empty>
      </div>
    );
  }
  const { header, verdict, verdict_error } = q.data;
  return (
    <div className="page">
      <CompanyHead h={header} actions={<CompanyActions h={header} />} />
      {verdict ? <VerdictCard v={verdict} /> : (
        <Empty what={t("Giudizio non calcolato")} why={verdict_error ?? t("Mancano i dati per giudicare questa azienda.")} />
      )}
      <nav className="segmented fn-tabs" aria-label={t("Funzioni della scheda")}>
        {FUNCTIONS.map((f) => (
          <button key={f.value} type="button" aria-pressed={fn === f.value}
            onClick={() => setParams(f.value === "prove" ? {} : { fn: f.value }, { replace: true })}>{t(f.label)}</button>
        ))}
        <details className="dropdown fn-more" ref={moreRef}>
          <summary aria-pressed={!!more}>{more ? t(MORE_FUNCTIONS.find((f) => f.value === more)?.label ?? "") : t("Altro")} <span aria-hidden="true">▾</span></summary>
          <div className="menu">
            {MORE_FUNCTIONS.map((f) => (
              <button key={f.value} type="button" className={`menu-link${more === f.value ? " on" : ""}`}
                onClick={() => { if (moreRef.current) moreRef.current.open = false; setParams({ fn: f.value }, { replace: true }); }}>{t(f.label)}</button>
            ))}
          </div>
        </details>
      </nav>
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
          right={`${p.score === null ? t("dati insufficienti") : `${fmt(p.score, "score")} ${p.grade}`} · ${tn(p.evidence.length - p.missing.length, "{n} prova su {total}", "{n} prove su {total}", { total: p.evidence.length })}`}>
          <EvidenceTable rows={p.evidence} />
        </Card>
      ))}
    </div>
  );
}
