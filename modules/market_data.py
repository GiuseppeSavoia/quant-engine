"""
Istantanea dei dati di mercato in memoria, con slicing "point-in-time".

`MarketData.as_of(data)` restituisce solo ciò che era noto a quella data:
prezzi fino a quel giorno e bilanci con data di deposito (`filed`) non successiva;
se di uno stesso periodo esistono più versioni (correzioni), vale l'ultima già pubblicata.
La stessa funzione serve all'analisi live e al backtest, così il backtest non
può "sbirciare" nel futuro (look-ahead bias).

Valori inseriti a mano (fonte "manual" per gli esercizi, "manual_ttm" per gli ultimi 12 mesi): per lo stesso
periodo e voce prevalgono sui dati SEC, dalla data da cui l'utente li dichiara validi (`filed`).

Bilanci: `statements` = esercizi completi (10-K); `ttm_statements` = ultimi 12 mesi a ogni trimestre
(10-Q); `fundamentals` = le due insieme, la vista più aggiornata a una data.

Split azionari: i prezzi di Yahoo sono rettificati per tutti gli split fino a oggi, mentre il
numero di azioni di un bilancio è quello valido alla sua data di deposito. Per confrontarli
(market cap, valore per azione) le azioni vengono moltiplicate per gli split avvenuti DOPO il
deposito. Non è look-ahead: è solo un cambio di unità di misura, la stessa già applicata ai prezzi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


def is_ttm(fin: pd.DataFrame) -> pd.Series:
    """Righe "ultimi 12 mesi" calcolate dai trimestrali (fonte con suffisso _ttm, es. sec_ttm)."""
    if "source" not in fin.columns:
        return pd.Series(False, index=fin.index)
    return fin["source"].astype(str).str.endswith("_ttm")


# fonti i cui numeri di azioni NON vanno rettificati: Yahoo li riporta già sulla base attuale
# (da verificare sui dati reali); SEC e CSV sono come depositati
SHARES_ALREADY_ADJUSTED_SOURCES = {"yahoo", "yahoo_fill", "yahoo_fill_ttm"}
# oltre questo distacco dall'ultimo bilancio il numero di azioni non si usa (vedi MarketData.last_shares)
SHARES_MAX_AGE_DAYS = 400


def adjust_shares_for_splits(fin: pd.DataFrame, splits: pd.DataFrame | None) -> pd.DataFrame:
    """Riporta la metrica `shares` alla base dei prezzi rettificati: per ogni riga moltiplica
    per il prodotto dei rapporti degli split con data successiva a `filed`."""
    if fin.empty or splits is None or splits.empty:
        return fin
    mask = fin["metric"] == "shares"
    if "source" in fin.columns:
        mask &= ~fin["source"].isin(SHARES_ALREADY_ADJUSTED_SOURCES)
    if not mask.any():
        return fin
    fin = fin.copy()
    # solo le righe delle azioni, raggruppate per ticker una volta (prima: una maschera su tutta la tabella per ogni
    # ticker con split, 2 secondi all'apertura della webapp)
    by_ticker = fin.index[mask].groupby(fin.loc[mask, "ticker"])
    for ticker, sp in splits.sort_values("date").groupby("ticker"):
        rows = by_ticker.get(ticker)
        if rows is None or not len(rows):
            continue
        # suffix[i] = prodotto dei rapporti dal split i-esimo in poi; suffix[n] = 1
        ratios = sp["ratio"].to_numpy(dtype=float)
        suffix = np.append(np.cumprod(ratios[::-1])[::-1], 1.0)
        dates = pd.to_datetime(sp["date"]).to_numpy()
        first_after = np.searchsorted(dates, fin.loc[rows, "filed"].to_numpy(), side="right")
        fin.loc[rows, "value"] = fin.loc[rows, "value"].to_numpy() * suffix[first_after]
    return fin


def _wide(prices_long: pd.DataFrame, column: str) -> pd.DataFrame:
    if prices_long.empty:
        return pd.DataFrame()
    wide = prices_long.pivot_table(index="date", columns="ticker", values=column, aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide.sort_index()


@dataclass
class MarketData:
    close: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    financials: pd.DataFrame  # formato lungo: ticker, period_end, metric, value, filed
    benchmark: str | None = None
    risk_free_ticker: str | None = None
    has_manual: bool | None = None  # ci sono valori inseriti a mano (fonte manual*)? calcolato una volta
    insiders: pd.DataFrame = field(default_factory=pd.DataFrame)   # acquisti e vendite degli insider (Form 4)
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)   # posizioni dei grandi investitori (13F)
    quarterly: pd.DataFrame = field(default_factory=pd.DataFrame)  # utile netto per singolo trimestre (sec_q)
    # apertura (rettificata come gli altri prezzi): serve solo alle candele; in fondo e facoltativa, così chi
    # costruisce MarketData con close/high/low/volume posizionali continua a funzionare
    open: pd.DataFrame = field(default_factory=pd.DataFrame)
    _statements_cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ costruttori
    @classmethod
    def from_long(cls, prices_long: pd.DataFrame, financials_long: pd.DataFrame,
                  benchmark: str | None = None, risk_free_ticker: str | None = None,
                  splits: pd.DataFrame | None = None) -> "MarketData":
        prices_long = prices_long.copy()
        prices_long["date"] = pd.to_datetime(prices_long["date"])
        fin = financials_long.copy()
        if not fin.empty:
            fin["period_end"] = pd.to_datetime(fin["period_end"])
            if "filed" not in fin.columns:
                fin["filed"] = fin["period_end"] + pd.Timedelta(days=90)
            fin["filed"] = pd.to_datetime(fin["filed"])
            fin = adjust_shares_for_splits(fin, splits)
            fin = fin.sort_values("filed", kind="stable").reset_index(drop=True)
        return cls(
            close=_wide(prices_long, "close"),
            high=_wide(prices_long, "high"),
            low=_wide(prices_long, "low"),
            volume=_wide(prices_long, "volume"),
            open=_wide(prices_long, "open") if "open" in prices_long.columns else pd.DataFrame(),
            financials=fin,
            benchmark=benchmark,
            risk_free_ticker=risk_free_ticker,
        )

    @classmethod
    def from_db(cls, db, cfg: dict, tickers: list[str]) -> "MarketData":
        u = cfg["universe"]
        extra = [t for t in (u.get("benchmark"), u.get("risk_free_ticker")) if t]
        all_t = list(dict.fromkeys(list(tickers) + extra))
        data = cls.from_long(db.load_prices(all_t), db.load_financials(tickers),
                             benchmark=u.get("benchmark"), risk_free_ticker=u.get("risk_free_ticker"),
                             splits=db.load_splits(tickers))
        if hasattr(db, "load_insiders"):  # solo se scaricati: altrimenti le metriche restano vuote, non a zero
            insiders = db.load_insiders(tickers, codes=["P", "S"])  # le uniche operazioni che dicono qualcosa
            holdings = db.load_thirteenf()
            data.insiders = insiders if not insiders.empty else pd.DataFrame()
            data.holdings = holdings if not holdings.empty else pd.DataFrame()
        if hasattr(db, "load_quarterly"):  # solo l'utile: serve alla crescita trimestrale (O'Neil, Zweig...)
            q = db.load_quarterly(tickers, metrics=["net_income"])
            data.quarterly = q.sort_values("filed", kind="stable").reset_index(drop=True) if not q.empty \
                else pd.DataFrame()
        return data

    # ------------------------------------------------------------ copia veloce (Parquet)
    # istantanea salvata prima di un frame nuovo (es. "open"): manca il file, load_snapshot dà None e si rigenera
    _FRAMES = ("close", "high", "low", "volume", "open", "financials", "insiders", "holdings", "quarterly")

    def save_snapshot(self, folder: Path, key: str) -> None:
        """Salva tutto in Parquet in `folder` con la chiave `key` (es. ultima esecuzione + dati dell'utente): la
        webapp la rilegge in una frazione di secondo invece di ricostruirla dal database (~10 s)."""
        import json

        folder.mkdir(parents=True, exist_ok=True)
        for name in self._FRAMES:
            frame = getattr(self, name)
            frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
            frame.to_parquet(folder / f"{name}.parquet")
        (folder / "meta.json").write_text(json.dumps({"key": key, "benchmark": self.benchmark,
                                                      "risk_free_ticker": self.risk_free_ticker}), encoding="utf-8")

    @classmethod
    def load_snapshot(cls, folder: Path, key: str) -> "MarketData | None":
        """La copia salvata con la stessa chiave, o None (assente, vecchia o illeggibile: si ricostruisce)."""
        import json

        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            if meta.get("key") != key:
                return None
            frames = {name: pd.read_parquet(folder / f"{name}.parquet") for name in cls._FRAMES}
        except Exception:  # noqa: BLE001
            return None
        return cls(**frames, benchmark=meta.get("benchmark"), risk_free_ticker=meta.get("risk_free_ticker"))

    # ------------------------------------------------------------ point in time
    def as_of(self, date) -> "MarketData":
        date = pd.Timestamp(date)
        fin = self.financials
        if not fin.empty:
            fin = fin[fin["filed"] <= date]  # solo ciò che era già pubblico
        return MarketData(
            close=self.close.loc[:date], high=self.high.loc[:date], low=self.low.loc[:date],
            volume=self.volume.loc[:date], financials=fin,
            benchmark=self.benchmark, risk_free_ticker=self.risk_free_ticker, has_manual=self.manual_data(),
            insiders=self.insiders[self.insiders["filed"] <= date] if not self.insiders.empty else self.insiders,
            holdings=self.holdings[self.holdings["filed"] <= date] if not self.holdings.empty else self.holdings,
            quarterly=self.quarterly[self.quarterly["filed"] <= date] if not self.quarterly.empty else self.quarterly,
            open=self.open.loc[:date] if not self.open.empty else self.open,
        )

    # ------------------------------------------------------------ accessori
    @staticmethod
    def is_fx(symbol) -> bool:
        """Cambio di Yahoo (EURUSD=X): serve al portafoglio, ma non è un titolo né un giorno di borsa."""
        return str(symbol).endswith("=X")

    @property
    def last_date(self) -> pd.Timestamp | None:
        """Ultimo giorno con un prezzo di un titolo (benchmark compreso). I cambi non contano: quotano anche
        quando le borse sono chiuse e possono essere stati scaricati dopo (portafoglio), e un cambio più avanti
        di oltre 5 sedute lasciava senza prezzo tutti i titoli nelle metriche. Un titolo europeo con una seduta
        in più del benchmark invece conta (non perde il suo ultimo prezzo)."""
        key = "__last_date__"
        if key not in self._statements_cache:
            cols = [c for c in self.close.columns if not self.is_fx(c)]
            close = self.close[cols].dropna(how="all") if cols else pd.DataFrame()
            self._statements_cache[key] = close.index.max() if not close.empty else None
        return self._statements_cache[key]

    def recent_close(self, cols: list[str], rows: int | None = None) -> pd.DataFrame:
        """Chiusure di `cols` fino a `last_date` (ultime `rows` righe): righe in coda con soli cambi escluse."""
        last = self.last_date
        c = self.close[cols]
        if last is not None:
            c = c.loc[:last]
        return c.iloc[-rows:] if rows else c

    @property
    def calendar(self) -> pd.DatetimeIndex:
        """Giorni di borsa veri: quelli del benchmark se disponibile."""
        if self.benchmark and self.benchmark in self.close:
            return self.close[self.benchmark].dropna().index
        return self.close.dropna(how="all").index

    def prices(self, ticker: str) -> pd.Series:
        key = ("prices", ticker)
        if key not in self._statements_cache:
            self._statements_cache[key] = (self.close[ticker].dropna() if ticker in self.close
                                           else pd.Series(dtype=float))
        return self._statements_cache[key]

    def returns(self, ticker: str) -> pd.Series:
        """Rendimenti logaritmici giornalieri (calcolati una volta per istantanea)."""
        key = ("returns", ticker)
        if key not in self._statements_cache:
            px = self.prices(ticker)
            px = px[px > 0]
            self._statements_cache[key] = pd.Series(np.diff(np.log(px.to_numpy())), index=px.index[1:]) \
                if len(px) > 1 else pd.Series(dtype=float)
        return self._statements_cache[key]

    def ohlc(self, ticker: str) -> pd.DataFrame:
        cols = {}
        for name, frame in (("open", self.open), ("high", self.high), ("low", self.low), ("close", self.close),
                            ("volume", self.volume)):
            if ticker in frame:
                cols[name] = frame[ticker]
        return pd.DataFrame(cols).dropna(subset=["close"]) if "close" in cols else pd.DataFrame()

    def risk_free(self, fallback: float) -> float:
        """Ultimo rendimento del Treasury 10Y (^TNX è quotato in percentuale)."""
        if self.risk_free_ticker and self.risk_free_ticker in self.close:
            series = self.close[self.risk_free_ticker].dropna()
            if not series.empty and np.isfinite(series.iloc[-1]) and series.iloc[-1] > 0:
                return float(series.iloc[-1]) / 100.0
        return fallback

    def manual_data(self) -> bool:
        if self.has_manual is None:
            fin = self.financials
            self.has_manual = bool(not fin.empty and "source" in fin
                                   and fin["source"].astype(str).str.startswith("manual").any())
        return self.has_manual

    def _statements_of(self, ticker: str, ttm: bool) -> pd.DataFrame:
        cache = self._statements_cache
        wide_key = ("__wide__", ttm)
        if wide_key not in cache:
            fin = self.financials
            if not fin.empty:
                fin = fin[is_ttm(fin) == ttm]
            if not fin.empty and "source" in fin.columns:
                # per lo stesso periodo e voce: un valore a mano vince su SEC/ESEF, che vincono sempre sulla riserva
                # di Yahoo anche se depositati prima (se già validi a quella data). Ordinamento stabile: a parità
                # di priorità resta l'ordine per data di deposito
                src = fin["source"].astype(str)
                rank = np.where(src.str.startswith("manual"), 2, np.where(src.str.startswith("yahoo_fill"), 0, 1))
                if (rank != 1).any():
                    fin = fin.assign(_rank=rank).sort_values("_rank", kind="stable").drop(columns="_rank")
            if fin.empty:
                cache[wide_key] = (None, set())
            else:
                latest = fin.drop_duplicates(["ticker", "period_end", "metric"], keep="last")
                wide = latest.set_index(["ticker", "period_end", "metric"])["value"].unstack("metric").sort_index()
                wide.columns.name = None
                cache[wide_key] = (wide, set(wide.index.get_level_values(0)))
        key = ("statements", ticker, ttm)
        if key not in cache:
            wide, tickers = cache[wide_key]
            cache[key] = (wide.xs(ticker, level=0).dropna(axis=1, how="all") if ticker in tickers
                          else pd.DataFrame())
        return cache[key]

    def statement_sources(self, ticker: str, ttm: bool = False, column: str = "source") -> pd.DataFrame:
        """Da quale fonte viene ogni valore di `statements` (ttm=False) o `ttm_statements` (ttm=True): stessa forma,
        celle = `source` (o un'altra colonna, es. "filed") della riga che vince (stesse priorità di `_statements_of`)."""
        fin = self.financials
        if fin.empty or "source" not in fin.columns:
            return pd.DataFrame()
        cache = self._statements_cache
        if "__rows_by_ticker__" not in cache:  # posizioni delle righe di ogni ticker: si filtra una volta sola
            cache["__rows_by_ticker__"] = fin.groupby("ticker", sort=False).indices
        rows = cache["__rows_by_ticker__"].get(ticker)
        if rows is None:
            return pd.DataFrame()
        fin = fin.iloc[rows]
        fin = fin[is_ttm(fin) == ttm]
        if fin.empty:
            return pd.DataFrame()
        src = fin["source"].astype(str)
        rank = np.where(src.str.startswith("manual"), 2, np.where(src.str.startswith("yahoo_fill"), 0, 1))
        fin = fin.assign(_rank=rank).sort_values("_rank", kind="stable")
        latest = fin.drop_duplicates(["period_end", "metric"], keep="last")
        out = latest.set_index(["period_end", "metric"])[column].unstack("metric").sort_index()
        out.columns.name = None
        return out

    def quarterly_income(self, ticker: str) -> pd.Series:
        """Utile netto per singolo trimestre (ultima versione già depositata), per data di chiusura."""
        cache = self._statements_cache
        if "__quarterly__" not in cache:
            q = self.quarterly
            cache["__quarterly__"] = {} if q.empty else {
                t: g.set_index("period_end")["value"].sort_index()
                for t, g in q.drop_duplicates(["ticker", "period_end"], keep="last").groupby("ticker")}
        return cache["__quarterly__"].get(ticker, pd.Series(dtype=float))

    def statements(self, ticker: str) -> pd.DataFrame:
        """Bilanci ANNUALI del ticker: righe = chiusure d'esercizio (crescenti), colonne = metriche.
        Per ogni periodo vale l'ultima versione depositata (i dati sono ordinati per `filed`)."""
        return self._statements_of(ticker, ttm=False)

    def ttm_statements(self, ticker: str) -> pd.DataFrame:
        """Valori degli ultimi 12 mesi alla chiusura dei trimestri (dai 10-Q)."""
        return self._statements_of(ticker, ttm=True)

    def fundamentals(self, ticker: str) -> pd.DataFrame:
        """Esercizi completi e trimestri insieme: ogni riga è "ultimi 12 mesi" a quella data (un esercizio
        completo lo è per definizione). È la vista più aggiornata; per la storia annuale usare `statements`."""
        key = ("fundamentals", ticker)
        cache = self._statements_cache
        if key not in cache:
            annual, ttm = self.statements(ticker), self.ttm_statements(ticker)
            if ttm.empty:
                cache[key] = annual
            else:
                both = pd.concat([annual, ttm]) if not annual.empty else ttm
                cache[key] = both[~both.index.duplicated(keep="first")].sort_index()
        return cache[key]

    @staticmethod
    def last_shares(st: pd.DataFrame, max_age_days: int = SHARES_MAX_AGE_DAYS) -> float:
        """Ultimo numero di azioni noto, solo se non è molto più vecchio dell'ultimo bilancio di `st`: alcune
        aziende con più classi di azioni (es. Berkshire, Visa) non lo dichiarano in modo leggibile per anni, e
        quello di 10 anni prima darebbe capitalizzazioni e multipli assurdi. Meglio vuoto."""
        if st.empty or "shares" not in st:
            return np.nan
        sh = st["shares"].dropna()
        if sh.empty or (st.index[-1] - sh.index[-1]).days > max_age_days:
            return np.nan
        return float(sh.iloc[-1])

    @staticmethod
    def year_ago(st: pd.DataFrame, tolerance_days: int = 45) -> pd.Series:
        """Riga di un anno prima dell'ultima (stesso trimestre dell'anno precedente), vuota se manca."""
        if len(st) < 2:
            return pd.Series(dtype=float)
        target = st.index[-1] - pd.DateOffset(years=1)
        gap = np.abs((st.index[:-1] - target).days)
        i = int(np.argmin(gap))
        return st.iloc[i] if gap[i] <= tolerance_days else pd.Series(dtype=float)
