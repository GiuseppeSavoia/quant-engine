// Client dell'API Python (/api/v1, api/app.py). Valori grezzi: la formattazione la fa fmt.ts. La lingua va in
// X-Lang: con "en" il server traduce i testi del motore (modules/i18n).
import { LANG } from "./i18n";

export const API = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Lang": LANG, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* risposta senza JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- tipi delle risposte
export interface CompanyRow { ticker: string; name: string; sector: string | null; universe: string }
export interface Health { ok: boolean; demo: boolean; database: string; last_update?: string | null }
export interface StripItem { key: string; label: string; value: number; kind: string; change: number | null; change_kind?: string;
  change_1y?: number | null; spark: number[] | null; detail?: string; highs?: number; lows?: number }
export interface MarketStrip { as_of: string | null; items: StripItem[] }

export interface Valued { value: number | null; kind: string }
export interface KeyMetric extends Valued { key: string; label: string }
export interface PriceTarget { buy_below?: number | null; sell_above?: number | null; note?: string }

export interface Header {
  ticker: string; name: string; sector: string | null; sub_industry: string | null; universe: string;
  currency: string; profile: string; price: number | null; change: number | null; price_date: string | null;
  statement_date: string | null; statement_kind: string; market_cap: Valued; key_metrics: KeyMetric[];
  return_1y: Valued; range_52w: { low: number | null; high: number | null }; fair_value: number | null;
  fair_value_margin: number | null; price_target: PriceTarget; followed: boolean;
}

export interface Evidence {
  key: string; label: string; pillar: string; value: number | null; shown: string; reference: string;
  outcome: "favorevole" | "neutro" | "contrario" | "mancante"; score: number | null; weight: number; text: string;
}
export interface Pillar {
  key: string; label: string; score: number | null; mean: number | null; coverage: number; grade: string;
  evidence: Evidence[]; missing: Evidence[];
}
export interface Verdict {
  ticker: string; category: string; label: string; rule: string; profile: string; profile_label: string;
  pillars: Record<string, Pillar>; coverage: number; overall: number | null; alarms: string[]; checks: string[];
  changes: string[]; data_issues: string[]; data_notes: string[]; price: number | null; as_of: string | null;
  disclaimer: string;
}
export interface Company {
  header: Header; verdict: Verdict | null; verdict_error: string | null;
  what_changed: Record<string, unknown>; alerts: Record<string, unknown>[]; elapsed_ms: number;
}

// ---------------------------------------------------------------- valutazione a leve
export type Levers = Record<string, number>;
export interface LeverMeta { key: string; label: string; pct: boolean }
export interface ScenarioResult {
  per_share: number | null; equity_value: number | null; operating_value: number | null; terminal_share: number | null;
  checks: { level: string; message: string }[];
}
export interface Plausibility {
  lever: string; value: number | null; p10: number | null; p50: number | null; p90: number | null;
  percentile: number | null; verdict: string; kind: "pct" | "num";
}
export interface Valuation {
  ticker: string; currency: string; price: number; levers: Record<string, Levers>; lever_meta: LeverMeta[];
  probabilities: Record<string, number>; scenarios: Record<string, ScenarioResult>; expected: number | null;
  implied_growth: number | null; saved: boolean; setup_notes: string[]; notes: string[];
  base: Record<string, number>;
  simulation: null | {
    p10: number | null; p50: number | null; p90: number | null; prob_above_price: number | null; n: number;
    swing: { lever: string; label: string; low: number | null; high: number | null }[];
    histogram: { counts: number[]; edges: number[] };
  };
  plausibility: Plausibility[];
  year_table: Record<string, number | null>[];
}

// ---------------------------------------------------------------- bilanci
export type StatementsView = "annual" | "quarterly_ttm" | "quarterly_single";
export interface Statements {
  ticker: string; view: StatementsView; view_label: string; currency: string;
  views: { value: StatementsView; label: string }[]; empty: boolean; why?: string;
  periods: { label: string; end: string; ttm: boolean; link: { url: string; title: string } | null }[];
  table_from: number;
  charts: { key: string; title: string; type: "bars" | "lines"; kind: "money" | "pct";
    series: { key: string; label: string; values: (number | null)[] }[] }[];
  shares: { values: (number | null)[]; summary: string } | null;
  rows: { key: string; label: string; kind: "money_mln" | "shares_mln" | "pct" | "mult"; values: (number | null)[]; marks: string[] }[];
  marks: Record<string, { symbol: string; why: string }>;
  note: string;
}

// ---------------------------------------------------------------- le mie aziende
export interface WatchRow {
  spark?: number[] | null;
  ticker: string; name: string | null; sector: string | null; currency: string; price: number | null;
  ret_1m: number | null; ret_1y: number | null; pe: number | null; ev_ebit: number | null; fcf_yield: number | null;
  roic: number | null; revenue_growth: number | null; dividend_yield: number | null;
  my_fair_value: number | null; my_margin: number | null; buy_below: number | null; sell_above: number | null;
  buy_margin: number | null; last_filing: string | null; last_form: string | null; has_thesis: boolean;
  category: string | null; verdict: string | null; coverage: number | null; overall: number | null;
  pillar_quality: number | null; pillar_solidity: number | null; pillar_price: number | null;
  pillar_flags: number | null; pillar_insiders: number | null; alert_count: number;
}
export interface AlertItem {
  id: number | null; ticker: string; code: string; tone: string; message: string; created_at: string | null;
  computed?: boolean; unseen?: boolean; cleared_at?: string | null; seen_at?: string | null;
}
export interface MyCompanies {
  as_of: string; count: number; rows: WatchRow[];
  alerts: AlertItem[]; suggestions: { ticker: string; name: string }[];
}

// ---------------------------------------------------------------- esplora
export interface MetricMeta { key: string; label: string; group: string; fmt: string; better: string | null; help: string; unit: string; factor: number }
export interface Rule { metric: string; op: string; value: number }
export interface ExploreCatalog {
  metrics: MetricMeta[]; ops: { value: string; label: string; rank: boolean }[];
  presets: { name: string; description: string; rules: Rule[] }[];
  saved: { name: string; rules: Rule[]; sectors: string[]; filters: { verdicts?: string[]; min_scores?: Record<string, number> } }[];
  universes: string[]; sectors: string[]; pillars: { key: string; label: string; help: string }[];
  verdicts: { value: string; label: string }[]; sorts: { value: string; label: string }[];
  default_columns: string[]; note: string;
}
export interface ExploreRow {
  spark?: number[] | null;
  ticker: string; name: string | null; sector: string | null; currency: string | null; price: number | null;
  category: string | null; verdict: string | null; followed: boolean; my_margin: number | null;
  pillar_quality: number | null; pillar_solidity: number | null; pillar_price: number | null; pillar_flags: number | null;
  values: Record<string, number | null>;
}
export interface ExploreResult {
  universe: string; total: number; count: number; shown: number; sorted_by: string; rules: string[];
  verdict_counts: Record<string, number>; columns: { key: string; label: string; fmt: string; help: string }[];
  currencies: string[]; rows: ExploreRow[]; european: boolean;
}

// ---------------------------------------------------------------- confronta
export interface CompareCompany {
  ticker: string; name: string; currency: string; category: string | null; verdict: string | null; coverage: number | null;
  pillars: { key: string; label: string; score: number | null; grade: string }[];
  price: number | null; fair_value: number | null; fair_margin: number | null; buy_below: number | null;
}
export interface CompareResult {
  tickers: string[]; as_of: string; years: number; price_years: number[]; companies: CompareCompany[];
  groups: { group: string; rows: { key: string; label: string; fmt: string; better: string | null; help: string;
    values: Record<string, number | null>; best: string | null }[] }[];
  charts: { key: string; title: string; note: string; kind: "num" | "pct" | "mult"; dates: string[]; series: Record<string, (number | null)[]> }[];
}

// ---------------------------------------------------------------- concorrenti e documenti
export interface Peers {
  ticker: string; empty: boolean; why?: string; group: string; currency: string;
  columns: { key: string; label: string; fmt: string; better: string | null }[];
  rows: { ticker: string; name: string | null; me: boolean; values: Record<string, number | null> }[];
  versus: { key: string; own: number | null; median: number | null }[];
}
export interface Documents {
  ticker: string; sec_filer: boolean; news_enabled: boolean; filings_total: number; why_no_filings: string;
  news: { published: string; source: string; title: string; link: string }[];
  filings: { when: string; form: string; what: string; important: boolean; url: string | null }[];
}

// ---------------------------------------------------------------- oggi
export interface TodayAlert { id: number; ticker: string; code: string; tone: string; message: string; created_at: string | null; unseen: boolean; move: boolean }
export interface Today {
  as_of: string; source: string; unseen: number; news_mine: boolean; documents_days: number; insiders_loaded: boolean;
  mine: { ticker: string; name: string; last: number | null; change_pct: number | null; ret_1y: number | null; margin: number | null;
    buy_below: number | null; buy_gap: number | null; category: string; verdict: string; rule: string; held: boolean; unseen: number; spark?: number[] | null }[];
  alerts: TodayAlert[]; moves: TodayAlert[];
  news: { published: string; source: string; title: string; link: string; tickers: string[] }[];
  documents: { when: string; ticker: string; name: string; form: string; what: string; important: boolean; url: string | null; mine: boolean }[];
  movers: { up: { ticker: string; name: string; last: number; change_pct: number }[]; down: { ticker: string; name: string; last: number; change_pct: number }[] };
  insiders: { ticker: string; name: string; value: number; buyers: number }[];
  exchanges: { code: string; name: string; local: string; state: string; open: boolean; holiday: string | null; hours: string; until: string; index: string; holidays_complete: boolean }[];
}

// ---------------------------------------------------------------- grafico del prezzo
export interface PriceChart {
  ticker: string; currency: string; benchmark: string; periods: string[]; styles: string[]; period: string; style: string;
  empty: boolean; candles: boolean; freq: "D" | "W" | "M"; bar: { one: string; many: string };
  dates: string[]; open: (number | null)[]; high: (number | null)[]; low: (number | null)[]; close: (number | null)[];
  volume: (number | null)[]; change: (number | null)[]; vol_ma: (number | null)[]; sma50: (number | null)[];
  sma200: (number | null)[]; drawdown: (number | null)[];
  summary: { return: number; high: number; high_date: string; low: number; low_date: string; volatility: number | null; from_high: number; max_drawdown: number };
  references: { label: string; value: number; style: "dash" | "buy" | "sell" | "dot"; in_range: boolean }[];
  filings: string[]; trades: { date: string; price: number; kind: string; label: string; shares: number | null }[];
  bench: { stock: (number | null)[]; benchmark: (number | null)[] } | null; notes: string[];
}

// ---------------------------------------------------------------- diario delle decisioni
export interface JournalEntry {
  id: number; date: string; ticker: string; name: string; action: string; action_label: string; price: number | null;
  currency: string; ret: number | null; bench_ret: number | null; excess: number | null; right: boolean | null;
  days: number | null; reason: string | null; change_mind: string | null; review_date: string | null; review_due: boolean;
  verdict: string | null; review_note: string | null;
}
export interface Journal {
  benchmark: string; actions: { value: string; label: string }[]; verdicts: string[]; min_days: number;
  summary: { decisions: number; judged: number; right: number; rate: number | null; due: number };
  entries: JournalEntry[];
}

// ---------------------------------------------------------------- il mio metodo
export interface Profile { styles: string[]; horizon: string; positions: number; principles: string; red_flags: string; sell_rules: string }
export interface CustomMetric { key: string; label: string; formula: string; fmt: string; better: string | null; description: string | null }
export interface MethodPage {
  profile: Profile; styles: string[]; horizons: string[]; custom_metrics: CustomMetric[];
  formats: { value: string; label: string }[]; formula_names: { key: string; label: string; group: string }[];
  functions: string[]; checklist: { id: number; question: string; help: string; weight: number }[];
}
export interface FormulaPreview {
  computable: number; total: number; median: number | null; p10: number | null; p90: number | null;
  best: { ticker: string; name: string; value: number }[];
}

// ---------------------------------------------------------------- portafoglio
export interface PortfolioPage {
  empty: boolean; kinds: { value: string; label: string }[]; currencies: string[]; tax_note: string; benchmark?: string;
  summary?: { value: number | null; invested: number | null; gain: number | null; gain_pct: number | null; vs_benchmark: number | null;
    realized: number; dividends_net: number; prices_at: string | null } | null;
  notes?: { tone: string; title: string; text: string }[];
  positions?: { ticker: string; name: string; sector: string; quantity: number; avg_cost: number | null; price: number | null;
    currency: string; value_eur: number | null; cost_eur: number | null; weight: number | null; gain_eur: number | null;
    gain_pct: number | null; known: boolean; category: string | null; verdict: string | null }[];
  composition?: { kind: string; name: string; share: number }[];
  history?: { dates: string[]; value: (number | null)[]; invested: (number | null)[]; benchmark: (number | null)[] | null } | null;
  history_missing?: string[];
  transactions?: { id: number; date: string; kind: string; kind_label: string; ticker: string; quantity: number; price: number;
    currency: string; fx: number | null; fees: number | null; note: string | null }[];
  dividends?: { date: string; ticker: string; gross_eur: number | null; us_tax: number | null; it_tax: number | null; net_eur: number | null }[];
  taxes?: { unrealized_gain: number | null; tax_if_sold: number | null; incomplete: boolean;
    carry: { amount: number; year: number; expires: number }[]; by_year: { year: string; net_gain: number; loss_used: number; tax: number }[] } | null;
}

export interface NewsItem { published: string; first_seen: string; source: string; feed: string; group: string; title: string; link: string; tickers: string[]; lang: string }
export interface NewsStream {
  enabled: boolean; saved_at: string | null; total: number; matched: number; limit: number; groups: string[];
  followed: string[]; keep_days: number; items: NewsItem[];
}
export interface Filing8K { when: string; ticker: string; name: string; form: string; what: string; important: boolean; url: string | null; codes: string[] }
export interface FilingsPage { days: number; mine: boolean; followed: string[]; item_labels: { code: string; label: string }[]; companies: number; items: Filing8K[] }
export interface XAccounts { accounts: string[]; height: number }

// ---------------------------------------------------------------- strategie
export interface StrategyItem { name: string; group: string; mine: boolean }
export interface Job { key: string; label: string; state: "queued" | "running" | "done" | "error"; position: number; queued_at: string; started_at: string | null; finished_at: string | null; error: string | null }
export interface StrategyDefinition {
  name: string; description?: string; start?: string;
  universe: { sectors: string[]; watchlist_only: boolean };
  filters: Rule[]; ranking: { metric: string; weight: number }[];
  portfolio: { positions: number; weighting: string; max_weight: number; max_sector: number; rebalance_days: number };
}
export interface PeriodRow { period: string; what: string; total_return: number | null; cagr: number | null; annual_volatility: number | null; sharpe_ratio: number | null; max_drawdown: number | null }
export interface StrategyReport {
  stale: boolean; data_as_of: string | null; benchmark: string;
  summary: { better: boolean; cagr: number; baseline_cagr: number; sharpe: number; baseline_sharpe: number; bench_cagr: number | null } | null;
  periods: PeriodRow[];
  curves: { dates: string[]; series: { key: string; values: (number | null)[] }[]; mid: string | null };
  years: { year: number; strategy: number; baseline: number | null; benchmark: number | null }[];
  facts: { turnover_annual: number | null; total_costs: number | null; avg_exposure: number | null; avg_coverage: number | null };
  variants: number; significance: { text: string; dsr: number | null } | null;
}
export interface StrategyPage extends StrategyItem {
  description: string; investor: { who: string; source: string; original: string; translation: string } | null;
  definition: StrategyDefinition | null; rules: { label: string; text: string }[]; watchlist_only: boolean;
  baseline_name: string; report: StrategyReport | null; job: Job | null;
}
export interface StrategyHoldings { rows: { ticker: string; name: string | null; sector: string | null; weight: number; score: number | null }[]; invested: number }
export interface StrategiesOverview {
  rows: (StrategyItem & { since: number; stale: boolean; all: number | null; first: number | null; second: number | null; sharpe: number | null; drawdown: number | null; turnover: number | null })[];
  missing: string[]; jobs: Job[];
}
export interface StrategyBuilder {
  sectors: string[]; rankable: { key: string; label: string; group: string }[]; excluded: string[];
  presets: { name: string; rules: Rule[] }[]; saved: { name: string; rules: Rule[]; sectors: string[] }[];
  weightings: { value: string; label: string }[]; rebalance: { value: number; label: string }[];
  default: StrategyDefinition; reserved: string[];
}
export interface StrategyPreview { ok: boolean; error: string | null; passing?: number; universe?: number; held?: number }

// ---------------------------------------------------------------- pipeline
export interface PipelineStageInfo { key: string; label: string; missing: string[] }
export interface CompanyPipeline { ticker: string; stage: string | null; label: string | null; note: string | null; stages: PipelineStageInfo[] }
export interface PipelineCard {
  ticker: string; name: string; stage: string; note: string | null; price: number | null; currency: string | null;
  fair_value: number | null; margin: number | null; days: number | null; missing: string[]; verdict: string | null; category: string | null;
}
export interface PipelineBoard {
  stages: { key: string; label: string; cards: PipelineCard[] }[]; count: number;
  history: { ticker: string; stage: string; label: string; note: string | null; at: string }[];
  requirements: { key: string; label: string; stages: string[] }[];
}
