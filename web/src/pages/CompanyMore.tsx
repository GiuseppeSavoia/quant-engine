// Funzioni "Altro" della scheda: tutte le metriche (DES), dividendi e riacquisti (DVD), insider e grandi investitori
// (HDS), la mia ricerca con il diario delle valutazioni (NOTE), stime degli analisti (EE), strategia automatica (AUTO).
// Dati dal motore (servizi dossier ed estimates): qui solo tabelle, grafici e moduli.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PeriodChart } from "../components/charts";
import { Badge, Card, Empty, Notice, Num, Segmented, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { fmt, kindOfMetric } from "../lib/fmt";

const enc = encodeURIComponent;
type N = number | null;

function useCompany<T>(ticker: string, path: string, extra = "") {
  return useQuery({ queryKey: ["company-more", ticker, path, extra], queryFn: () => api<T>(`/companies/${enc(ticker)}/${path}${extra}`) });
}

function Loading<T>({ q, children }: { q: { isPending: boolean; isError: boolean; error: Error | null; data?: T }; children: (d: T) => React.ReactNode }) {
  if (q.isPending) return <Skeleton height={240} />;
  if (q.isError) return <Empty what="Non disponibile" why={q.error?.message} />;
  return <>{children(q.data as T)}</>;
}

// ---------------------------------------------------------------- DES
interface MetricsView { sector: string | null; currency: string; groups: { group: string; rows: { key: string; label: string; help: string; fmt: string; value: N; sector_median: N; rank_sector: N; index_median: N }[] }[] }

export function MetricsPanel({ ticker }: { ticker: string }) {
  const q = useCompany<MetricsView>(ticker, "metrics");
  return <Loading q={q}>{(m) => (
    <>
      <p className="honest">Ogni numero con la mediana del suo settore ({m.sector ?? "n/d"}) e dell'S&P 500. «Nel settore»: quante aziende del settore fanno peggio (100% = la migliore; per P/E e simili, più basso è meglio). Passa sopra il nome per la spiegazione.</p>
      <div className="grid-2">
        {m.groups.map((g) => (
          <Card key={g.group} title={g.group}>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Metrica</th><th className="n">Valore</th><th className="n">Settore</th><th className="n">Nel settore</th><th className="n">S&P 500</th></tr></thead>
                <tbody>{g.rows.map((r) => {
                  const k = kindOfMetric(r.fmt);
                  const o = k === "money" ? { currency: m.currency } : {};
                  return (
                    <tr key={r.key}><td title={r.help}>{r.label}</td><td className="n"><b>{fmt(r.value, k, o)}</b></td>
                      <td className="n">{fmt(r.sector_median, k, o)}</td><td className="n">{fmt(r.rank_sector, "pct", { digits: 0 })}</td>
                      <td className="n">{fmt(r.index_median, k, k === "money" ? { currency: "USD" } : {})}</td></tr>
                  );
                })}</tbody>
              </table>
            </div>
          </Card>
        ))}
      </div>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- DVD
interface Dividends { empty: boolean; currency: string; years: { year: number; dividends: N; buybacks: N; net_income: N; free_cash_flow: N; shares: N; dps: N; payout: N; fcf_payout: N; dividend_yield: N; shareholder_yield: N }[] }
const mld = (v: N) => (v === null ? null : v / 1e9);
const mln = (v: N) => (v === null ? null : v / 1e6);

export function DividendsPanel({ ticker }: { ticker: string }) {
  const q = useCompany<Dividends>(ticker, "dividends");
  return <Loading q={q}>{(d) => d.empty ? (
    <Empty what="Né dividendi né riacquisti" why="Negli esercizi disponibili l'azienda non ha restituito soldi agli azionisti (o i dati mancano)." />
  ) : (
    <>
      <div className="grid-2">
        <Card title="Soldi restituiti agli azionisti" right={`miliardi di ${d.currency}`}>
          <PeriodChart type="bars" kind="num" title="Dividendi e riacquisti" labels={d.years.map((y) => String(y.year))}
            series={[{ key: "div", label: "Dividendi", values: d.years.map((y) => mld(y.dividends)) },
              { key: "buy", label: "Riacquisti", values: d.years.map((y) => mld(y.buybacks)) }]} />
        </Card>
        <Card title="Rendimento per chi possiede le azioni" right="al prezzo di fine esercizio">
          <PeriodChart type="lines" kind="pct" title="Rendimento per gli azionisti" labels={d.years.map((y) => String(y.year))}
            series={[{ key: "dy", label: "Dividendi", values: d.years.map((y) => y.dividend_yield) },
              { key: "sy", label: "Dividendi + riacquisti", values: d.years.map((y) => y.shareholder_yield) }]} />
        </Card>
      </div>
      <Card>
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Voce</th>{d.years.map((y) => <th key={y.year} className="n">{y.year}</th>)}</tr></thead>
            <tbody>
              {([["Dividendi (mln)", (y) => fmt(mln(y.dividends), "int")], ["Riacquisti (mln)", (y) => fmt(mln(y.buybacks), "int")],
                ["Utile netto (mln)", (y) => fmt(mln(y.net_income), "int")], ["Free cash flow (mln)", (y) => fmt(mln(y.free_cash_flow), "int")],
                ["Azioni (mln)", (y) => fmt(mln(y.shares), "num", { digits: 1 })], ["Dividendo per azione", (y) => fmt(y.dps, "price")],
                ["Dividendi / utile", (y) => fmt(y.payout, "pct")], ["(Dividendi + riacquisti) / FCF", (y) => fmt(y.fcf_payout, "pct")],
                ["Rendimento da dividendi", (y) => fmt(y.dividend_yield, "pct")], ["Rendimento per gli azionisti", (y) => fmt(y.shareholder_yield, "pct")],
              ] as [string, (y: Dividends["years"][number]) => string][]).map(([label, f]) => (
                <tr key={label}><td>{label}</td>{d.years.map((y) => <td key={y.year} className="n">{f(y)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="honest">Rendimenti al prezzo di fine esercizio. Dividendo per azione = dividendi pagati / azioni diluite (approssimato). Riacquisti: soldi spesi per comprare azioni proprie.</p>
      </Card>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- HDS
interface Ownership {
  no_data: boolean; currency: string; has_funds_data: boolean;
  summary: Record<"90" | "365", { buy_value: N; buyers: N; sell_value: N; planned_sell_value?: N }> | null;
  transactions: { filed: string; trans_date: string; owner: string; title: string; relationship: string; kind: string; code: string; shares: N; price: N; value: N; planned: boolean; owned_after: N }[];
  transactions_total: number; all_total: number;
  funds: { fund: string; report_date: string; change: string; shares: N; shares_change: N; value: N; weight: N }[];
}

export function OwnershipPanel({ ticker }: { ticker: string }) {
  const [all, setAll] = useState(false);
  const q = useCompany<Ownership>(ticker, "ownership", all ? "?everything=true" : "");
  return <Loading q={q}>{(o) => o.no_data ? (
    <Empty what="Nessun dato su insider e grandi investitori" why="Si scaricano con l'aggiornamento dei dati reali (archivi trimestrali SEC dei Form 4 e 13F dei fondi seguiti)." />
  ) : (
    <>
      <Card title="Insider" right="Form 4, depositati entro due giorni">
        <p className="honest" style={{ marginTop: 0 }}>Conta soprattutto chi COMPRA con i propri soldi, meglio se in più persone; le vendite dicono poco, quelle programmate (piani 10b5-1) ancora meno.</p>
        {o.summary && (
          <dl className="kv big">
            <div><dt>Acquisti 90 giorni</dt><dd>{fmt(o.summary["90"].buy_value, "money", { currency: o.currency })}</dd></div>
            <div><dt>Chi ha comprato (90 g)</dt><dd>{fmt(o.summary["90"].buyers, "int")}</dd></div>
            <div><dt>Vendite non progr. 90 g</dt><dd>{fmt(o.summary["90"].sell_value, "money", { currency: o.currency })}</dd></div>
            <div><dt>Acquisti 12 mesi</dt><dd>{fmt(o.summary["365"].buy_value, "money", { currency: o.currency })}</dd></div>
            <div><dt>Chi ha comprato (12 m)</dt><dd>{fmt(o.summary["365"].buyers, "int")}</dd></div>
            <div><dt>Vendite non progr. 12 mesi</dt><dd>{fmt(o.summary["365"].sell_value, "money", { currency: o.currency })}</dd></div>
          </dl>
        )}
        <div className="row" style={{ margin: "12px 0 8px" }}>
          <Segmented label="Operazioni" value={all ? "tutte" : "mercato"} onChange={(v) => setAll(v === "tutte")}
            options={[{ value: "mercato", label: "Acquisti e vendite sul mercato" }, { value: "tutte", label: "Tutte le operazioni" }]} />
          <span className="honest">{fmt(o.transactions_total, "int")} operazioni{o.transactions.length < o.transactions_total ? `, mostrate le ${o.transactions.length} più recenti` : ""}</span>
        </div>
        {o.transactions.length === 0 ? <p className="honest">Nessuna operazione {all ? "in archivio" : "sul mercato: solo assegnazioni, esercizi di opzioni e simili"}.</p> : (
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="dense">
              <thead><tr><th>Depositato</th><th>Operazione</th><th>Chi</th><th>Ruolo</th><th>Tipo</th><th className="n">Azioni</th><th className="n">Prezzo</th><th className="n">Valore</th><th>10b5-1</th></tr></thead>
              <tbody>{o.transactions.map((t, i) => (
                <tr key={i} className={t.code === "P" ? "me" : undefined}>
                  <td>{fmt(t.filed, "date_short")}</td><td>{fmt(t.trans_date, "date_short")}</td><td className="clip">{t.owner}</td>
                  <td className="clip">{t.title || t.relationship}</td><td>{t.kind}</td><td className="n">{fmt(t.shares, "int")}</td>
                  <td className="n">{fmt(t.price, "price")}</td><td className="n">{fmt(t.value, "int")}</td><td>{t.planned ? "sì" : ""}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Grandi investitori" right="ultimo 13F di ogni fondo seguito">
        {o.funds.length === 0 ? <p className="honest" style={{ margin: 0 }}>{o.has_funds_data ? "Nessuno dei fondi seguiti possiede questa azienda nel suo ultimo 13F." : "Nessun dato sui fondi."}</p> : (
          <>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Fondo</th><th>Al</th><th>Rispetto al trimestre prima</th><th className="n">Azioni</th><th className="n">Variazione</th><th className="n">Valore ($)</th><th className="n">Peso nel fondo</th></tr></thead>
                <tbody>{o.funds.map((f) => (
                  <tr key={f.fund}><td>{f.fund}</td><td>{fmt(f.report_date, "date_short")}</td><td>{f.change}</td><td className="n">{fmt(f.shares, "int")}</td>
                    <td className="n"><Num v={f.shares_change} kind="pct" change /></td><td className="n">{fmt(f.value, "int")}</td><td className="n">{fmt(f.weight, "pct", { digits: 2 })}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <p className="honest">Posizioni a fine trimestre, depositate fino a 45 giorni dopo: cosa hanno fatto dopo non si sa.</p>
          </>
        )}
      </Card>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- NOTE
interface Research {
  currency: string; fields: Record<"one_liner" | "story" | "catalysts" | "risks" | "change_mind" | "review_date", string>;
  thesis: string; fair_value: N; assumptions: Record<string, number>;
  checklist: { id: number; question: string; help: string; weight: N; score: N }[]; score: N; updated_at: string | null;
  diary: { created_at: string; price: N; expected_value: N; margin_then: N; return_since: N; toward_value: boolean; note: string | null }[];
  diary_chart: { dates: string[]; price: N[]; yours: N[] } | null; toward: { yes: number; total: number };
}

function ResearchForm({ ticker, r }: { ticker: string; r: Research }) {
  const qc = useQueryClient();
  const [f, setF] = useState(r.fields);
  const [scores, setScores] = useState<Record<number, N>>(Object.fromEntries(r.checklist.map((c) => [c.id, c.score])));
  const [thesis, setThesis] = useState(r.thesis);
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: () => api(`/companies/${enc(ticker)}/research`, { method: "PUT", body: JSON.stringify({ fields: f, scores, thesis }) }),
    onSuccess: () => { setSaved(true); qc.invalidateQueries({ queryKey: ["company-more", ticker, "research"] }); qc.invalidateQueries({ queryKey: ["company-pipeline", ticker] }); },
  });
  const set = (k: keyof Research["fields"]) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => { setSaved(false); setF({ ...f, [k]: e.target.value }); };
  return (
    <Card title="La mia ricerca" right="si salva tutto insieme">
      <div className="form-grid">
        <label className="field wide"><span>La tesi in una frase</span><input className="text-input" value={f.one_liner} onChange={set("one_liner")} placeholder="es. Leader con margini stabili, prezzata come se dovesse smettere di crescere" /></label>
        <label className="field wide"><span>La storia: che azienda sarà tra 10 anni</span><textarea rows={3} value={f.story} onChange={set("story")} placeholder="Mercato, vantaggio, come guadagna, cosa cambia nei prossimi anni…" /></label>
        <label className="field"><span>Cosa deve succedere perché io abbia ragione</span><textarea rows={3} value={f.catalysts} onChange={set("catalysts")} /></label>
        <label className="field"><span>Rischi</span><textarea rows={3} value={f.risks} onChange={set("risks")} /></label>
        <label className="field"><span>Cosa mi farebbe cambiare idea</span><textarea rows={2} value={f.change_mind} onChange={set("change_mind")} /></label>
        <label className="field"><span>Prossima revisione (avviso REV in Oggi)</span><input className="text-input" type="date" value={f.review_date} onChange={set("review_date")} /></label>
      </div>
      <div className="sub" style={{ marginTop: 14 }}>Il mio giudizio (voti 1-5; clicca di nuovo un voto per toglierlo)</div>
      {r.checklist.length === 0 ? <p className="honest">La checklist è vuota: le domande si aggiungono in Il mio metodo.</p> : (
        <div className="checklist">
          {r.checklist.map((c) => (
            <div key={c.id} className="check-q" title={c.help}>
              <span>{c.question}</span>
              <div className="pills" role="radiogroup" aria-label={c.question}>
                {[1, 2, 3, 4, 5].map((v) => (
                  <button key={v} type="button" role="radio" aria-checked={scores[c.id] === v} className={`pill${scores[c.id] === v ? " on" : ""}`}
                    onClick={() => { setSaved(false); setScores({ ...scores, [c.id]: scores[c.id] === v ? null : v }); }}>{v}</button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="honest">Domande e pesi si cambiano in Il mio metodo. Il voto compare accanto alle metriche, ma non nei backtest: nel passato non esisteva.</p>
      <label className="field wide"><span>Appunti liberi</span><textarea rows={4} value={thesis} onChange={(e) => { setSaved(false); setThesis(e.target.value); }} placeholder="Quello che non sta nei campi sopra…" /></label>
      <div className="actions">
        <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>Salva la mia ricerca</button>
        {saved && !save.isPending && <span className="pos">✓ Salvata</span>}
        {save.isError && <span className="neg">{save.error.message}</span>}
      </div>
    </Card>
  );
}

export function ResearchPanel({ ticker }: { ticker: string }) {
  const q = useCompany<Research>(ticker, "research");
  return <Loading q={q}>{(r) => (
    <>
      <ResearchForm key={r.updated_at ?? "nuova"} ticker={ticker} r={r} />
      <Card>
        <dl className="kv big">
          <div><dt>Il tuo voto</dt><dd>{r.score === null ? "–" : `${fmt(r.score, "num", { digits: 1 })} / 5`}</dd></div>
          <div><dt>Il tuo fair value</dt><dd>{fmt(r.fair_value, "price", { currency: r.currency })}</dd></div>
          <div><dt>Ultima modifica</dt><dd>{fmt(r.updated_at?.slice(0, 10) ?? null, "date")}</dd></div>
        </dl>
      </Card>
      <Card title="Diario delle valutazioni" right="si salvano dalla funzione Valutazione">
        {r.diary.length === 0 ? <p className="honest" style={{ margin: 0 }}>Ancora nessuna valutazione salvata. Col tempo il diario ti dice se le tue stime erano troppo ottimiste o troppo pessimiste.</p> : (
          <>
            {r.diary_chart && (
              <PeriodChart type="lines" kind="num" zero={false} title="Prezzo e il tuo valore" height={240} labels={r.diary_chart.dates.map((d) => fmt(d, "date_short"))}
                series={[{ key: "p", label: "Prezzo", values: r.diary_chart.price }, { key: "y", label: "Il tuo valore", values: r.diary_chart.yours }]} />
            )}
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>Data</th><th className="n">Prezzo allora</th><th className="n">Il tuo valore</th><th className="n">Margine allora</th><th className="n">Prezzo da allora</th><th>Verso il tuo valore?</th><th>Nota</th></tr></thead>
                <tbody>{r.diary.map((e, i) => (
                  <tr key={i}><td>{fmt(e.created_at.slice(0, 10), "date")}</td><td className="n">{fmt(e.price, "price")}</td><td className="n">{fmt(e.expected_value, "price")}</td>
                    <td className="n"><Num v={e.margin_then} kind="pct" change /></td><td className="n"><Num v={e.return_since} kind="pct" change /></td>
                    <td>{e.toward_value ? <Badge tone="pos">✓ sì</Badge> : <Badge tone="off">✗ no</Badge>}</td><td className="wrap">{e.note ?? ""}</td></tr>
                ))}</tbody>
              </table>
            </div>
            {r.toward.total > 1 && <p className="honest">Il prezzo si è mosso verso il tuo valore in {r.toward.yes} valutazioni su {r.toward.total}. Con poche valutazioni e pochi mesi è ancora presto per giudicare.</p>}
          </>
        )}
      </Card>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- EE
interface Estimates {
  empty: boolean; currency?: string; fx_notes?: string[]; trailing_pe?: N;
  summary?: { pe_ntm: N; ev_sales_ntm: N; eps_growth_fy0: N; eps_growth_fy1: N; revenue_growth_fy0: N; revenue_growth_fy1: N; peg: N; price_target: N; upside: N; as_of: string | null; sources: string[] };
  dcf?: { base_growth: number; consensus_growth: number; gap: number } | null;
  table?: { voce: string; periodo: string; metric: string; avg: N; low: N; high: N; analysts: N; year_ago: N; growth: N; source: string; as_of: string }[];
}

export function EstimatesPanel({ ticker }: { ticker: string }) {
  const qc = useQueryClient();
  const q = useCompany<Estimates>(ticker, "estimates");
  const [csv, setCsv] = useState<string | null>(null);
  const imp = useMutation({
    mutationFn: (text: string) => api<{ rows: number; tickers: string[] }>("/estimates/import", { method: "POST", body: JSON.stringify({ csv: text }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["company-more", ticker, "estimates"] }),
  });
  const upload = (
    <div className="row" style={{ marginTop: 10 }}>
      <label className="btn">Importa da CSV<input type="file" accept=".csv,text/csv" hidden onChange={(e) => {
        const file = e.target.files?.[0]; if (!file) return;
        file.text().then((t) => { setCsv(file.name); imp.mutate(t); });
      }} /></label>
      <span className="honest">Colonne: ticker, metric (eps, revenue, price_target), period (0y, +1y…), avg, low, high, analysts.</span>
      {imp.isSuccess && <span className="pos">✓ {csv}: {imp.data.rows} righe ({imp.data.tickers.join(", ")})</span>}
      {imp.isError && <span className="neg">{imp.error.message}</span>}
    </div>
  );
  return <Loading q={q}>{(e) => e.empty ? (
    <>
      <Empty what="Nessuna stima per questa azienda" why="Importale da un CSV. Con i dati reali le aziende seguite e nella pipeline si aggiornano da sole con l'aggiornamento serale." />
      {upload}
    </>
  ) : (
    <>
      <p className="honest">Consenso degli analisti: solo informazione, non entra nel giudizio. Le medie degli analisti sono storicamente ottimiste, soprattutto oltre l'anno.</p>
      {(e.fx_notes ?? []).map((n) => <Notice key={n} tone={n.includes("esclusi") ? "warn" : "info"}>{n[0].toUpperCase() + n.slice(1)}.</Notice>)}
      <Card>
        <dl className="kv big">
          <div><dt>P/E prossimi 12 mesi</dt><dd>{fmt(e.summary!.pe_ntm, "mult")}</dd></div>
          <div><dt>P/E ultimi 12 mesi</dt><dd>{fmt(e.trailing_pe, "mult")}</dd></div>
          <div><dt>EV/Ricavi 12 mesi</dt><dd>{fmt(e.summary!.ev_sales_ntm, "mult")}</dd></div>
          <div><dt>EPS: quest'anno / prossimo</dt><dd>{fmt(e.summary!.eps_growth_fy0, "pct", { signed: true })} / {fmt(e.summary!.eps_growth_fy1, "pct", { signed: true })}</dd></div>
          <div><dt>Ricavi: quest'anno / prossimo</dt><dd>{fmt(e.summary!.revenue_growth_fy0, "pct", { signed: true })} / {fmt(e.summary!.revenue_growth_fy1, "pct", { signed: true })}</dd></div>
          <div><dt>PEG</dt><dd>{fmt(e.summary!.peg, "num")}</dd></div>
          <div><dt>Prezzo obiettivo ({e.currency})</dt><dd>{fmt(e.summary!.price_target, "price")} <Num v={e.summary!.upside} kind="pct" change digits={0} /></dd></div>
        </dl>
      </Card>
      {e.dcf && (
        <Notice tone={Math.abs(e.dcf.gap) > 0.05 ? "warn" : "info"}>
          Il tuo scenario Base del DCF usa una crescita dei ricavi del {fmt(e.dcf.base_growth, "pct")} l'anno per 5 anni; il consenso si aspetta {fmt(e.dcf.consensus_growth, "pct")} in media nei prossimi due esercizi ({e.dcf.gap > 0 ? "più ottimista" : "più prudente"} di {fmt(Math.abs(e.dcf.gap), "pp")}).
        </Notice>
      )}
      <Card title="Stime per voce e periodo" right="ricavi in milioni">
        <div className="table-wrap">
          <table className="dense">
            <thead><tr><th>Voce</th><th>Periodo</th><th className="n">Media</th><th className="n">Minimo</th><th className="n">Massimo</th><th className="n">Analisti</th><th className="n">Un anno prima</th><th className="n">Crescita</th><th>Fonte</th><th>Al</th></tr></thead>
            <tbody>{(e.table ?? []).map((r, i) => (
              <tr key={i}><td>{r.voce}</td><td>{r.periodo}</td><td className="n">{fmt(r.avg, "num")}</td><td className="n">{fmt(r.low, "num")}</td><td className="n">{fmt(r.high, "num")}</td>
                <td className="n">{fmt(r.analysts, "int")}</td><td className="n">{fmt(r.year_ago, "num")}</td><td className="n"><Num v={r.growth} kind="pct" change /></td><td>{r.source}</td><td>{fmt(r.as_of?.slice(0, 10) ?? null, "date_short")}</td></tr>
            ))}</tbody>
          </table>
        </div>
        {upload}
      </Card>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- AUTO
interface Auto {
  currency: string; quality: { check: string; detail: string; severity: string }[];
  why: { action: string; regime: string; reasoning: string; evaluated: boolean; confidence?: N; threshold?: N; target?: N; value_needed?: N; mf_needed?: N; margin_now?: N; margin_needed?: N } | null;
  dcf: { fair_value?: N; margin?: N; multi_factor?: N; growth?: N; notes?: string[]; error?: string };
  decisions: { as_of: string; action: string; confidence: N; threshold: N; regime: string; price: N; fair_value: N; margin_of_safety: N; reasoning: string }[];
  versions: { period_end: string; metric: string; value: N; filed: string; form: string; source: string; accn: string }[]; versions_total: number;
}

export function AutoPanel({ ticker }: { ticker: string }) {
  const q = useCompany<Auto>(ticker, "auto");
  return <Loading q={q}>{(a) => (
    <>
      <Notice tone="warn" title="Sperimentale:">la strategia automatica originale del progetto (regole e soglie in config.yaml) non è il giudizio della scheda: quello è il Verdetto in cima. Nei backtest ha reso meno dell'indice. BUY/SKIP qui sotto riguardano solo questa strategia.</Notice>
      {a.quality.map((x, i) => <Notice key={i} tone={x.severity === "errore" ? "neg" : "warn"} title="Dati da controllare:">{x.check}: {x.detail}</Notice>)}
      <Card title="All'ultima analisi">
        {a.why ? (
          <>
            <p style={{ marginTop: 0 }}><Badge tone={a.why.action === "BUY" ? "pos" : a.why.action === "BLOCKED" ? "neg" : "neutral"}>{a.why.action}</Badge> {a.why.reasoning} · regime {a.why.regime}</p>
            {a.why.evaluated && (
              <dl className="kv">
                <div><dt>Confidenza</dt><dd>{fmt(a.why.confidence, "score")}</dd></div>
                <div><dt>Obiettivo</dt><dd>{fmt(a.why.target, "score")}</dd></div>
                <div><dt>Margine DCF oggi</dt><dd><Num v={a.why.margin_now} kind="pct" change /></dd></div>
                <div><dt>Margine che servirebbe</dt><dd>{fmt(a.why.margin_needed, "pct")}</dd></div>
              </dl>
            )}
          </>
        ) : <p className="honest" style={{ margin: 0 }}>Il titolo non era nell'ultima analisi della strategia.</p>}
        {a.dcf.error ? <p className="honest">DCF della strategia non disponibile: {a.dcf.error}</p> : (
          <>
            <dl className="kv">
              <div><dt>Fair value del sistema</dt><dd>{fmt(a.dcf.fair_value, "price", { currency: a.currency })}</dd></div>
              <div><dt>Margine DCF</dt><dd><Num v={a.dcf.margin} kind="pct" change /></dd></div>
              <div><dt>Punteggio multi-factor</dt><dd>{fmt(a.dcf.multi_factor, "score")}</dd></div>
              <div><dt>Crescita usata</dt><dd>{fmt(a.dcf.growth, "pct")}</dd></div>
            </dl>
            {(a.dcf.notes ?? []).length > 0 && <p className="honest">{a.dcf.notes!.join(" · ")}</p>}
          </>
        )}
      </Card>
      <Card title="Storico delle decisioni della strategia">
        {a.decisions.length === 0 ? <p className="honest" style={{ margin: 0 }}>Il titolo non è mai entrato in un'analisi della strategia.</p> : (
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="dense">
              <thead><tr><th>Data</th><th>Decisione</th><th className="n">Confidenza</th><th className="n">Soglia</th><th>Regime</th><th className="n">Prezzo</th><th className="n">Fair value DCF</th><th className="n">Margine</th><th>Motivo</th></tr></thead>
              <tbody>{a.decisions.map((x, i) => (
                <tr key={i}><td>{fmt(x.as_of?.slice(0, 10) ?? null, "date_short")}</td><td>{x.action}</td><td className="n">{fmt(x.confidence, "score")}</td><td className="n">{fmt(x.threshold, "score")}</td>
                  <td>{x.regime}</td><td className="n">{fmt(x.price, "price")}</td><td className="n">{fmt(x.fair_value, "price")}</td><td className="n"><Num v={x.margin_of_safety} kind="pct" change /></td><td className="wrap">{x.reasoning}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <details className="details card" style={{ padding: 16 }}>
        <summary>Versioni depositate dei bilanci ({fmt(a.versions_total, "int")} valori)</summary>
        <p className="honest">Ogni valore con la sua data di deposito alla SEC. Un 10-K ripete gli anni precedenti, a volte corretti: il backtest a ogni data usa solo le versioni già depositate. Importi nella valuta del bilancio, non scalati.</p>
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          <table className="dense">
            <thead><tr><th>Periodo al</th><th>Voce</th><th className="n">Valore</th><th>Depositato</th><th>Documento</th><th>Fonte</th></tr></thead>
            <tbody>{a.versions.map((v, i) => (
              <tr key={i}><td>{fmt(v.period_end?.slice(0, 10) ?? null, "date_short")}</td><td>{v.metric}</td><td className="n">{fmt(v.value, "num")}</td>
                <td>{fmt(v.filed?.slice(0, 10) ?? null, "date_short")}</td><td>{v.form}</td><td>{v.source}</td></tr>
            ))}</tbody>
          </table>
        </div>
        {a.versions.length < a.versions_total && <p className="honest">Mostrati i primi {fmt(a.versions.length, "int")}.</p>}
      </details>
    </>
  )}</Loading>;
}

// ---------------------------------------------------------------- TAV: tavolo dei grandi investitori
interface PanelRule { metric: string; label: string; text: string; value_text: string; status: "passed" | "failed" | "missing"; rank: N; need: null | { kind: "price"; price: number; change: number } | { kind: "rank"; rank: number } | { kind: "value"; value: number; text: string } }
interface PanelMember {
  name: string; short: string; family: string | null; who: string; original: string; missing_in_rules: string;
  outcome: string; reason: string; rules: PanelRule[];
  ranking: { metrics: string[]; positions: number; passing: number; position: number | null; selected: boolean } | null;
  history: { since: number; stale: boolean; cagr: N; first: N; second: N; sharpe: N; baseline_second: N } | null;
}
interface Panel {
  note: string; history_note: string; currency: string; data_as_of: string; outside_index: boolean;
  outcomes: Record<string, { label: string; tone: string }>; members: PanelMember[];
  summary: { counts: Record<string, number>; lines: string[] };
}
const OUT_SYM: Record<string, string> = { compra: "▲", piace: "●", quasi: "◐", no: "✗", fuori: "○", dati: "?" };
const RULE_BADGE: Record<string, [string, "pos" | "neg" | "off", string]> = { passed: ["passata", "pos", "✓"], failed: ["mancata", "neg", "✗"], missing: ["dato mancante", "off", "?"] };

function needText(r: PanelRule, cur: string): string {
  const n = r.need;
  if (!n) return "";
  if (n.kind === "price") return `passerebbe a ${fmt(n.price, "price", { currency: cur })} (${fmt(n.change, "pct", { signed: true })})`;
  if (n.kind === "rank") return `servirebbe essere nel ${fmt(1 - n.rank, "pct", { digits: 0 })} migliore (oggi ${fmt(1 - (r.rank ?? 0), "pct", { digits: 0 })} dalla cima)`;
  return n.text;
}

export function InvestorsPanel({ ticker }: { ticker: string }) {
  const q = useCompany<Panel>(ticker, "investors");
  return <Loading q={q}>{(p) => (
    <>
      <Notice tone="info" title="Solo informazione:">{p.note}</Notice>
      <Card title="Il tavolo in sintesi" right={`dati al ${fmt(p.data_as_of?.slice(0, 10) ?? null, "date")}${p.outside_index ? " · fuori dall'S&P 500: posizione ipotetica" : ""}`}>
        <div className="pills" style={{ marginBottom: 10 }}>
          {Object.entries(p.outcomes).map(([k, o]) => p.summary.counts[k] ? <Badge key={k} tone={o.tone as never}>{OUT_SYM[k]} {o.label} · {p.summary.counts[k]}</Badge> : null)}
        </div>
        {p.summary.lines.map((l) => <p key={l} style={{ margin: "4px 0" }}>{l}</p>)}
      </Card>
      <Card title="Investitore per investitore">
        {p.members.map((m) => {
          const o = p.outcomes[m.outcome];
          return (
            <details key={m.name} className="tav-row">
              <summary><Badge tone={o.tone as never}>{OUT_SYM[m.outcome]} {o.label}</Badge><b>{m.short}</b><span className="honest">{m.reason}</span></summary>
              <div className="tav-body">
                {m.rules.length > 0 && (
                  <div className="table-wrap">
                    <table className="dense">
                      <thead><tr><th>Regola</th><th>Condizione</th><th className="n">Oggi</th><th>Cosa dovrebbe cambiare</th></tr></thead>
                      <tbody>{m.rules.map((r, i) => {
                        const [t, tone, sym] = RULE_BADGE[r.status];
                        return <tr key={i}><td><Badge tone={tone}>{sym} {t}</Badge></td><td>{r.text}</td><td className="n">{r.value_text}</td><td className="wrap">{needText(r, p.currency)}</td></tr>;
                      })}</tbody>
                    </table>
                  </div>
                )}
                {m.ranking?.position && <p className="honest">Classifica ({m.ranking.metrics.join(", ") || "capitalizzazione"}): {m.ranking.position}ª su {m.ranking.passing} che passano le regole; ne tiene {m.ranking.positions}.</p>}
                {m.history && <p className="honest">La strategia dal {m.history.since}: {fmt(m.history.cagr, "pct", { signed: true })} l'anno (1ª metà {fmt(m.history.first, "pct", { signed: true })}, 2ª {fmt(m.history.second, "pct", { signed: true })}; indice a pesi uguali nella 2ª {fmt(m.history.baseline_second, "pct", { signed: true })}), Sharpe {fmt(m.history.sharpe, "num")}.{m.history.stale ? " Backtest su dati di un giorno precedente." : ""}</p>}
                <dl className="sheet">
                  <dt>Chi</dt><dd>{m.who}</dd><dt>Il metodo</dt><dd>{m.original}</dd><dt>Cosa manca nelle regole</dt><dd>{m.missing_in_rules}</dd>
                </dl>
              </div>
            </details>
          );
        })}
        <p className="honest">{p.history_note}</p>
      </Card>
    </>
  )}</Loading>;
}
