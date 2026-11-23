"""
Ingestione dati nel Golden Data Lake.

- Prezzi (Yahoo): incrementali dall'ultimo giorno di CIASCUN ticker, a blocchi di `price_chunk_size` ticker con
  una pausa tra un blocco e l'altro; i ticker che non arrivano vengono ritentati con attese crescenti, poi a
  gruppi sempre più piccoli fino al singolo ticker. Storico completo (`history_years`) la prima volta, e di nuovo
  se `history_years` aumenta; un ticker che Yahoo non ha (es. ex membro dell'indice delistato) non viene
  richiesto a ogni esecuzione. A fine aggiornamento un WARNING elenca i titoli dell'indice rimasti indietro
  rispetto all'ultima seduta (prima un blocco di 89 titoli è rimasto fermo due settimane senza che nulla lo dicesse).
- Split: Yahoo rettifica TUTTO lo storico a ogni split, mentre il database scarica solo i giorni
  nuovi. Se nei giorni nuovi compare uno split, lo storico di quel ticker viene riscaricato per
  intero (altrimenti resterebbe un falso crollo del -90% il giorno di uno split 10:1). Gli split
  finiscono nella tabella `splits`: servono a riportare le azioni dei bilanci alla base dei prezzi.
- Bilanci (SEC o Yahoo): riscaricati solo quando è plausibile che ci sia un nuovo documento
  (trimestrale o annuale) o quando il dato ha più di `fundamentals_max_age_days` giorni.

Le sorgenti sono oggetti separati, sostituibili nei test o con altri fornitori.
"""
from __future__ import annotations

import logging
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PRICE_FIELDS = ["Open", "High", "Low", "Close", "Volume"]
SPLIT_FIELD = "Stock Splits"  # colonna di yf.download(actions=True): 0 = nessuno split, 10 = split 10:1

# metrica normalizzata -> possibili nomi delle righe nei bilanci di yfinance (in ordine di preferenza)
METRIC_MAP: dict[str, list[str]] = {
    "free_cash_flow": ["Free Cash Flow"],
    "operating_cash_flow": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "revenue": ["Total Revenue", "Operating Revenue"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "pretax_income": ["Pretax Income"],
    "tax_provision": ["Tax Provision"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
}

_EMPTY_PRICES = pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume", "split"])
_EMPTY_SPLITS = pd.DataFrame(columns=["ticker", "date", "ratio"])
_EMPTY_FIN = pd.DataFrame(columns=["ticker", "period_end", "metric", "value"])


# ---------------------------------------------------------------- normalizzazione
def _single_ticker_frame(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    cols = {c: str(c).lower() for c in frame.columns if str(c) in PRICE_FIELDS}
    cols.update({c: "split" for c in frame.columns if str(c) == SPLIT_FIELD})
    out = frame[list(cols)].rename(columns=cols).copy()
    out = out.dropna(subset=["close"]) if "close" in out.columns else out.iloc[0:0]
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out.reset_index(names="date")
    out.insert(0, "ticker", ticker)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in out.columns:
            out[col] = float("nan")
    out["split"] = out["split"].fillna(0.0).astype(float) if "split" in out.columns else 0.0
    return out[["ticker", "date", "open", "high", "low", "close", "volume", "split"]]


def normalize_download(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Converte l'output di yf.download (colonne semplici o MultiIndex, in qualunque
    ordine dei livelli) in formato lungo."""
    if raw is None or raw.empty:
        return _EMPTY_PRICES.copy()
    frames = []
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(map(str, raw.columns.get_level_values(0)))
        field_level = 0 if lvl0 & set(PRICE_FIELDS) else 1
        ticker_level = 1 - field_level
        for ticker in pd.unique(raw.columns.get_level_values(ticker_level)):
            sub = raw.xs(ticker, axis=1, level=ticker_level)
            frames.append(_single_ticker_frame(sub, str(ticker)))
    else:
        frames.append(_single_ticker_frame(raw, tickers[0]))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else _EMPTY_PRICES.copy()


def extract_splits(prices_long: pd.DataFrame) -> pd.DataFrame:
    """Split contenuti in un download di prezzi (colonna `split`): ticker, date, ratio."""
    if prices_long is None or prices_long.empty or "split" not in prices_long:
        return _EMPTY_SPLITS.copy()
    ratio = pd.to_numeric(prices_long["split"], errors="coerce")
    rows = prices_long[(ratio > 0) & (ratio != 1)]
    return pd.DataFrame({"ticker": rows["ticker"], "date": pd.to_datetime(rows["date"]),
                         "ratio": ratio[rows.index]}).reset_index(drop=True)


def normalize_statements(ticker: str, statements: list[pd.DataFrame]) -> pd.DataFrame:
    """Unisce i bilanci di yfinance (righe = voci, colonne = date di chiusura)
    nelle metriche normalizzate di METRIC_MAP."""
    records = []
    for metric, candidates in METRIC_MAP.items():
        for frame in statements:
            if frame is None or frame.empty:
                continue
            row_name = next((c for c in candidates if c in frame.index), None)
            if row_name is None:
                continue
            series = pd.to_numeric(frame.loc[row_name], errors="coerce").dropna()
            for period_end, value in series.items():
                records.append((ticker, pd.Timestamp(period_end).normalize(), metric, float(value)))
            break  # prima fonte valida per questa metrica
    df = pd.DataFrame(records, columns=["ticker", "period_end", "metric", "value"])
    if df.empty:
        return _EMPTY_FIN.copy()
    df = df.drop_duplicates(subset=["ticker", "period_end", "metric"], keep="first")

    # FCF mancante: Operating Cash Flow + Capital Expenditure (che in yfinance è negativo)
    wide = df.pivot_table(index="period_end", columns="metric", values="value", aggfunc="first")
    if "operating_cash_flow" in wide and "capex" in wide:
        derived = (wide["operating_cash_flow"] + wide["capex"]).dropna()
        have = set(wide["free_cash_flow"].dropna().index) if "free_cash_flow" in wide else set()
        extra = [(ticker, p, "free_cash_flow", float(v)) for p, v in derived.items() if p not in have]
        if extra:
            df = pd.concat([df, pd.DataFrame(extra, columns=df.columns)], ignore_index=True)
    return df


# ------------------------------------------------------------------ sorgente Yahoo
class YahooSource:
    """Adattatore per yfinance (importato solo quando serve)."""

    def __init__(self, timeout: float = 30.0):
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Manca yfinance: esegui `pip install -r requirements.txt`") from exc
        self.timeout = timeout
        # yfinance scrive un errore per ogni titolo delistato: il riepilogo lo fa già _download_with_retry
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    def download_prices(self, tickers: list[str], start: datetime) -> pd.DataFrame:
        import yfinance as yf

        with warnings.catch_warnings():  # yfinance usa API di pandas deprecate: avvisi non nostri
            warnings.simplefilter("ignore")
            raw = yf.download(
                tickers=tickers,
                start=start.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                threads=True,
                group_by="column",
                actions=True,  # aggiunge la colonna "Stock Splits"
                timeout=self.timeout,
            )
        return normalize_download(raw, tickers)

    def fetch_splits(self, ticker: str) -> pd.DataFrame:
        """Storico completo degli split (anche quelli precedenti ai prezzi salvati)."""
        import yfinance as yf

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s = yf.Ticker(ticker).splits
        if s is None or s.empty:
            return _EMPTY_SPLITS.copy()
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        return pd.DataFrame({"ticker": ticker, "date": idx.normalize(), "ratio": s.to_numpy(dtype=float)})

    def fetch_statements(self, ticker: str) -> pd.DataFrame:
        import yfinance as yf

        asset = yf.Ticker(ticker)
        statements = [asset.cashflow, asset.balance_sheet, asset.income_stmt]
        df = normalize_statements(ticker, statements)
        df["source"] = "yahoo"  # nessuna data di deposito: verrà stimata
        return df


# ------------------------------------------------------------------- ingestor
class DataIngestor:
    """Aggiorna prezzi e bilanci nel database.

    price_source: oggetto con download_prices(tickers, start) (Yahoo o demo)
    fundamentals_source: oggetto con fetch_statements(ticker) (SEC, Yahoo o demo)
    """

    def __init__(self, db, cfg: dict, price_source=None, fundamentals_source=None, max_workers: int = 4,
                 sleep=time.sleep, facts_store=None):
        self.db = db
        self.facts_store = facts_store  # archivio di tutti i valori SEC (FactsStore), solo con fonti che li danno
        self.cfg = cfg
        self.price_source = price_source
        self.fundamentals_source = fundamentals_source
        self.max_workers = max_workers
        self.sleep = sleep  # sostituibile nei test

    # -------------------------------------------------------------- prezzi
    def _attempts(self, tickers: list[str], start: datetime, attempts: int) -> tuple[list[pd.DataFrame], list[str]]:
        """Fino a `attempts` richieste per lo stesso gruppo, con attese crescenti; ogni nuovo tentativo chiede solo
        i ticker non ancora arrivati. Restituisce (dati arrivati, ticker ancora senza dati)."""
        remaining, frames = list(tickers), []
        for attempt in range(attempts):
            try:
                df = self.price_source.download_prices(remaining, start)
            except Exception as exc:
                logger.warning("Download fallito (tentativo %d/%d, %d ticker): %s", attempt + 1, attempts,
                               len(remaining), exc)
                df = pd.DataFrame()
            if not df.empty:
                frames.append(df)
                got = set(df["ticker"])
                remaining = [t for t in remaining if t not in got]
            if not remaining:
                break
            if attempt < attempts - 1:
                delay = 2 * 2 ** attempt
                logger.info("%d ticker senza dati, nuovo tentativo tra %ds", len(remaining), delay)
                self.sleep(delay)
        return frames, remaining

    def _bisect(self, tickers: list[str], start: datetime) -> tuple[list[pd.DataFrame], list[str]]:
        """Riprova a metà, poi a metà della metà... fino al singolo ticker (un tentativo per pezzo): un simbolo che
        fa fallire tutta la richiesta, o una risposta troncata da Yahoo, non deve lasciare senza dati gli altri."""
        pause = float(self.cfg["data"].get("price_chunk_pause", 1.0))
        frames, missing = [], []
        half = len(tickers) // 2
        for piece in (tickers[:half], tickers[half:]):
            self.sleep(pause)
            got, rest = self._attempts(piece, start, 1)
            frames += got
            if len(rest) > 1:
                more, rest = self._bisect(rest, start)
                frames += more
            missing += rest
        return frames, missing

    def _download_with_retry(self, tickers: list[str], start: datetime) -> pd.DataFrame:
        """Scarica un blocco; ritenta (con attese crescenti) i ticker che non sono arrivati, poi li riprova a gruppi
        sempre più piccoli fino al singolo ticker. yfinance spesso non solleva errori ma restituisce colonne vuote
        quando Yahoo limita; altre volte un solo simbolo fa fallire l'intera richiesta."""
        attempts = max(int(self.cfg["data"].get("price_retries", 3)), 1)
        frames, remaining = self._attempts(list(tickers), start, attempts)
        if len(remaining) > 1:
            logger.info("%d ticker senza dati: li riprovo a gruppi più piccoli", len(remaining))
            more, remaining = self._bisect(remaining, start)
            frames += more
        if remaining:
            logger.warning("Nessun prezzo per %d ticker: %s", len(remaining), remaining[:20])
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def lagging(self, tickers: Iterable[str]) -> tuple[pd.Timestamp | None, dict[str, pd.Timestamp | None]]:
        """(ultima seduta, {ticker: ultimo prezzo}) dei `tickers` fermi prima dell'ultima seduta. L'ultima seduta è
        l'ultimo prezzo del benchmark (SPY), o il più recente nel database se il benchmark manca; None = mai un prezzo."""
        last = self.db.last_price_dates()
        bench = self.cfg.get("universe", {}).get("benchmark")
        ref = last.get(bench) if bench in last else max(last.values(), default=None)
        if ref is None:
            return None, {}
        return ref, {t: last.get(t) for t in tickers if t not in last or last[t] < ref}

    def _report_lagging(self, tickers: list[str]) -> list[str]:
        """Controllo di fine aggiornamento: avviso (WARNING, con conteggio ed elenco) per i titoli dell'indice che
        restano indietro rispetto all'ultima seduta; gli altri (ex membri, Europa, cambi) solo come informazione."""
        ref, behind = self.lagging(tickers)
        if not behind:
            return []
        try:
            members = set(self.db.load_companies(only_index=True)["ticker"])
        except Exception:  # noqa: BLE001 - database senza anagrafica (es. universo da lista)
            members = set()
        members = members or set(tickers)
        index_behind = sorted(t for t in behind if t in members)
        others = len(behind) - len(index_behind)
        if index_behind:
            by_day: dict[str, int] = {}
            for t in index_behind:
                day = behind[t].date().isoformat() if behind[t] is not None else "mai"
                by_day[day] = by_day.get(day, 0) + 1
            days = ", ".join(f"{d} ({n})" for d, n in sorted(by_day.items(), reverse=True))
            logger.warning("Prezzi indietro: %d titoli dell'indice senza l'ultima seduta (%s); fermi al %s: %s",
                           len(index_behind), ref.date(), days, ", ".join(index_behind))
        if others:
            logger.info("Prezzi: %d titoli fuori indice (ex membri, Europa, cambi) senza la seduta del %s USA",
                        others, ref.date())
        return index_behind

    def update_prices(self, tickers: list[str], today: datetime | None = None) -> dict:
        today = today or datetime.now()
        full_start = today - timedelta(days=int(365.25 * self.cfg["data"]["history_years"]))
        last = self.db.last_price_dates()
        first = self.db.first_price_dates()
        chunk = int(self.cfg["data"].get("price_chunk_size", 100))
        pause = float(self.cfg["data"].get("price_chunk_pause", 1.0))
        # segna "storico completo già chiesto con questa profondità": cambia se cambia history_years
        marker = f"history_{self.cfg['data']['history_years']}y"
        requested = self.db.fetched_tickers(marker)

        # titoli fermi da molto (delistati: Yahoo non li ha più) che l'ultima volta non sono arrivati: si riprovano
        # solo ogni `price_dead_retry_days` giorni, per non pagare a ogni esecuzione tentativi e attese
        dead_days = pd.Timedelta(days=int(self.cfg["data"].get("price_dead_days", 30)))
        retry_dead = timedelta(days=int(self.cfg["data"].get("price_dead_retry_days", 7)))
        newest = max(last.values(), default=None)

        # raggruppa per data di partenza: storico completo (nuovi ticker, o storico più corto di quello
        # configurato: la richiesta completa sostituisce anche le righe già salvate) e incrementale
        groups: dict[datetime, list[str]] = {}
        full: list[str] = []
        resting: list[str] = []
        for t in tickers:
            too_short = t in first and first[t] > pd.Timestamp(full_start) + pd.Timedelta(days=30)
            if t not in last or too_short:
                if t not in requested:
                    groups.setdefault(full_start, []).append(t)
                    full.append(t)
                    continue
                if t not in last:
                    continue  # storico completo già chiesto e mai arrivato: Yahoo non ha il simbolo
                # Storico più corto ma GIÀ chiesto per intero: il titolo è quotato dopo l'inizio dello storico
                # (ABBV 2013, META 2012...). Prima qui si saltava il ticker per sempre: dal 2026-11-01 (storico
                # di 18 anni) 141 titoli, 89 dell'indice, sono rimasti fermi al 2026-10-30 senza alcun errore.
                # Ora prosegue con l'aggiornamento incrementale come gli altri.
            if newest is not None and newest - last[t] > dead_days:
                missing_at = self.db.last_fetched(t, "prices_missing")
                if missing_at is not None and today - missing_at < retry_dead:
                    resting.append(t)
                    continue
            # dal SUO ultimo giorno (non dall'ultimo giorno del database): chi è rimasto indietro recupera tutto
            start = last[t].to_pydatetime() - timedelta(days=5)  # sovrapposizione per eventuali correzioni
            if start.date() < today.date():
                # arrotonda alla settimana per avere pochi gruppi
                key = datetime(start.year, start.month, start.day) - timedelta(days=start.weekday())
                groups.setdefault(key, []).append(t)
        if resting:
            logger.info("Prezzi: %d ticker fermi da oltre %d giorni e non arrivati di recente, non richiesti: %s",
                        len(resting), dead_days.days, resting[:15])

        inserted, t0 = 0, time.time()
        n_chunks = sum((len(v) + chunk - 1) // chunk for v in groups.values())
        done = 0
        reload: set[str] = set()
        for start, group in sorted(groups.items()):
            for i in range(0, len(group), chunk):
                batch = group[i:i + chunk]
                done += 1
                logger.info("Prezzi blocco %d/%d: %d ticker dal %s", done, n_chunks, len(batch), start.date())
                df = self._download_with_retry(batch, start)
                got = set(df["ticker"]) if not df.empty else set()
                empty = [t for t in batch if t not in got and t in last]
                if empty:  # incrementali senza dati: annotati, servono a riconoscere i delistati (vedi sopra)
                    self.db.mark_fetched_many(empty, "prices_missing")
                inserted += self.db.upsert_prices(df)
                splits = extract_splits(df)
                self.db.upsert_splits(splits)
                # split successivo all'ultimo prezzo salvato: lo storico in archivio è sulla base vecchia
                reload |= {t for t, d in zip(splits["ticker"], splits["date"]) if t in last and d > last[t]}
                if done < n_chunks:
                    self.sleep(pause)
        self.db.mark_fetched_many(full, marker)
        if reload:
            self.reload_history(sorted(reload), full_start)
        logger.info("Prezzi aggiornati: %d righe in %.1fs", inserted, time.time() - t0)
        behind = self._report_lagging(list(tickers))
        return {"rows": inserted, "reloaded": sorted(reload), "behind": behind}

    def reload_history(self, tickers: list[str], full_start: datetime) -> list[str]:
        """Riscarica e sostituisce l'intero storico dei ticker indicati (stessa profondità di prima)."""
        first = self.db.first_price_dates()
        done = []
        for t in tickers:
            start = min(full_start, first[t].to_pydatetime()) if t in first else full_start
            logger.info("Split per %s: riscarico lo storico dal %s", t, start.date())
            df = self._download_with_retry([t], start)
            if df.empty:  # meglio uno storico vecchio che nessuno storico
                logger.error("Storico di %s non riscaricato: i prezzi prima dello split restano sulla base vecchia", t)
                continue
            self.db.replace_prices(t, df)
            self.db.upsert_splits(extract_splits(df))
            done.append(t)
        return done

    # -------------------------------------------------------------- split
    def update_splits(self, tickers: list[str], today: datetime | None = None) -> list[str]:
        """Scarica una volta per ticker lo storico completo degli split (i nuovi arrivano poi con i
        prezzi). Se l'archivio contiene già prezzi a cavallo di uno split non rettificato, li riscarica."""
        if self.price_source is None or not hasattr(self.price_source, "fetch_splits"):
            return []
        todo = [t for t in tickers if self.db.last_fetched(t, "splits") is None]
        if not todo:
            return []
        logger.info("Storico split per %d ticker...", len(todo))
        frames = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.price_source.fetch_splits, t): t for t in todo}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    frames.append(fut.result())
                    self.db.mark_fetched(t, "splits")
                except Exception as exc:
                    logger.error("Split non scaricati per %s: %s", t, exc)
        splits = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else _EMPTY_SPLITS
        if splits.empty:
            return []
        self.db.upsert_splits(splits)
        broken = sorted({t for t, d, r in zip(splits["ticker"], splits["date"], splits["ratio"])
                         if self._unadjusted_at(t, d, r)})
        if not broken:
            return []
        today = today or datetime.now()
        full_start = today - timedelta(days=int(365.25 * self.cfg["data"]["history_years"]))
        return self.reload_history(broken, full_start)

    def _unadjusted_at(self, ticker: str, date: pd.Timestamp, ratio: float) -> bool:
        """True se nei prezzi salvati il giorno dello split c'è un salto pari al rapporto
        (cioè lo storico precedente non è stato rettificato)."""
        px = self.db.load_prices([ticker]).set_index("date")["close"].dropna()
        before, after = px[px.index < date], px[px.index >= date]
        if before.empty or after.empty or before.iloc[-1] <= 0 or after.iloc[0] <= 0:
            return False
        jump = np.log(after.iloc[0] / before.iloc[-1])
        return abs(jump + np.log(ratio)) < abs(jump)

    # -------------------------------------------------------------- bilanci
    def _needs_fundamentals(self, ticker: str, last_end: pd.Timestamp | None, now: datetime) -> bool:
        """Riscarica solo se è plausibile che ci sia un documento nuovo (`last_end` = ultima chiusura, trimestre
        o esercizio, già nel database), oppure se il dato è vecchio."""
        d = self.cfg["data"]
        last = self.db.last_fetched(ticker, "fundamentals")
        if last is None or last_end is None:
            return True
        age = now - last
        if age > timedelta(days=d.get("fundamentals_max_age_days", 90)):
            return True
        report_due = pd.Timestamp(now) > last_end + pd.Timedelta(days=d.get("fundamentals_due_days", 130))
        return report_due and age > timedelta(days=d.get("fundamentals_refresh_days", 7))

    def update_fundamentals(self, tickers: list[str], force: bool = False, refresh: Iterable[str] = ()) -> dict:
        """Scarica i bilanci dei ticker che ne hanno bisogno: `refresh` (nuovi 10-Q/10-K trovati nell'indice
        SEC), quelli non ancora nell'archivio completo, quelli per cui è atteso un documento o troppo vecchi."""
        now = datetime.now()
        last_ends = self.db.last_period_ends()
        bundle = self.facts_store is not None and hasattr(self.fundamentals_source, "fetch_bundle")
        # archivio completo: chi non c'è ancora, tranne chi ha già fallito (es. ex membri senza dati SEC)
        missing = set(tickers) - self.facts_store.tickers() - self.db.fetched_tickers("sec_facts_failed") \
            if bundle else set()
        wanted = set(refresh) | missing
        todo = [t for t in tickers if force or t in wanted or self._needs_fundamentals(t, last_ends.get(t), now)]
        cik_map = getattr(self.fundamentals_source, "cik_map", None)
        if cik_map is not None:  # SEC: senza CIK non c'è nulla da chiedere (es. ex membri delistati)
            no_cik = [t for t in todo if cik_map.get(t) is None or pd.isna(cik_map.get(t))]
            if no_cik:
                logger.info("Bilanci: %d ticker senza CIK SEC saltati (in genere ex membri delistati): %s",
                            len(no_cik), no_cik[:15])
                todo = [t for t in todo if t not in set(no_cik)]
        if not todo:
            logger.info("Bilanci già aggiornati")
            return {}

        logger.info("Download bilanci per %d ticker...", len(todo))
        results: dict[str, bool] = {}
        t0 = time.time()
        fetch = self.fundamentals_source.fetch_bundle if bundle else self.fundamentals_source.fetch_statements
        give_up = int(self.cfg.get("sec", {}).get("filings_give_up_days", 10))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(fetch, t): t for t in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                t = futures[fut]
                try:
                    df = fut.result()
                    if bundle:  # trimestri singoli e archivio completo; i depositi ora presenti sono caricati
                        self.db.replace_quarterly(t, df["quarterly"])
                        self.facts_store.replace(t, df["facts"], df["labels"])
                        self.db.settle_filings(t, set(df["facts"]["accn"].dropna()), give_up)
                        df = df["statements"]
                    if df.empty:
                        logger.warning("Bilanci vuoti per %s", t)
                        results[t] = False
                    elif getattr(self.fundamentals_source, "full_history", False):
                        # la fonte dà sempre tutto lo storico: si sostituisce (scritture sul thread principale)
                        self.db.replace_financials(t, df, getattr(self.fundamentals_source, "sources",
                                                                  sorted(df["source"].unique())))
                        results[t] = True
                    else:
                        self.db.upsert_financials(df)  # Yahoo dà solo gli ultimi anni: si aggiunge
                        results[t] = True
                    self.db.mark_fetched(t, "fundamentals")
                except Exception as exc:
                    logger.error("Bilanci non scaricati per %s: %s", t, exc)
                    results[t] = False
                    if bundle:
                        self.db.mark_fetched(t, "sec_facts_failed")
                if i % 50 == 0:
                    logger.info("Bilanci: %d/%d (%.0fs)", i, len(todo), time.time() - t0)
        logger.info("Bilanci aggiornati: %d/%d in %.0fs", sum(results.values()), len(todo), time.time() - t0)
        return results

    def update_all(self, tickers: list[str], force_fundamentals: bool = False, refresh: Iterable[str] = ()) -> dict:
        """Prezzi, split e bilanci. Restituisce il risultato dei prezzi (righe, storici riscaricati, `behind` =
        titoli dell'indice ancora senza l'ultima seduta)."""
        u = self.cfg["universe"]
        extra = [t for t in (u.get("benchmark"), u.get("risk_free_ticker")) if t]
        all_tickers = list(dict.fromkeys(list(tickers) + extra))
        prices = self.update_prices(all_tickers)
        self.update_splits(all_tickers)
        if self.fundamentals_source is not None:
            self.update_fundamentals(list(tickers), force=force_fundamentals, refresh=refresh)
        return prices

    def load_csv(self, csv_path: str, kind: str) -> int:
        """Carica dati da CSV già in formato lungo (kind = 'prices' o 'financials')."""
        df = pd.read_csv(csv_path)
        if kind == "prices":
            return self.db.upsert_prices(df)
        if kind == "financials":
            return self.db.upsert_financials(df, source="csv")
        raise ValueError("kind deve essere 'prices' o 'financials'")
