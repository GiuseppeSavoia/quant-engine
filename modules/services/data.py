"""Contesto comune dei servizi: caricamento dei dati (con la copia veloce), versioni, anagrafica, universi, valute,
catalogo delle metriche e prezzi. Nessuna dipendenza da Streamlit.

`AppData(cfg, db, reports_dir)` si usa da solo (es. un generatore di pagine statiche): i servizi si raggiungono con
`app.services.company`, `app.services.portfolio`... oppure direttamente per nome (`app.peers("AAPL")`).
`DashboardData` (modules/dashboard_data.py) ne è la sottoclasse con i metodi della webapp scritti per esteso."""
from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from modules.fundamental import FundamentalAnalyzer
from modules.market_data import MarketData, is_ttm
from modules.metrics import compute_metrics
from modules.multi_factor import MultiFactorModel
from modules import metrics as metrics_mod
from modules.universe import UniverseManager

logger = logging.getLogger("modules.dashboard_data")  # stesso nome di prima della divisione in servizi

# valuta dal suffisso di borsa di Yahoo (titoli fuori dall'anagrafica: solo nel portafoglio o nel diario)
SUFFIX_CURRENCY = {"MI": "EUR", "DE": "EUR", "F": "EUR", "PA": "EUR", "AS": "EUR", "MC": "EUR", "BR": "EUR",
                   "LS": "EUR", "VI": "EUR", "HE": "EUR", "IR": "EUR", "SW": "CHF", "L": "GBP", "CO": "DKK",
                   "ST": "SEK", "OL": "NOK", "TO": "CAD", "T": "JPY", "HK": "HKD"}

# servizi: nome dell'attributo in `Services` -> (modulo, classe). Importati solo quando servono.
SERVICES = {
    "company": ("modules.services.company", "CompanyService"),
    "valuation": ("modules.services.valuation", "ValuationService"),
    "portfolio": ("modules.services.portfolio", "PortfolioService"),
    "watch": ("modules.services.watch", "WatchService"),
    "method": ("modules.services.my_method", "MethodService"),
    "strategies": ("modules.services.strategies", "StrategyService"),
    "news": ("modules.services.news", "NewsService"),
    "reports": ("modules.services.reports", "ReportService"),
}


def _service_class(name: str):
    import importlib

    module, cls = SERVICES[name]
    return getattr(importlib.import_module(module), cls)


class Services:
    """I servizi di un contesto, creati alla prima richiesta (sono leggeri: solo un riferimento al contesto)."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, name: str):
        if name not in SERVICES:
            raise AttributeError(name)
        svc = _service_class(name)(self._ctx)
        setattr(self, name, svc)
        return svc


_ROUTES: dict[str, str] | None = None


def routes() -> dict[str, str]:
    """Metodo pubblico -> servizio che lo definisce (un nome appartiene a un solo servizio)."""
    global _ROUTES
    if _ROUTES is None:
        out: dict[str, str] = {}
        for svc in SERVICES:
            for attr in dir(_service_class(svc)):
                if not attr.startswith("_") and attr not in ("ctx", "cfg", "db", "data") \
                        and callable(getattr(_service_class(svc), attr)):
                    if attr in out:
                        raise RuntimeError(f"{attr} definito in {out[attr]} e in {svc}")
                    out[attr] = svc
        _ROUTES = out
    return _ROUTES


class AppData:
    """Dati dell'app aperti una volta: anagrafica, universi, MarketData (dalla copia Parquet se valida), modelli della
    strategia e le cache del catalogo delle metriche, dei beta di settore e del premio per il rischio."""

    def __init__(self, cfg: dict, db, reports_dir: Path):
        self.cfg = cfg
        self.db = db
        self.reports_dir = Path(reports_dir)
        comp = db.load_companies(only_index=False)
        self.companies = comp.set_index("ticker") if not comp.empty else pd.DataFrame()
        index_members = comp.loc[comp["in_index"] == 1, "ticker"].tolist() if not comp.empty else []
        self.index_tickers = sorted(index_members or cfg["universe"]["tickers"])
        self.custom_companies = db.load_custom_companies()
        self.foreign = db.load_foreign()  # aziende europee (FTSE MIB, Euro Stoxx 50)
        # nelle pagine delle aziende ci sono anche quelle aggiunte dall'utente e quelle europee; strategie e
        # laboratorio usano solo l'indice (index_tickers)
        self.tickers = sorted(set(self.index_tickers) | set(self.custom_companies["ticker"]) | set(self.foreign["ticker"]))
        # anche gli ex membri dell'indice: servono ai backtest del laboratorio, come in main.py
        self.universe = UniverseManager(db, cfg)
        since = pd.Timestamp.now().normalize() - pd.DateOffset(years=int(cfg["data"]["history_years"]))
        data_tickers = self.universe.data_tickers(since) if index_members else self.tickers
        self.data = self._load_data(db, cfg, data_tickers)
        self.fundamental = FundamentalAnalyzer(cfg["valuation"])
        self.multi_factor = MultiFactorModel(cfg["multi_factor"]["weights"])
        # ultima esecuzione di main.py (chiave delle cache: cambia a ogni aggiornamento, anche senza la strategia)
        self.run_id = db.last_run_id() or db.latest_run_id()
        # ultima analisi della strategia automatica (decisioni e ordini simulati): ora facoltativa
        self.decisions_run_id = db.latest_run_id()
        self.is_demo = self._demo_database(db)
        self._index_set = set(self.index_tickers)
        self._metrics: pd.DataFrame | None = None
        self._industry: pd.DataFrame | None = None
        self._erp: dict | None = None
        self._load_custom_metrics()

    # ------------------------------------------------------------------ servizi
    @cached_property
    def services(self) -> Services:
        """Funziona anche per le sottoclassi che non passano da __init__ (verdict_research.PointInTimeView)."""
        return Services(self)

    def __getattr__(self, name: str):
        """Un metodo pubblico che il contesto non ha (AppData usato da solo) arriva al servizio che lo definisce.
        Solo per i nomi dei servizi: gli altri attributi mancanti restano un AttributeError come prima."""
        if name.startswith("_") or name not in routes():
            raise AttributeError(f"{type(self).__name__!s} non ha l'attributo {name!r}")
        return getattr(getattr(self.services, routes()[name]), name)

    # ------------------------------------------------------------------ apertura
    @staticmethod
    def _demo_database(db) -> bool:
        """True se il database è quello dei dati simulati (ultima esecuzione di main.py con --demo), anche se aperto
        da una copia con --config: i prezzi in tempo reale di Yahoo non vanno mescolati ai prezzi simulati."""
        try:
            with db._conn() as conn:
                row = conn.execute("SELECT argv FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
        except Exception:  # noqa: BLE001 - tabella runs assente: database vero o vuoto
            return False
        return bool(row and row[0] and "--demo" in str(row[0]))

    def _load_custom_metrics(self) -> None:
        valid = []
        for d in self.db.load_custom_metrics():
            try:
                metrics_mod.validate_custom(d)
                valid.append(d)
            except ValueError as exc:  # una formula diventata invalida non deve bloccare l'app
                logger.warning("Metrica personale %s ignorata: %s", d.get("key"), exc)
        metrics_mod.set_custom(valid)

    def universe_fn(self):
        """Universo point-in-time per i backtest del laboratorio, come in main.py."""
        pit = self.universe.source == "sp500" and self.cfg["universe"].get("point_in_time_membership", True)
        return (lambda day: self.universe.tickers(as_of=day)) if pit else None

    @staticmethod
    def _load_data(db, cfg: dict, tickers: list[str]) -> MarketData:
        """Dati dalla copia Parquet se è ancora valida (stessa ultima esecuzione, stessi dati inseriti a mano, stessi
        ticker), altrimenti dal database, salvando la copia per la prossima apertura."""
        import hashlib

        key = "|".join([str(db.latest_run_id()), db.data_version(), db.user_data_version(),
                        hashlib.md5(",".join(tickers).encode()).hexdigest()])
        folder = Path(db.db_path).parent / "cache" / Path(db.db_path).stem
        data = MarketData.load_snapshot(folder, key)
        if data is None:
            data = MarketData.from_db(db, cfg, tickers)
            try:
                data.save_snapshot(folder, key)
            except Exception as exc:  # noqa: BLE001 - senza copia la webapp funziona lo stesso, solo più lenta
                logger.warning("Copia veloce dei dati non salvata: %s", exc)
        return data

    # ------------------------------------------------------------------ anagrafica e universi
    def sectors(self) -> dict[str, str]:
        return self.universe.sectors()

    def info(self, ticker: str) -> dict:
        row = self.companies.loc[ticker].to_dict() if ticker in self.companies.index else {}
        return {"ticker": ticker, **row}

    def currency(self, ticker: str) -> str:
        index_set = getattr(self, "_index_set", None)  # le viste point-in-time (verdict_research) non passano da __init__
        if index_set is None:
            index_set = set(getattr(self, "index_tickers", []) or [])
        if ticker in index_set:  # S&P 500: dollari, senza leggere altro (chiamata per ogni riga delle tabelle)
            return "USD"
        cc = self.custom_companies.set_index("ticker")["currency"] if not self.custom_companies.empty else {}
        fc = self.foreign.set_index("ticker")["currency"] if not self.foreign.empty else {}
        known = (cc.get(ticker) if hasattr(cc, "get") else None) or (fc.get(ticker) if hasattr(fc, "get") else None)
        if known:
            return known
        try:  # titolo solo nel portafoglio (es. ENI.MI, VWCE.DE): la valuta delle sue operazioni
            tx = getattr(self, "db", None).load_transactions()
            hit = tx.loc[tx["ticker"] == ticker, "currency"].dropna() if not tx.empty else pd.Series(dtype=str)
            if not hit.empty:
                return str(hit.iloc[-1])
        except Exception:  # noqa: BLE001 - senza tabella del portafoglio: si decide dal suffisso
            pass
        suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
        return SUFFIX_CURRENCY.get(suffix, "USD") if suffix else "USD"

    def universe_of(self, ticker: str) -> str:
        """A quale gruppo appartiene: "S&P 500", l'indice europeo (es. "FTSE MIB, Euro Stoxx 50") o "Aggiunta da te"."""
        if ticker in set(self.index_tickers):
            return "S&P 500"
        if not self.foreign.empty and ticker in set(self.foreign["ticker"]):
            return str(self.foreign.set_index("ticker").at[ticker, "index_name"])
        return "Aggiunta da te"

    def universe_tickers(self, name: str) -> list[str]:
        """Ticker di un gruppo: "S&P 500", "FTSE MIB", "Euro Stoxx 50", "Le mie aggiunte" o "Tutte"."""
        if name == "S&P 500":
            return list(self.index_tickers)
        if name == "Le mie aggiunte":
            return list(self.custom_companies["ticker"])
        if name == "Tutte" or self.foreign.empty:
            return list(self.tickers) if name == "Tutte" else []
        return list(self.foreign.loc[self.foreign["index_name"].str.contains(name, regex=False), "ticker"])

    # ------------------------------------------------------------------ metriche
    def metrics(self) -> pd.DataFrame:
        """Catalogo delle metriche per tutti i membri dell'indice (calcolato una volta), metriche personali e voto
        della checklist compresi."""
        if self._metrics is None:
            self._metrics = compute_metrics(self.data, self.tickers,
                                            insider_days=int(self.cfg.get("insiders", {}).get("window_days", 90)))
        self._metrics[metrics_mod.JUDGMENT.key] = self.checklist_scores().reindex(self._metrics.index)
        return self._metrics

    # ------------------------------------------------------------------ prezzi e bilanci grezzi
    def prices(self, ticker: str) -> pd.DataFrame:
        """OHLCV (apertura compresa, per le candele; NaN dove manca) con medie mobili a 50/200 giorni e drawdown dal
        massimo."""
        ohlc = self.data.ohlc(ticker)
        if ohlc.empty:
            return ohlc
        close = ohlc["close"]
        return ohlc.assign(sma50=close.rolling(50).mean(), sma200=close.rolling(200).mean(),
                           drawdown=close / close.cummax() - 1)

    def benchmark(self) -> pd.Series:
        b = self.data.benchmark
        return self.data.prices(b) if b else pd.Series(dtype=float)

    def financial_versions(self, ticker: str) -> pd.DataFrame:
        """Tutte le versioni depositate (anche le correzioni successive): utile per il point-in-time."""
        fin = self.data.financials
        if fin.empty:
            return fin
        sub = fin[fin["ticker"] == ticker]
        return sub.sort_values(["period_end", "metric", "filed"]).reset_index(drop=True)

    def filing_dates(self, ticker: str) -> pd.Series:
        """Date di deposito dei bilanci annuali (10-K) del titolo."""
        fin = self.data.financials
        if fin.empty:
            return pd.Series(dtype="datetime64[ns]")
        return pd.Series(sorted(fin.loc[(fin["ticker"] == ticker) & ~is_ttm(fin), "filed"].unique()))

    def latest_filings(self) -> pd.DataFrame:
        """Ultimo documento depositato per titolo: data e tipo (10-K = esercizio, 10-Q = trimestre)."""
        fin = self.data.financials
        if fin.empty:
            return pd.DataFrame(columns=["filed", "form", "period_end"])
        last = fin.loc[fin.groupby("ticker")["filed"].idxmax()].set_index("ticker")
        form = np.where(is_ttm(last), "10-Q", "10-K")
        return pd.DataFrame({"filed": last["filed"], "form": form, "period_end": last["period_end"]})

    def _single(self, ticker: str) -> MarketData:
        """MarketData ridotto al titolo + benchmark + tasso: lo slicing point-in-time diventa rapido."""
        d = self.data
        cols = [c for c in dict.fromkeys([ticker, d.benchmark, d.risk_free_ticker]) if c and c in d.close]
        fin = d.financials[d.financials["ticker"] == ticker] if not d.financials.empty else d.financials
        return MarketData(close=d.close[cols], high=d.high.reindex(columns=cols), low=d.low.reindex(columns=cols),
                          volume=d.volume.reindex(columns=cols), financials=fin, benchmark=d.benchmark,
                          risk_free_ticker=d.risk_free_ticker, open=d.open.reindex(columns=cols))
