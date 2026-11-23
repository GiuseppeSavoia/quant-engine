"""
Golden Data Lake: unico punto di accesso al database SQLite.

Tabelle:
  companies      anagrafica: ticker, CIK SEC, settore GICS, data di ingresso nell'S&P 500
  index_membership  intervalli di appartenenza all'S&P 500 (ex membri compresi), da Wikipedia
  price_history  prezzi giornalieri OHLCV (rettificati per split e dividendi, alla base di oggi) per ticker
  splits         frazionamenti azionari (ticker, data, rapporto: 10 = 10 nuove azioni per 1 vecchia);
                 servono a riportare le azioni dei bilanci sulla stessa base dei prezzi rettificati
  financials     bilanci in formato lungo con versioni: (ticker, period_end, metric, value, filed, source)
                 `filed` è la data in cui il dato è diventato pubblico: è ciò che rende il
                 backtest point-in-time (anche le correzioni successive restano come versioni)
  fetch_log      quando sono stati scaricati per l'ultima volta i dati di un ticker
  decisions      journal di ogni decisione (BUY / SKIP / BLOCKED) con tutti i numeri
  paper_orders   ordini simulati prodotti dall'execution engine (nessun broker reale)
  backtest_runs  parametri e metriche di ogni backtest
  runs           ogni esecuzione: commit git, configurazione completa, versioni delle librerie
  experiments    prove del laboratorio (webapp): modifiche alla configurazione, metriche e curve
  company_notes  scritte dall'utente nella webapp: tesi d'investimento, fair value e ipotesi del suo DCF
  watchlist      le aziende che l'utente segue (home della webapp)
  saved_screens  le ricerche con filtri salvate dall'utente (pagina Esplora)
  user_strategies le strategie a regole create dall'utente (pagina Strategie)
  custom_metrics le metriche personali (formule sulle metriche del catalogo)
  insider_transactions operazioni degli insider dai Form 4 (data di deposito = quando sono diventate pubbliche)
  thirteenf_holdings   posizioni dei grandi investitori seguiti dai 13F trimestrali
  valuations     diario delle valutazioni: ogni valutazione a scenari salvata, con prezzo e ipotesi di allora
  research_notes scheda di ricerca strutturata (tesi, storia, catalizzatori, rischi, revisione)
  custom_companies aziende aggiunte dall'utente fuori dall'indice (simbolo Yahoo; anagrafica in companies)
  (i dati di bilancio inseriti a mano stanno in financials con fonte "manual" / "manual_ttm")
  checklist_items / checklist_scores  domande della checklist qualitativa e voti dati alle aziende
  user_profile   il metodo dell'utente (stile, orizzonte, regole personali)
  quarterly_financials voci normalizzate per SINGOLO trimestre (fonte "sec_q", con versioni come financials)
  sec_filings    10-Q e 10-K delle aziende seguite visti nell'indice della SEC: da caricare, caricati, senza dati
  portfolio_transactions il portafoglio reale inserito a mano: acquisti, vendite, dividendi (vedi modules/portfolio.py)
  decision_journal diario delle decisioni: comprare, vendere, non comprare, con il perché e la revisione
  foreign_companies aziende europee (FTSE MIB, Euro Stoxx 50): indice, ISIN, LEI, valuta; bilanci ESEF in financials
  sec_events     8-K delle aziende seguite (eventi rilevanti: risultati, acquisizioni, dirigenti...) con le voci
  share_class_counts azioni per classe dalla copertina dei 10-Q/10-K (aziende con più classi, es. Berkshire)
  alerts         storico degli avvisi (modules/alerts.py): uno per condizione finché resta vera (`cleared_at` vuoto),
                 visto dall'utente (`seen_at`); ricompare solo se la condizione cessa e poi torna
  price_targets  soglie di prezzo fissate dall'utente per azienda: compra sotto, vendi sopra, nota
  verdict_history storico dei giudizi (modules/verdict_history.py): uno per (data dei dati, ticker), scritto a fine
                 aggiornamento per tutto l'universo; `verdict_runs` = una riga per scrittura (configurazione, dati a mano)

Archivio completo (file separato, data/sec_facts.db, classe FactsStore): ogni valore depositato nei 10-K e 10-Q,
tutte le etichette. Si ricostruisce dalla SEC, quindi non entra nei backup giornalieri.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
    ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT, country TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS price_history (
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS splits (
    ticker TEXT NOT NULL, date TEXT NOT NULL, ratio REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY, cik INTEGER, name TEXT, sector TEXT, sub_industry TEXT,
    date_added TEXT, in_index INTEGER DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS financials (
    ticker TEXT NOT NULL, period_end TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
    filed TEXT NOT NULL, source TEXT,
    PRIMARY KEY (ticker, period_end, metric, filed)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    ticker TEXT NOT NULL, kind TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, kind)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, timestamp TEXT, as_of TEXT, ticker TEXT, action TEXT,
    confidence REAL, threshold REAL, regime TEXT,
    price REAL, fair_value REAL, margin_of_safety REAL, wacc REAL, beta REAL,
    growth REAL, implied_growth REAL,
    value_score REAL, mf_score REAL, momentum REAL, quality REAL, volatility_score REAL,
    reasoning TEXT, sector TEXT
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, timestamp TEXT, ticker TEXT, side TEXT, order_type TEXT,
    shares REAL, weight REAL, reference_price REAL, limit_price REAL,
    stop_loss REAL, take_profit REAL, est_slippage REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY, timestamp TEXT, params TEXT, metrics TEXT
);
CREATE TABLE IF NOT EXISTS index_membership (
    ticker TEXT NOT NULL, name TEXT, start TEXT, end TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, name TEXT, overrides TEXT, config_hash TEXT,
    config TEXT, git_commit TEXT, git_dirty INTEGER, data_as_of TEXT, metrics TEXT, benchmark_metrics TEXT,
    equity TEXT, benchmark TEXT, research TEXT, seconds REAL
);
CREATE TABLE IF NOT EXISTS insider_transactions (
    accession TEXT NOT NULL, line TEXT NOT NULL, ticker TEXT, owner TEXT, owner_cik TEXT, relationship TEXT,
    title TEXT, trans_date TEXT, filed TEXT, code TEXT, shares REAL, price REAL, value REAL, acquired INTEGER,
    owned_after REAL, planned INTEGER, PRIMARY KEY (accession, line)
);
CREATE INDEX IF NOT EXISTS idx_insider_ticker ON insider_transactions(ticker, filed);
CREATE TABLE IF NOT EXISTS thirteenf_holdings (
    fund_cik INTEGER NOT NULL, fund_name TEXT, accession TEXT NOT NULL, report_date TEXT, filed TEXT, issuer TEXT,
    cusip TEXT NOT NULL, title TEXT, value REAL, shares REAL, ticker TEXT, PRIMARY KEY (fund_cik, accession, cusip)
);
CREATE TABLE IF NOT EXISTS quarterly_financials (
    ticker TEXT NOT NULL, period_end TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
    filed TEXT NOT NULL, source TEXT,
    PRIMARY KEY (ticker, period_end, metric, filed)
);
CREATE TABLE IF NOT EXISTS sec_filings (
    ticker TEXT NOT NULL, accession TEXT NOT NULL, form TEXT, filed TEXT, detected_at TEXT,
    status TEXT, loaded_at TEXT, PRIMARY KEY (ticker, accession)
);
CREATE INDEX IF NOT EXISTS idx_sec_filings_status ON sec_filings(status);
CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, kind TEXT NOT NULL, date TEXT NOT NULL,
    quantity REAL, price REAL, currency TEXT, fx REAL, fees REAL, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS decision_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, date TEXT NOT NULL, action TEXT NOT NULL,
    price REAL, reason TEXT, change_mind TEXT, review_date TEXT, tx_id INTEGER, review_note TEXT, verdict TEXT,
    reviewed_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS foreign_companies (
    ticker TEXT PRIMARY KEY, name TEXT, index_name TEXT, isin TEXT, lei TEXT, country TEXT, sector TEXT,
    currency TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sec_events (
    ticker TEXT NOT NULL, accession TEXT NOT NULL, form TEXT, filed TEXT, cik INTEGER, items TEXT, accepted TEXT,
    PRIMARY KEY (ticker, accession)
);
CREATE INDEX IF NOT EXISTS idx_sec_events_filed ON sec_events(filed);
CREATE TABLE IF NOT EXISTS share_class_counts (
    ticker TEXT NOT NULL, accession TEXT NOT NULL, form TEXT, filed TEXT, period_end TEXT, class_label TEXT NOT NULL,
    shares REAL, PRIMARY KEY (ticker, accession, class_label)
);
CREATE TABLE IF NOT EXISTS valuations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, created_at TEXT, data_as_of TEXT, price REAL,
    expected_value REAL, per_scenario TEXT, probabilities TEXT, scenarios TEXT, simulation TEXT, story TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS research_notes (
    ticker TEXT PRIMARY KEY, one_liner TEXT, story TEXT, catalysts TEXT, risks TEXT, change_mind TEXT,
    review_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS custom_companies (
    ticker TEXT PRIMARY KEY, currency TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS custom_metrics (
    key TEXT PRIMARY KEY, label TEXT, formula TEXT, fmt TEXT, better TEXT, description TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, help TEXT, weight REAL, position INTEGER, active INTEGER
);
CREATE TABLE IF NOT EXISTS checklist_scores (
    ticker TEXT NOT NULL, item_id INTEGER NOT NULL, score REAL, updated_at TEXT, PRIMARY KEY (ticker, item_id)
);
CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS user_strategies (
    name TEXT PRIMARY KEY, definition TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS saved_screens (
    name TEXT PRIMARY KEY, rules TEXT, sectors TEXT, created_at TEXT, filters TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY, added_at TEXT
);
CREATE TABLE IF NOT EXISTS estimates (
    ticker TEXT NOT NULL, period TEXT NOT NULL, metric TEXT NOT NULL, avg REAL, low REAL, high REAL, analysts REAL,
    year_ago REAL, growth REAL, currency TEXT, source TEXT NOT NULL, as_of TEXT NOT NULL,
    PRIMARY KEY (ticker, period, metric, source, as_of)
);
CREATE TABLE IF NOT EXISTS pipeline (
    ticker TEXT PRIMARY KEY, stage TEXT NOT NULL, note TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_log (
    ticker TEXT NOT NULL, stage TEXT NOT NULL, note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_notes (
    ticker TEXT PRIMARY KEY, thesis TEXT, fair_value REAL, assumptions TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, code TEXT NOT NULL, key TEXT NOT NULL,
    message TEXT, data TEXT, created_at TEXT, seen_at TEXT, cleared_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(cleared_at, ticker, code);
CREATE TABLE IF NOT EXISTS price_targets (
    ticker TEXT PRIMARY KEY, buy_below REAL, sell_above REAL, note TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS verdict_history (
    as_of TEXT NOT NULL, ticker TEXT NOT NULL, run_id TEXT, config_hash TEXT, universe TEXT, category TEXT,
    profile TEXT, quality REAL, solidity REAL, price_pillar REAL, flags REAL, insiders REAL, coverage REAL,
    overall REAL, alarms INTEGER, checks INTEGER, errors INTEGER, price REAL, user_fair_value REAL, rule TEXT,
    PRIMARY KEY (as_of, ticker)
);
CREATE INDEX IF NOT EXISTS idx_verdict_history_ticker ON verdict_history(ticker, as_of);
CREATE TABLE IF NOT EXISTS verdict_runs (
    run_id TEXT PRIMARY KEY, as_of TEXT, config_hash TEXT, user_data TEXT, written_at TEXT, tickers INTEGER,
    seconds REAL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, git_commit TEXT, git_dirty INTEGER,
    config_hash TEXT, config TEXT, argv TEXT, versions TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_date ON price_history(date);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker, timestamp);
CREATE INDEX IF NOT EXISTS idx_financials_filed ON financials(filed);
"""

# Schemi riconosciuti delle versioni precedenti
_PRICE_COLS = {"ticker", "date", "open", "high", "low", "close", "volume"}
_FIN_V1_COLS = {"ticker", "period_end", "metric", "value"}
_FIN_V2_COLS = _FIN_V1_COLS | {"filed", "source"}
# Data di pubblicazione stimata per i bilanci senza data (es. Yahoo): fine esercizio + 90 giorni
ESTIMATED_FILING_LAG_DAYS = 90


class DBManager:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ setup
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._migrate(conn)
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "decisions", "sector", "TEXT")
            self._ensure_column(conn, "experiments", "period", "TEXT")
            self._ensure_column(conn, "saved_screens", "filters", "TEXT")  # giudizio e punteggi minimi (JSON)
            if version != SCHEMA_VERSION:  # scrivere sempre cambierebbe il file a ogni apertura (cache della webapp)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _ensure_column(cls, conn: sqlite3.Connection, table: str, column: str, kind: str) -> None:
        if column not in cls._columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    @classmethod
    def _migrate(cls, conn: sqlite3.Connection) -> None:
        """Nessun dato viene cancellato:
        - tabelle del progetto originale con colonne diverse -> rinominate legacy_<nome>
        - financials v1 (senza data di pubblicazione) -> convertita in v2 con data stimata"""
        price_cols = cls._columns(conn, "price_history")
        if price_cols and price_cols != _PRICE_COLS:
            cls._rename_legacy(conn, "price_history")
        fin_cols = cls._columns(conn, "financials")
        if fin_cols == _FIN_V1_COLS:
            logger.info("Migrazione bilanci allo schema con data di pubblicazione")
            conn.execute("ALTER TABLE financials RENAME TO financials_v1")
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO financials (ticker, period_end, metric, value, filed, source) "
                f"SELECT ticker, period_end, metric, value, date(period_end, '+{ESTIMATED_FILING_LAG_DAYS} days'), "
                "'yahoo' FROM financials_v1"
            )
            conn.execute("DROP TABLE financials_v1")
        elif fin_cols and fin_cols != _FIN_V2_COLS:
            cls._rename_legacy(conn, "financials")

    @staticmethod
    def _rename_legacy(conn: sqlite3.Connection, table: str) -> None:
        logger.warning("Tabella '%s' con schema vecchio: rinominata in 'legacy_%s'", table, table)
        conn.execute(f"DROP TABLE IF EXISTS legacy_{table}")
        conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")

    # ----------------------------------------------------------------- backup
    def backup(self, dest_dir: str | Path, keep: int = 7, now: datetime | None = None) -> Path | None:
        """Copia coerente del database (API di backup di SQLite, sicura anche a database aperto).
        Al massimo una copia al giorno; si tengono le `keep` più recenti. keep = 0 disattiva."""
        if keep <= 0:
            return None
        now = now or datetime.now()
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(self.db_path).stem
        existing = sorted(dest_dir.glob(f"{stem}_*.db"))
        if any(p.name.startswith(f"{stem}_{now:%Y%m%d}") for p in existing):
            return None  # già fatto oggi
        target = dest_dir / f"{stem}_{now:%Y%m%d_%H%M%S}.db"
        src, dst = sqlite3.connect(self.db_path), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        for old in sorted(dest_dir.glob(f"{stem}_*.db"))[:-keep]:
            old.unlink()
        logger.info("Backup del database: %s", target)
        return target

    # ----------------------------------------------------------------- prezzi
    def upsert_prices(self, df: pd.DataFrame) -> int:
        """df in formato lungo: ticker, date, open, high, low, close, volume."""
        if df is None or df.empty:
            return 0
        cols = ["ticker", "date", "open", "high", "low", "close", "volume"]
        data = df.copy()
        data["date"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
        for col in cols:
            if col not in data.columns:
                data[col] = None
        rows = [
            tuple(None if pd.isna(v) else (float(v) if i >= 2 else str(v)) for i, v in enumerate(rec))
            for rec in data[cols].itertuples(index=False, name=None)
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO price_history (ticker, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def replace_prices(self, ticker: str, df: pd.DataFrame) -> int:
        """Sostituisce l'intero storico di un ticker (dopo uno split cambia la base di tutti i prezzi)."""
        if df is None or df.empty:
            return 0
        with self._conn() as conn:
            conn.execute("DELETE FROM price_history WHERE ticker = ?", (ticker,))
        return self.upsert_prices(df[df["ticker"] == ticker])

    def last_price_dates(self) -> dict[str, pd.Timestamp]:
        with self._conn() as conn:
            rows = conn.execute("SELECT ticker, MAX(date) FROM price_history GROUP BY ticker").fetchall()
        return {t: pd.Timestamp(d) for t, d in rows if d}

    def first_price_dates(self) -> dict[str, pd.Timestamp]:
        with self._conn() as conn:
            rows = conn.execute("SELECT ticker, MIN(date) FROM price_history GROUP BY ticker").fetchall()
        return {t: pd.Timestamp(d) for t, d in rows if d}

    def load_prices(self, tickers: Iterable[str] | None = None, start: str | None = None) -> pd.DataFrame:
        query = "SELECT ticker, date, open, high, low, close, volume FROM price_history"
        clauses, params = [], []
        if tickers is not None:
            clauses.append("ticker IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(tickers)))
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY date"
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        df["date"] = pd.to_datetime(df["date"])
        return df

    # ------------------------------------------------------------------ split
    def upsert_splits(self, df: pd.DataFrame) -> int:
        """df: ticker, date, ratio. Rapporti nulli o pari a 1 non sono split e vengono ignorati."""
        if df is None or df.empty:
            return 0
        data = df[(df["ratio"] > 0) & (df["ratio"] != 1)]
        rows = [(str(t), pd.Timestamp(d).strftime("%Y-%m-%d"), float(r))
                for t, d, r in data[["ticker", "date", "ratio"]].itertuples(index=False, name=None)]
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO splits (ticker, date, ratio) VALUES (?, ?, ?)", rows)
        return len(rows)

    def load_splits(self, tickers: Iterable[str] | None = None) -> pd.DataFrame:
        query = "SELECT ticker, date, ratio FROM splits"
        params: list = []
        if tickers is not None:
            query += " WHERE ticker IN (SELECT value FROM json_each(?))"
            params = [json.dumps(list(tickers))]
        with self._conn() as conn:
            df = pd.read_sql_query(query + " ORDER BY ticker, date", conn, params=params)
        df["date"] = pd.to_datetime(df["date"])
        return df

    # --------------------------------------------------------------- bilanci
    def upsert_financials(self, df: pd.DataFrame, source: str | None = None) -> int:
        """df in formato lungo: ticker, period_end, metric, value[, filed, source].
        Senza `filed` la data di pubblicazione viene stimata (fine esercizio + 90 giorni)."""
        if df is None or df.empty:
            return 0
        data = df.dropna(subset=["value"]).copy()
        data["period_end"] = pd.to_datetime(data["period_end"])
        if "filed" not in data.columns:
            data["filed"] = pd.NaT
        data["filed"] = pd.to_datetime(data["filed"]).fillna(
            data["period_end"] + pd.Timedelta(days=ESTIMATED_FILING_LAG_DAYS))
        if "source" not in data.columns:
            data["source"] = source
        elif source is not None:
            data["source"] = data["source"].fillna(source)
        data["period_end"] = data["period_end"].dt.strftime("%Y-%m-%d")
        data["filed"] = data["filed"].dt.strftime("%Y-%m-%d")
        rows = [
            (str(t), str(p), str(m), float(v), str(f), None if s is None or pd.isna(s) else str(s))
            for t, p, m, v, f, s in data[["ticker", "period_end", "metric", "value", "filed", "source"]]
            .itertuples(index=False, name=None)
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO financials (ticker, period_end, metric, value, filed, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def replace_financials(self, ticker: str, df: pd.DataFrame, sources: str | Iterable[str]) -> int:
        """Sostituisce tutti i bilanci di `ticker` provenienti da `sources` (es. "sec" e "sec_ttm"). Per le
        fonti che restituiscono sempre lo storico completo: così spariscono anche i valori che il lettore
        ora scarta."""
        sources = [sources] if isinstance(sources, str) else list(sources)
        with self._conn() as conn:
            conn.executemany("DELETE FROM financials WHERE ticker = ? AND source = ?", [(ticker, s) for s in sources])
        return self.upsert_financials(df, source=sources[0] if len(sources) == 1 else None)

    def load_financials(self, tickers: Iterable[str] | None = None) -> pd.DataFrame:
        query = "SELECT ticker, period_end, metric, value, filed, source FROM financials"
        params: list = []
        if tickers is not None:
            tickers = list(tickers)
            query += " WHERE ticker IN (SELECT value FROM json_each(?))"
            params = [json.dumps(tickers)]
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        df["period_end"] = pd.to_datetime(df["period_end"])
        df["filed"] = pd.to_datetime(df["filed"])
        return df

    def last_period_ends(self) -> dict[str, pd.Timestamp]:
        with self._conn() as conn:
            rows = conn.execute("SELECT ticker, MAX(period_end) FROM financials GROUP BY ticker").fetchall()
        return {t: pd.Timestamp(d) for t, d in rows if d}

    # ------------------------------------------------------------- anagrafica
    def replace_index_members(self, df: pd.DataFrame) -> None:
        """Imposta i membri attuali dell'indice; gli ex membri restano in tabella con in_index = 0."""
        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for r in df.itertuples(index=False):
            cik = getattr(r, "cik", None)
            added = getattr(r, "date_added", None)
            rows.append((r.ticker, None if cik is None or pd.isna(cik) else int(cik), getattr(r, "name", None),
                         getattr(r, "sector", None), getattr(r, "sub_industry", None),
                         None if added is None or pd.isna(added) else pd.Timestamp(added).strftime("%Y-%m-%d"), now))
        with self._conn() as conn:
            conn.execute("UPDATE companies SET in_index = 0")
            conn.executemany(
                "INSERT INTO companies (ticker, cik, name, sector, sub_industry, date_added, in_index, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET cik = COALESCE(excluded.cik, companies.cik), name = excluded.name, "
                "sector = excluded.sector, sub_industry = excluded.sub_industry, date_added = excluded.date_added, "
                "in_index = 1, updated_at = excluded.updated_at",
                rows,
            )

    def replace_membership(self, df: pd.DataFrame) -> None:
        def day(v):
            return None if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")

        rows = [(r.ticker, r.name, day(r.start), day(r.end)) for r in df.itertuples(index=False)]
        with self._conn() as conn:
            conn.execute("DELETE FROM index_membership")
            conn.executemany("INSERT INTO index_membership (ticker, name, start, end) VALUES (?, ?, ?, ?)", rows)

    def load_membership(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT ticker, name, start, end FROM index_membership", conn)
        df["start"] = pd.to_datetime(df["start"])
        df["end"] = pd.to_datetime(df["end"])
        return df

    def set_ciks(self, mapping: dict[str, int]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO companies (ticker, cik, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET cik = excluded.cik, updated_at = excluded.updated_at",
                [(t, int(c), now) for t, c in mapping.items()],
            )

    def company_ciks(self) -> dict[str, int | None]:
        with self._conn() as conn:
            rows = conn.execute("SELECT ticker, cik FROM companies").fetchall()
        return {t: c for t, c in rows}

    def load_companies(self, only_index: bool = False) -> pd.DataFrame:
        query = "SELECT ticker, cik, name, sector, sub_industry, date_added, in_index FROM companies"
        if only_index:
            query += " WHERE in_index = 1"
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn)
        df["date_added"] = pd.to_datetime(df["date_added"])
        return df

    # ------------------------------------------------------------- fetch log
    def mark_fetched(self, ticker: str, kind: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fetch_log (ticker, kind, fetched_at) VALUES (?, ?, ?)",
                (ticker, kind, datetime.now().isoformat(timespec="seconds")),
            )

    def mark_fetched_many(self, tickers: list[str], kind: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO fetch_log (ticker, kind, fetched_at) VALUES (?, ?, ?)",
                             [(t, kind, now) for t in tickers])

    def fetched_tickers(self, kind: str) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT ticker FROM fetch_log WHERE kind = ?", (kind,))}

    def last_fetched(self, ticker: str, kind: str) -> datetime | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT fetched_at FROM fetch_log WHERE ticker = ? AND kind = ?", (ticker, kind)
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    # --------------------------------------------------------------- journal
    def log_decisions(self, run_id: str, decisions: list[dict]) -> None:
        cols = [
            "as_of", "ticker", "action", "confidence", "threshold", "regime", "price", "fair_value",
            "margin_of_safety", "wacc", "beta", "growth", "implied_growth", "value_score", "mf_score",
            "momentum", "quality", "volatility_score", "reasoning", "sector",
        ]
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(run_id, now, *[_sql_value(d.get(c)) for c in cols]) for d in decisions]
        with self._conn() as conn:
            conn.executemany(
                f"INSERT INTO decisions (run_id, timestamp, {', '.join(cols)}) "
                f"VALUES ({', '.join('?' * (len(cols) + 2))})",
                rows,
            )

    def log_orders(self, run_id: str, orders: list[dict]) -> None:
        cols = [
            "ticker", "side", "order_type", "shares", "weight", "reference_price", "limit_price",
            "stop_loss", "take_profit", "est_slippage", "status",
        ]
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(run_id, now, *[_sql_value(o.get(c)) for c in cols]) for o in orders]
        with self._conn() as conn:
            conn.executemany(
                f"INSERT INTO paper_orders (run_id, timestamp, {', '.join(cols)}) "
                f"VALUES ({', '.join('?' * (len(cols) + 2))})",
                rows,
            )

    def log_backtest(self, run_id: str, params: dict, metrics: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO backtest_runs (run_id, timestamp, params, metrics) VALUES (?, ?, ?, ?)",
                (run_id, datetime.now().isoformat(timespec="seconds"),
                 json.dumps(params, default=str), json.dumps(metrics, default=str)),
            )

    def log_run(self, run_id: str, info: dict) -> None:
        dirty = info.get("git_dirty")
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, started_at, git_commit, git_dirty, config_hash, config, argv, "
                "versions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, info.get("started_at"), info.get("git_commit"), None if dirty is None else int(dirty),
                 info.get("config_hash"), json.dumps(info.get("config"), default=str),
                 json.dumps(info.get("argv")), json.dumps(info.get("versions"))),
            )

    # ----------------------------------------------------------- letture webapp
    def run_exists(self, run_id: str) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is not None

    def latest_run_id(self) -> str | None:
        """Ultima esecuzione che ha registrato decisioni (cioè un'analisi completa)."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(run_id) FROM decisions").fetchone()
        return row[0] if row else None

    def load_decisions(self, run_id: str | None = None, ticker: str | None = None) -> pd.DataFrame:
        clauses, params = [], []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        query = "SELECT * FROM decisions" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY id"
        with self._conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def load_orders(self, run_id: str) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM paper_orders WHERE run_id = ? ORDER BY weight DESC", conn,
                                     params=[run_id])

    def load_backtest_runs(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT run_id, timestamp, params, metrics FROM backtest_runs ORDER BY run_id",
                                   conn)
        df["params"] = df["params"].map(json.loads)
        df["metrics"] = df["metrics"].map(json.loads)
        return df

    def last_run_id(self, before: str | None = None) -> str | None:
        """Ultima esecuzione di main.py (cambia quando cambiano i dati: chiave di cache della webapp).
        `before`: solo quelle precedenti (es. a un aggiornamento ancora in corso)."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(run_id) FROM runs WHERE ? IS NULL OR run_id < ?", (before, before)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------- laboratorio
    def log_experiment(self, exp) -> int:
        """Salva un esperimento del laboratorio (modules.lab.Experiment); restituisce l'id."""
        def series_json(s: pd.Series) -> str:
            return json.dumps({pd.Timestamp(k).strftime("%Y-%m-%d"): float(v) for k, v in s.dropna().items()})

        info = exp.info or {}
        dirty = info.get("git_dirty")
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO experiments (created_at, name, overrides, config_hash, config, git_commit, git_dirty, "
                "data_as_of, metrics, benchmark_metrics, equity, benchmark, research, seconds, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), exp.name, json.dumps(exp.overrides),
                 info.get("config_hash"), json.dumps(exp.config, default=str), info.get("git_commit"),
                 None if dirty is None else int(dirty), exp.data_as_of, json.dumps(exp.metrics, default=str),
                 json.dumps(exp.benchmark_metrics, default=str), series_json(exp.equity),
                 series_json(exp.benchmark), exp.research.to_json(orient="records") if not exp.research.empty else None,
                 exp.seconds, getattr(exp, "period", "all")),
            )
            return int(cur.lastrowid)

    def load_experiments(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM experiments ORDER BY id", conn)
        return self._parse_experiments(df)

    def find_experiment(self, config_hash: str, period: str, data_as_of: str | None) -> dict | None:
        """Ultimo esperimento con la stessa configurazione, periodo e data dei dati (per non rifarlo).
        Con `data_as_of` None: il più recente con quella configurazione, a qualunque data dei dati."""
        query = "SELECT * FROM experiments WHERE config_hash = ? AND COALESCE(period, 'all') = ?"
        params = [config_hash, period]
        if data_as_of is not None:
            query += " AND data_as_of = ?"
            params.append(data_as_of)
        with self._conn() as conn:
            df = pd.read_sql_query(query + " ORDER BY data_as_of DESC, id DESC LIMIT 1", conn, params=params)
        return None if df.empty else self._parse_experiments(df).iloc[0].to_dict()

    def count_variants(self) -> int:
        """Configurazioni diverse provate negli esperimenti (laboratorio e strategie)."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(DISTINCT config_hash) FROM experiments").fetchone()[0])

    @staticmethod
    def _parse_experiments(df: pd.DataFrame) -> pd.DataFrame:
        for col in ("overrides", "metrics", "benchmark_metrics", "config"):
            df[col] = df[col].map(lambda v: json.loads(v) if v else {})
        for col in ("equity", "benchmark"):
            df[col] = df[col].map(lambda v: pd.Series(json.loads(v), dtype=float).rename(index=pd.Timestamp)
                                  if v else pd.Series(dtype=float))
        df["research"] = df["research"].map(lambda v: pd.read_json(io.StringIO(v)) if v else pd.DataFrame())
        df["period"] = df["period"].fillna("all")
        return df

    # -------------------------------------------------------- trimestri singoli
    def replace_quarterly(self, ticker: str, df: pd.DataFrame) -> int:
        """Sostituisce tutti i trimestri di `ticker` (la SEC dà sempre lo storico completo)."""
        rows = [] if df is None or df.empty else [
            (r.ticker, _sql_value(r.period_end), r.metric, float(r.value), _sql_value(r.filed), r.source)
            for r in df.itertuples(index=False)]
        with self._conn() as conn:
            conn.execute("DELETE FROM quarterly_financials WHERE ticker = ?", (ticker,))
            conn.executemany("INSERT OR REPLACE INTO quarterly_financials VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def load_quarterly(self, tickers: Iterable[str] | None = None, metrics: Iterable[str] | None = None) -> pd.DataFrame:
        query, params, where = "SELECT ticker, period_end, metric, value, filed, source FROM quarterly_financials", [], []
        if tickers is not None:
            where.append("ticker IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(tickers)))
        if metrics is not None:
            where.append("metric IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(metrics)))
        if where:
            query += " WHERE " + " AND ".join(where)
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        df["period_end"] = pd.to_datetime(df["period_end"])
        df["filed"] = pd.to_datetime(df["filed"])
        return df

    # ------------------------------------------------- depositi SEC in arrivo
    def add_filings(self, df: pd.DataFrame) -> int:
        """Registra i 10-Q/10-K visti nell'indice SEC (ticker, accession, form, filed) come "da caricare";
        quelli già noti restano come sono. Restituisce quanti sono nuovi."""
        if df is None or df.empty:
            return 0
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(r.ticker, r.accession, r.form, _sql_value(r.filed), now) for r in df.itertuples(index=False)]
        with self._conn() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO sec_filings (ticker, accession, form, filed, detected_at, status) "
                             "VALUES (?, ?, ?, ?, ?, 'da caricare')", rows)
            return conn.total_changes - before

    def pending_filings(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM sec_filings WHERE status = 'da caricare' ORDER BY filed", conn)

    def load_filings(self, since: str | None = None) -> pd.DataFrame:
        query, params = "SELECT * FROM sec_filings", []
        if since:
            query += " WHERE filed >= ?"
            params = [since]
        with self._conn() as conn:
            return pd.read_sql_query(query + " ORDER BY filed DESC, ticker", conn, params=params)

    def settle_filings(self, ticker: str, accessions: set[str], give_up_days: int) -> None:
        """Dopo un download dei dati di `ticker`: i depositi presenti nei dati diventano "caricati"; quelli
        ancora assenti dopo `give_up_days` "non nei dati SEC" (10-K/A con le sole parti testuali, o
        documenti che l'API companyfacts non riporta, es. alcuni depositi congiunti di utility e REIT)."""
        now = datetime.now()
        with self._conn() as conn:
            pending = conn.execute("SELECT accession, detected_at FROM sec_filings WHERE ticker = ? AND "
                                   "status = 'da caricare'", (ticker,)).fetchall()
            for accession, detected in pending:
                if accession in accessions:
                    status = "caricato"
                elif detected and now - datetime.fromisoformat(detected) > pd.Timedelta(days=give_up_days):
                    status = "non nei dati SEC"
                else:
                    continue
                conn.execute("UPDATE sec_filings SET status = ?, loaded_at = ? WHERE ticker = ? AND accession = ?",
                             (status, now.isoformat(timespec="seconds"), ticker, accession))

    # ------------------------------------------------------- portafoglio e diario delle decisioni
    def add_transaction(self, tx: dict) -> int:
        cols = ["ticker", "kind", "date", "quantity", "price", "currency", "fx", "fees", "note"]
        with self._conn() as conn:
            cur = conn.execute(f"INSERT INTO portfolio_transactions ({', '.join(cols)}, created_at) VALUES "
                               f"({', '.join('?' * len(cols))}, ?)",
                               [_sql_value(tx.get(c)) for c in cols] + [datetime.now().isoformat(timespec="seconds")])
            return int(cur.lastrowid)

    def update_transaction(self, tx_id: int, tx: dict) -> None:
        """Modifica un'operazione (stesse colonne di add_transaction); id e data di inserimento restano."""
        cols = ["ticker", "kind", "date", "quantity", "price", "currency", "fx", "fees", "note"]
        with self._conn() as conn:
            conn.execute(f"UPDATE portfolio_transactions SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                         [_sql_value(tx.get(c)) for c in cols] + [int(tx_id)])

    def delete_transaction(self, tx_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM portfolio_transactions WHERE id = ?", (int(tx_id),))

    def load_transactions(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM portfolio_transactions ORDER BY date, id", conn)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def add_decision(self, d: dict) -> int:
        cols = ["ticker", "date", "action", "price", "reason", "change_mind", "review_date", "tx_id"]
        with self._conn() as conn:
            cur = conn.execute(f"INSERT INTO decision_journal ({', '.join(cols)}, created_at) VALUES "
                               f"({', '.join('?' * len(cols))}, ?)",
                               [_sql_value(d.get(c)) for c in cols] + [datetime.now().isoformat(timespec="seconds")])
            return int(cur.lastrowid)

    def review_decision(self, dec_id: int, note: str, verdict: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE decision_journal SET review_note = ?, verdict = ?, reviewed_at = ? WHERE id = ?",
                         (note, verdict, datetime.now().isoformat(timespec="seconds"), int(dec_id)))

    def delete_decision(self, dec_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM decision_journal WHERE id = ?", (int(dec_id),))

    def load_decisions_journal(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM decision_journal ORDER BY date DESC, id DESC", conn)
        for c in ("date", "review_date"):
            df[c] = pd.to_datetime(df[c])
        return df

    # ------------------------------------------------------- aziende europee
    def upsert_foreign(self, df: pd.DataFrame) -> None:
        """Aziende europee: in foreign_companies e in companies (fuori dall'S&P 500, in_index = 0)."""
        now = datetime.now().isoformat(timespec="seconds")
        val = lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)  # noqa: E731
        with self._conn() as conn:
            for r in df.itertuples(index=False):
                conn.execute("INSERT OR REPLACE INTO foreign_companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (r.ticker, val(r.name), val(r.index_name), val(r.isin), val(r.lei), val(r.country),
                              val(r.sector), val(r.currency), now))
                conn.execute("INSERT INTO companies (ticker, name, sector, in_index, updated_at) VALUES (?, ?, ?, 0, ?) "
                             "ON CONFLICT(ticker) DO UPDATE SET name = excluded.name, sector = excluded.sector, "
                             "updated_at = excluded.updated_at", (r.ticker, val(r.name), val(r.sector), now))

    def load_foreign(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM foreign_companies ORDER BY ticker", conn)

    # ------------------------------------------------------- 8-K (eventi)
    def add_events(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows = [(r.ticker, r.accession, r.form, _sql_value(r.filed), int(r.cik)) for r in df.itertuples(index=False)]
        with self._conn() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO sec_events (ticker, accession, form, filed, cik) "
                             "VALUES (?, ?, ?, ?, ?)", rows)
            return conn.total_changes - before

    def events_without_items(self, limit: int) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT DISTINCT accession, cik FROM sec_events WHERE items IS NULL "
                                     "ORDER BY filed DESC LIMIT ?", conn, params=[int(limit)])

    def set_event_items(self, accession: str, items: str, accepted) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE sec_events SET items = ?, accepted = ? WHERE accession = ?",
                         (items, accepted.isoformat() if accepted is not None else None, accession))

    def load_events(self, tickers=None, since: str | None = None) -> pd.DataFrame:
        query, params, where = "SELECT * FROM sec_events", [], []
        if tickers is not None:
            where.append("ticker IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(tickers)))
        if since:
            where.append("filed >= ?")
            params.append(since)
        if where:
            query += " WHERE " + " AND ".join(where)
        with self._conn() as conn:
            df = pd.read_sql_query(query + " ORDER BY COALESCE(accepted, filed) DESC", conn, params=params)
        df["filed"] = pd.to_datetime(df["filed"])
        df["accepted"] = pd.to_datetime(df["accepted"])
        return df

    # ------------------------------------------------------- azioni per classe
    def share_class_accessions(self) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT accession FROM share_class_counts")}

    def add_share_class_counts(self, rows: list[tuple]) -> None:
        """(ticker, accession, form, filed, period_end, class_label, shares); shares None = copertina senza azioni."""
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO share_class_counts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def load_share_class_counts(self, ticker: str) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM share_class_counts WHERE ticker = ? ORDER BY filed", conn,
                                     params=[ticker])

    # ------------------------------------------------------------- insider e 13F
    def upsert_insiders(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        day = lambda v: None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")  # noqa: E731
        num = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
        rows = [(r.accession, str(r.line), r.ticker, r.owner, r.owner_cik, r.relationship, r.title, day(r.trans_date),
                 day(r.filed), r.code, num(r.shares), num(r.price), num(r.value), int(bool(r.acquired)),
                 num(r.owned_after), int(bool(r.planned))) for r in df.itertuples(index=False)]
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO insider_transactions VALUES "
                             "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def insider_accessions(self) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT accession FROM insider_transactions")}

    def load_insiders(self, tickers=None, codes=None) -> pd.DataFrame:
        query, params, where = "SELECT * FROM insider_transactions", [], []
        if tickers is not None:
            where.append("ticker IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(tickers)))
        if codes is not None:
            where.append("code IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(codes)))
        if where:
            query += " WHERE " + " AND ".join(where)
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        for col in ("trans_date", "filed"):
            df[col] = pd.to_datetime(df[col])
        for col in ("acquired", "planned"):
            df[col] = df[col].astype(bool)
        return df

    def upsert_thirteenf(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        day = lambda v: pd.Timestamp(v).strftime("%Y-%m-%d")  # noqa: E731
        rows = [(int(r.fund_cik), r.fund_name, r.accession, day(r.report_date), day(r.filed), r.issuer, r.cusip,
                 r.title, float(r.value), float(r.shares), None if pd.isna(r.ticker) else r.ticker)
                for r in df.itertuples(index=False)]
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO thirteenf_holdings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def scale_thirteenf_values(self, factors: dict[str, float]) -> None:
        """Moltiplica i valori di interi depositi 13F (correzione dell'unità, superinvestors.fix_units)."""
        with self._conn() as conn:
            conn.executemany("UPDATE thirteenf_holdings SET value = value * ? WHERE accession = ?",
                             [(float(f), a) for a, f in factors.items()])

    def thirteenf_unmatched(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT issuer FROM thirteenf_holdings WHERE ticker IS NULL")]

    def set_thirteenf_tickers(self, mapping: dict[str, str]) -> None:
        with self._conn() as conn:
            conn.executemany("UPDATE thirteenf_holdings SET ticker = ? WHERE issuer = ? AND ticker IS NULL",
                             [(t, i) for i, t in mapping.items()])

    def thirteenf_accessions(self) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT accession FROM thirteenf_holdings")}

    def load_thirteenf(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM thirteenf_holdings", conn)
        for col in ("report_date", "filed"):
            df[col] = pd.to_datetime(df[col])
        return df

    # ------------------------------------------------------------- diario e scheda di ricerca
    def save_valuation(self, v: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO valuations (ticker, created_at, data_as_of, price, expected_value, per_scenario, "
                "probabilities, scenarios, simulation, story, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (v["ticker"], datetime.now().isoformat(timespec="seconds"), v.get("data_as_of"), v["price"],
                 v["expected_value"], json.dumps(v.get("per_scenario", {})), json.dumps(v.get("probabilities", {})),
                 json.dumps(v.get("scenarios", {})), json.dumps(v.get("simulation", {})), v.get("story", ""),
                 v.get("note", "")))
            return int(cur.lastrowid)

    def load_valuations(self, ticker: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM valuations"
        with self._conn() as conn:
            df = pd.read_sql_query(query + (" WHERE ticker = ?" if ticker else "") + " ORDER BY created_at", conn,
                                   params=[ticker] if ticker else [])
        for col in ("per_scenario", "probabilities", "scenarios", "simulation"):
            df[col] = df[col].map(lambda x: json.loads(x) if x else {})
        df["created_at"] = pd.to_datetime(df["created_at"])
        return df

    def delete_valuation(self, valuation_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM valuations WHERE id = ?", (int(valuation_id),))

    RESEARCH_FIELDS = ("one_liner", "story", "catalysts", "risks", "change_mind", "review_date")

    def save_research(self, ticker: str, fields: dict) -> None:
        values = [fields.get(k) for k in self.RESEARCH_FIELDS]
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO research_notes (ticker, one_liner, story, catalysts, risks, "
                         "change_mind, review_date, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (ticker, *values, datetime.now().isoformat(timespec="seconds")))

    def load_research(self, ticker: str | None = None) -> dict | pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM research_notes" + (" WHERE ticker = ?" if ticker else ""), conn,
                                   params=[ticker] if ticker else [])
        if ticker is None:
            return df
        return df.iloc[0].to_dict() if not df.empty else {}

    # ------------------------------------------------------------- aziende e dati dell'utente
    def add_custom_company(self, ticker: str, name: str, sector: str | None, currency: str | None) -> None:
        """Azienda fuori dall'indice: anagrafica in companies (in_index = 0) e segno in custom_companies."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("INSERT INTO companies (ticker, name, sector, in_index, updated_at) VALUES (?, ?, ?, 0, ?) "
                         "ON CONFLICT(ticker) DO UPDATE SET name = COALESCE(excluded.name, companies.name), "
                         "sector = COALESCE(excluded.sector, companies.sector), updated_at = excluded.updated_at",
                         (ticker, name, sector, now))
            conn.execute("INSERT OR REPLACE INTO custom_companies (ticker, currency, added_at) VALUES (?, ?, ?)",
                         (ticker, currency, now))

    def remove_custom_company(self, ticker: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM custom_companies WHERE ticker = ?", (ticker,))

    def load_custom_companies(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT c.ticker, c.currency, c.added_at, k.name, k.sector FROM custom_companies c "
                                     "LEFT JOIN companies k ON k.ticker = c.ticker ORDER BY c.ticker", conn)

    def data_version(self) -> str:
        """Impronta dei dati scaricati (ultimo prezzo, ultimo deposito e righe di ogni tabella): cambia con ogni
        aggiornamento, anche a metà. Chiave della copia veloce della webapp (MarketData.save_snapshot)."""
        parts = []
        with self._conn() as conn:
            for query in ("SELECT MAX(date) FROM price_history", "SELECT MAX(filed), COUNT(*) FROM financials",
                          "SELECT MAX(filed), COUNT(*) FROM insider_transactions",
                          # anche valori e ticker: fix_units e i ticker ritrovati cambiano righe esistenti
                          "SELECT MAX(filed), COUNT(*), TOTAL(value), COUNT(ticker) FROM thirteenf_holdings",
                          "SELECT MAX(filed), COUNT(*) FROM quarterly_financials", "SELECT COUNT(*) FROM splits"):
                try:
                    parts += [str(v) for v in conn.execute(query).fetchone()]
                except sqlite3.OperationalError:  # tabella non ancora creata
                    parts.append("-")
        return "-".join(parts)

    def user_data_version(self) -> str:
        """Impronta dei dati inseriti dall'utente (valori a mano e aziende aggiunte): chiave di cache della webapp."""
        with self._conn() as conn:
            n, total, last = conn.execute("SELECT COUNT(*), TOTAL(value), MAX(filed) FROM financials "
                                          "WHERE source LIKE 'manual%'").fetchone()
            custom = conn.execute("SELECT COUNT(*), GROUP_CONCAT(ticker) FROM custom_companies").fetchone()
        return f"{n}-{total}-{last}-{custom[0]}-{custom[1]}"

    def load_manual_financials(self, ticker: str | None = None) -> pd.DataFrame:
        query = ("SELECT ticker, period_end, metric, value, filed, source FROM financials "
                 "WHERE source LIKE 'manual%'")
        params: list = []
        if ticker is not None:
            query += " AND ticker = ?"
            params = [ticker]
        with self._conn() as conn:
            df = pd.read_sql_query(query + " ORDER BY period_end, metric", conn, params=params)
        for col in ("period_end", "filed"):
            df[col] = pd.to_datetime(df[col])
        return df

    def delete_manual_financials(self, rows: list[tuple]) -> None:
        """rows: (ticker, period_end, metric, filed) dei valori inseriti a mano da togliere."""
        def day(v):
            return pd.Timestamp(v).strftime("%Y-%m-%d")

        with self._conn() as conn:
            conn.executemany("DELETE FROM financials WHERE ticker = ? AND period_end = ? AND metric = ? AND filed = ? "
                             "AND source LIKE 'manual%'", [(t, day(p), m, day(f)) for t, p, m, f in rows])

    # ------------------------------------------------------------- il metodo dell'utente
    def save_custom_metric(self, d: dict) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO custom_metrics (key, label, formula, fmt, better, description, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (d["key"], d["label"], d["formula"], d.get("fmt", "num"), d.get("better"),
                          d.get("description", ""), datetime.now().isoformat(timespec="seconds")))

    def delete_custom_metric(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM custom_metrics WHERE key = ?", (key,))

    def load_custom_metrics(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, label, formula, fmt, better, description FROM custom_metrics "
                                "ORDER BY label").fetchall()
        return [dict(zip(("key", "label", "formula", "fmt", "better", "description"), r)) for r in rows]

    def load_checklist_items(self, defaults: list[tuple[str, str]] | None = None) -> pd.DataFrame:
        """Domande attive in ordine; la prima volta inserisce `defaults`."""
        with self._conn() as conn:
            if defaults and conn.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 0:
                conn.executemany("INSERT INTO checklist_items (question, help, weight, position, active) "
                                 "VALUES (?, ?, 1.0, ?, 1)", [(q, h, i) for i, (q, h) in enumerate(defaults)])
            return pd.read_sql_query("SELECT id, question, help, weight, position FROM checklist_items "
                                     "WHERE active = 1 ORDER BY position, id", conn)

    def save_checklist_item(self, question: str, help_text: str = "", weight: float = 1.0,
                            item_id: int | None = None) -> None:
        with self._conn() as conn:
            if item_id is None:
                pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM checklist_items").fetchone()[0]
                conn.execute("INSERT INTO checklist_items (question, help, weight, position, active) "
                             "VALUES (?, ?, ?, ?, 1)", (question, help_text, weight, pos))
            else:
                conn.execute("UPDATE checklist_items SET question = ?, help = ?, weight = ? WHERE id = ?",
                             (question, help_text, weight, item_id))

    def deactivate_checklist_item(self, item_id: int) -> None:
        """Le domande tolte restano nel database (con i voti già dati), ma non contano più."""
        with self._conn() as conn:
            conn.execute("UPDATE checklist_items SET active = 0 WHERE id = ?", (item_id,))

    def save_checklist_scores(self, ticker: str, scores: dict[int, float | None]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            for item_id, score in scores.items():
                if score is None:
                    conn.execute("DELETE FROM checklist_scores WHERE ticker = ? AND item_id = ?", (ticker, item_id))
                else:
                    conn.execute("INSERT OR REPLACE INTO checklist_scores (ticker, item_id, score, updated_at) "
                                 "VALUES (?, ?, ?, ?)", (ticker, int(item_id), float(score), now))

    def load_checklist_scores(self, ticker: str | None = None) -> pd.DataFrame:
        query = "SELECT ticker, item_id, score, updated_at FROM checklist_scores"
        with self._conn() as conn:
            if ticker is None:
                return pd.read_sql_query(query, conn)
            return pd.read_sql_query(query + " WHERE ticker = ?", conn, params=[ticker])

    def save_profile(self, profile: dict) -> None:
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO user_profile (key, value) VALUES (?, ?)",
                             [(k, json.dumps(v)) for k, v in profile.items()])

    def load_profile(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
        return {k: json.loads(v) for k, v in rows}

    # ------------------------------------------------------------- strategie dell'utente
    def save_strategy(self, definition: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("INSERT INTO user_strategies (name, definition, created_at, updated_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET definition = excluded.definition, updated_at = excluded.updated_at",
                         (definition["name"], json.dumps(definition), now, now))

    def load_strategies(self) -> dict[str, dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT name, definition FROM user_strategies ORDER BY name").fetchall()
        return {n: json.loads(d) for n, d in rows}

    def delete_strategy(self, name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM user_strategies WHERE name = ?", (name,))

    # ------------------------------------------------------------- ricerche salvate
    def save_screen(self, name: str, rules: list[dict], sectors: list[str] | None = None,
                    filters: dict | None = None) -> None:
        """`filters`: filtri sul giudizio di Esplora, {"verdicts": [categorie], "min_scores": {pilastro: minimo}}."""
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO saved_screens (name, rules, sectors, created_at, filters) "
                         "VALUES (?, ?, ?, ?, ?)",
                         (name, json.dumps(rules), json.dumps(sectors or []),
                          datetime.now().isoformat(timespec="seconds"), json.dumps(filters or {})))

    def load_screens(self) -> dict[str, dict]:
        """{nome: {rules, sectors, filters}}; le ricerche salvate prima dei filtri sul giudizio hanno filters {}."""
        with self._conn() as conn:
            rows = conn.execute("SELECT name, rules, sectors, filters FROM saved_screens ORDER BY name").fetchall()
        return {n: {"rules": json.loads(r), "sectors": json.loads(s or "[]"), "filters": json.loads(f or "{}")}
                for n, r, s, f in rows}

    def delete_screen(self, name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM saved_screens WHERE name = ?", (name,))

    # ------------------------------------------------------------- aziende seguite
    def add_to_watchlist(self, tickers: Iterable[str]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.executemany("INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
                             [(t, now) for t in tickers])

    def remove_from_watchlist(self, tickers: Iterable[str]) -> None:
        with self._conn() as conn:
            conn.executemany("DELETE FROM watchlist WHERE ticker = ?", [(t,) for t in tickers])

    def load_watchlist(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute("SELECT ticker FROM watchlist ORDER BY added_at, ticker")]

    # ------------------------------------------------------------- stime degli analisti (modules/estimates.py)
    def save_estimates(self, df: pd.DataFrame) -> int:
        """Salva le stime (stessa chiave = sostituita: un secondo scarico nello stesso giorno aggiorna)."""
        if df.empty:
            return 0
        rows = df.assign(as_of=pd.to_datetime(df["as_of"]).dt.strftime("%Y-%m-%d"))
        cols = ["ticker", "period", "metric", "avg", "low", "high", "analysts", "year_ago", "growth", "currency",
                "source", "as_of"]
        vals = [tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in r)
                for r in rows[cols].itertuples(index=False)]
        with self._conn() as conn:
            conn.executemany(f"INSERT OR REPLACE INTO estimates ({', '.join(cols)}) VALUES "
                             f"({', '.join('?' * len(cols))})", vals)
        return len(vals)

    def load_estimates(self, tickers: Iterable[str] | None = None) -> pd.DataFrame:
        query, params = "SELECT * FROM estimates", []
        if tickers is not None:
            query += " WHERE ticker IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(list(tickers)))
        with self._conn() as conn:
            return pd.read_sql(query + " ORDER BY as_of", conn, params=params, parse_dates=["as_of"])

    # ------------------------------------------------------------- pipeline (modules/workflow.py)
    def load_pipeline(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql("SELECT ticker, stage, note, created_at, updated_at FROM pipeline ORDER BY updated_at",
                               conn, parse_dates=["created_at", "updated_at"])

    def set_pipeline_stage(self, ticker: str, stage: str, note: str | None = None, at: str | None = None) -> None:
        """Mette `ticker` nella fase `stage` (o lo sposta) e registra lo spostamento nello storico."""
        at = at or datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("INSERT INTO pipeline (ticker, stage, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(ticker) DO UPDATE SET stage = excluded.stage, updated_at = excluded.updated_at, "
                         "note = COALESCE(excluded.note, pipeline.note)", (ticker, stage, note, at, at))
            conn.execute("INSERT INTO pipeline_log (ticker, stage, note, at) VALUES (?, ?, ?, ?)",
                         (ticker, stage, note, at))

    def remove_from_pipeline(self, ticker: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM pipeline WHERE ticker = ?", (ticker,))
            conn.execute("INSERT INTO pipeline_log (ticker, stage, note, at) VALUES (?, 'tolta', NULL, ?)",
                         (ticker, datetime.now().isoformat(timespec="seconds")))

    def load_pipeline_log(self, ticker: str | None = None) -> pd.DataFrame:
        query, params = "SELECT ticker, stage, note, at FROM pipeline_log", []
        if ticker is not None:
            query, params = query + " WHERE ticker = ?", [ticker]
        with self._conn() as conn:
            return pd.read_sql(query + " ORDER BY at", conn, params=params, parse_dates=["at"])

    # ------------------------------------------------------------- note dell'utente
    def save_note(self, ticker: str, **fields) -> None:
        """Aggiorna solo i campi passati (thesis, fair_value, assumptions) della nota di `ticker`."""
        allowed = {"thesis", "fair_value", "assumptions"}
        if not set(fields) <= allowed:
            raise ValueError(f"campi ammessi: {sorted(allowed)}")
        if "assumptions" in fields and fields["assumptions"] is not None:
            fields["assumptions"] = json.dumps(fields["assumptions"])
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO company_notes (ticker, updated_at) VALUES (?, ?)", (ticker, now))
            for k, v in fields.items():
                conn.execute(f"UPDATE company_notes SET {k} = ?, updated_at = ? WHERE ticker = ?", (v, now, ticker))

    def load_note(self, ticker: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT thesis, fair_value, assumptions, updated_at FROM company_notes "
                               "WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            return {}
        thesis, fair_value, assumptions, updated = row
        return {"thesis": thesis or "", "fair_value": fair_value,
                "assumptions": json.loads(assumptions) if assumptions else None, "updated_at": updated}

    def load_notes(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT ticker, thesis, fair_value, updated_at FROM company_notes", conn)

    # ------------------------------------------------------------- avvisi con storico (modules/alerts.py)
    def active_alerts(self) -> pd.DataFrame:
        """Avvisi la cui condizione era ancora vera all'ultima valutazione (cleared_at vuoto)."""
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM alerts WHERE cleared_at IS NULL ORDER BY id", conn)

    def add_alerts(self, alerts: list[dict], created_at: str) -> list[int]:
        ids = []
        with self._conn() as conn:
            for a in alerts:
                cur = conn.execute("INSERT INTO alerts (ticker, code, key, message, data, created_at) "
                                   "VALUES (?, ?, ?, ?, ?, ?)",
                                   (a["ticker"], a["code"], a["key"], a.get("message"),
                                    json.dumps(a.get("data") or {}, ensure_ascii=False, default=str), created_at))
                ids.append(int(cur.lastrowid))
        return ids

    def clear_alerts(self, ids: Iterable[int], cleared_at: str) -> None:
        """La condizione non è più vera: l'avviso resta nello storico e potrà ricomparire."""
        with self._conn() as conn:
            conn.executemany("UPDATE alerts SET cleared_at = ? WHERE id = ?", [(cleared_at, int(i)) for i in ids])

    def load_alerts(self, ticker: str | None = None, since: str | None = None, unseen: bool = False,
                    active: bool | None = None, limit: int | None = None) -> pd.DataFrame:
        """Storico degli avvisi dal più recente; `data` già decodificato."""
        where, params = [], []
        if ticker:
            where.append("ticker = ?")
            params.append(ticker)
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if unseen:
            where.append("seen_at IS NULL")
        if active is not None:
            where.append("cleared_at IS NULL" if active else "cleared_at IS NOT NULL")
        query = "SELECT * FROM alerts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        df["data"] = df["data"].map(lambda x: json.loads(x) if x else {})
        return df

    def mark_alerts_seen(self, ids: Iterable[int] | None = None, seen_at: str | None = None) -> None:
        """Segna come visti gli avvisi `ids` (tutti quelli non visti se None)."""
        seen_at = seen_at or datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            if ids is None:
                conn.execute("UPDATE alerts SET seen_at = ? WHERE seen_at IS NULL", (seen_at,))
            else:
                conn.executemany("UPDATE alerts SET seen_at = ? WHERE id = ? AND seen_at IS NULL",
                                 [(seen_at, int(i)) for i in ids])

    def set_price_target(self, ticker: str, buy_below: float | None = None, sell_above: float | None = None,
                         note: str | None = None) -> None:
        """Soglie di prezzo dell'utente (None = nessuna soglia da quel lato)."""
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO price_targets (ticker, buy_below, sell_above, note, updated_at) "
                         "VALUES (?, ?, ?, ?, ?)", (ticker, _sql_value(buy_below), _sql_value(sell_above), note,
                                                    datetime.now().isoformat(timespec="seconds")))

    def delete_price_target(self, ticker: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM price_targets WHERE ticker = ?", (ticker,))

    def load_price_targets(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM price_targets ORDER BY ticker", conn)

    # ------------------------------------------------------------- storico dei giudizi (modules/verdict_history.py)
    VERDICT_COLUMNS = ("as_of", "ticker", "run_id", "config_hash", "universe", "category", "profile", "quality",
                       "solidity", "price_pillar", "flags", "insiders", "coverage", "overall", "alarms", "checks",
                       "errors", "price", "user_fair_value", "rule")

    def save_verdict_history(self, rows: list[dict], run: dict) -> int:
        """Una riga per (data dei dati, ticker): un secondo aggiornamento con la stessa data la sostituisce."""
        cols = self.VERDICT_COLUMNS
        values = [tuple(_sql_value(r.get(c)) for c in cols) for r in rows]
        with self._conn() as conn:
            conn.executemany(f"INSERT OR REPLACE INTO verdict_history ({', '.join(cols)}) "
                             f"VALUES ({', '.join('?' * len(cols))})", values)
            conn.execute("INSERT OR REPLACE INTO verdict_runs (run_id, as_of, config_hash, user_data, written_at, "
                         "tickers, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         tuple(_sql_value(run.get(c)) for c in ("run_id", "as_of", "config_hash", "user_data",
                                                                  "written_at", "tickers", "seconds")))
        return len(values)

    def load_verdict_history(self, tickers: Iterable[str] | None = None, since: str | None = None,
                             until: str | None = None, run_id: str | None = None, as_of: str | None = None,
                             with_rule: bool = True) -> pd.DataFrame:
        """Storico dei giudizi in ordine (ticker, data). Date come testo AAAA-MM-GG nel database, Timestamp qui."""
        cols = [c for c in self.VERDICT_COLUMNS if with_rule or c != "rule"]
        where, params = [], []
        for col, op, val in (("as_of", ">=", since), ("as_of", "<=", until), ("run_id", "=", run_id),
                             ("as_of", "=", as_of)):
            if val is not None:
                where.append(f"{col} {op} ?")
                params.append(str(val))
        if tickers is not None:
            tickers = list(dict.fromkeys(tickers))
            if not tickers:
                return pd.DataFrame(columns=cols)
            where.append(f"ticker IN ({', '.join('?' * len(tickers))})")
            params += tickers
        query = f"SELECT {', '.join(cols)} FROM verdict_history" + \
            (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY ticker, as_of"
        with self._conn() as conn:
            try:
                df = pd.read_sql_query(query, conn, params=params)
            except Exception:  # noqa: BLE001 - database di sola lettura senza la tabella (mai aggiornato)
                return pd.DataFrame(columns=cols)
        df["as_of"] = pd.to_datetime(df["as_of"])
        return df

    def verdict_run(self, run_id: str | None) -> dict | None:
        """La scrittura dello storico fatta dall'esecuzione `run_id` (None se non c'è)."""
        if not run_id:
            return None
        with self._conn() as conn:
            try:
                row = conn.execute("SELECT run_id, as_of, config_hash, user_data, written_at, tickers, seconds "
                                   "FROM verdict_runs WHERE run_id = ?", (run_id,)).fetchone()
            except sqlite3.OperationalError:
                return None
        keys = ("run_id", "as_of", "config_hash", "user_data", "written_at", "tickers", "seconds")
        return dict(zip(keys, row)) if row else None

    def verdict_history_stats(self) -> dict:
        """Righe, date e ticker dello storico (per i rapporti)."""
        with self._conn() as conn:
            try:
                n, dates, tickers, first, last = conn.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT as_of), COUNT(DISTINCT ticker), MIN(as_of), MAX(as_of) "
                    "FROM verdict_history").fetchone()
            except sqlite3.OperationalError:
                return {"rows": 0, "dates": 0, "tickers": 0, "first": None, "last": None}
        return {"rows": n, "dates": dates, "tickers": tickers, "first": first, "last": last}

    def prune_verdict_rules(self, before: str) -> int:
        """Toglie il testo della regola (la parte più pesante) dalle righe più vecchie di `before`: categoria e
        punteggi restano, ed è ciò che serve per misurare il giudizio nel tempo."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE verdict_history SET rule = NULL WHERE as_of < ? AND rule IS NOT NULL",
                               (str(before),))
            return int(cur.rowcount or 0)

    def delete_experiment(self, exp_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM experiments WHERE id = ?", (int(exp_id),))

    def get_last_decisions(self, limit: int = 20) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", conn, params=[limit]
            )


def _sql_value(value):
    """Converte tipi numpy/pandas in tipi accettati da sqlite3."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        return value.item()
    return value


class FactsStore:
    """Archivio di ogni valore depositato nei 10-K e 10-Q (tutte le etichette), in un file SQLite separato.
    Etichette in `concepts` (taxonomy, concept, unit, label) e valori in `facts` con l'id dell'etichetta."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS concepts (
        id INTEGER PRIMARY KEY, taxonomy TEXT NOT NULL, concept TEXT NOT NULL, unit TEXT NOT NULL, label TEXT,
        UNIQUE (taxonomy, concept, unit)
    );
    CREATE TABLE IF NOT EXISTS facts (
        ticker TEXT NOT NULL, concept_id INTEGER NOT NULL, start TEXT, end TEXT NOT NULL, value REAL,
        accn TEXT, form TEXT, filed TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_facts_ticker ON facts(ticker, concept_id);
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                conn.execute("PRAGMA journal_mode = WAL")  # la webapp legge mentre l'aggiornamento scrive
            conn.executescript(self.SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def replace(self, ticker: str, facts: pd.DataFrame, labels: pd.DataFrame) -> int:
        """Sostituisce tutti i valori di `ticker` (companyfacts contiene sempre lo storico completo)."""
        def days(col):  # colonne intere, non valore per valore (decine di migliaia di righe per azienda)
            return pd.to_datetime(facts[col]).dt.strftime("%Y-%m-%d").astype(object).where(facts[col].notna(), None)

        with self._conn() as conn:
            conn.executemany("INSERT OR IGNORE INTO concepts (taxonomy, concept, unit, label) VALUES (?, ?, ?, ?)",
                             labels[["taxonomy", "concept", "unit", "label"]].itertuples(index=False, name=None))
            ids = pd.DataFrame(conn.execute("SELECT id, taxonomy, concept, unit FROM concepts").fetchall(),
                               columns=["id", "taxonomy", "concept", "unit"])
            cid = facts[["taxonomy", "concept", "unit"]].astype(object).merge(
                ids.astype(object), how="left", on=["taxonomy", "concept", "unit"])["id"]
            rows = list(zip([ticker] * len(facts), cid.astype(int).tolist(), days("start").tolist(),
                            days("end").tolist(), facts["value"].astype(float).tolist(),
                            facts["accn"].astype(object).tolist(), facts["form"].astype(object).tolist(),
                            days("filed").tolist()))
            conn.execute("DELETE FROM facts WHERE ticker = ?", (ticker,))
            conn.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def tickers(self) -> set[str]:
        with self._conn() as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM facts")}

    def filings(self, ticker: str) -> pd.DataFrame:
        """10-K e 10-Q di `ticker` presenti nell'archivio (numero, tipo, deposito)."""
        with self._conn() as conn:
            return pd.read_sql_query("SELECT accn, MIN(form) AS form, MIN(filed) AS filed FROM facts WHERE ticker = ? "
                                     "AND accn IS NOT NULL GROUP BY accn ORDER BY filed DESC", conn, params=[ticker])

    def concepts(self, ticker: str) -> pd.DataFrame:
        """Le etichette di `ticker`: quanti valori, primo e ultimo periodo, ultima data di deposito."""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT c.id AS concept_id, c.taxonomy, c.concept, c.unit, c.label, COUNT(*) AS n, "
                "MIN(f.end) AS first_end, MAX(f.end) AS last_end, MAX(f.filed) AS last_filed "
                "FROM facts f JOIN concepts c ON c.id = f.concept_id WHERE f.ticker = ? "
                "GROUP BY c.id ORDER BY last_end DESC, n DESC", conn, params=[ticker])

    def load(self, ticker: str, concept_ids: Iterable[int] | None = None) -> pd.DataFrame:
        """Valori di `ticker` nel formato di sec_quarterly.extract_all_facts."""
        query = ("SELECT c.taxonomy, c.concept, c.unit, f.start, f.end, f.value, f.accn, f.form, f.filed "
                 "FROM facts f JOIN concepts c ON c.id = f.concept_id WHERE f.ticker = ?")
        params: list = [ticker]
        if concept_ids is not None:
            query += " AND f.concept_id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps([int(i) for i in concept_ids]))
        with self._conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        for col in ("start", "end", "filed"):
            df[col] = pd.to_datetime(df[col])
        return df
