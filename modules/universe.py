"""
Universo dei titoli: lista fissa da config oppure S&P 500 aggiornato da Wikipedia.

Per ogni azienda si salvano nel database (tabella `companies`): ticker in formato
Yahoo (BRK.B -> BRK-B), nome, settore e sotto-settore GICS, data di ingresso
nell'indice e CIK (il codice SEC necessario per scaricare i bilanci).
Il CIK mancante viene recuperato dal file ufficiale della SEC company_tickers.json.

Membership storica (`universe.membership: history`): dalla pagina Wikipedia "Historical components
of the S&P 500" (tabella dei cambi: chi entra e chi esce, con la data) si ricostruiscono gli
intervalli di appartenenza all'indice, ex membri compresi:
  - membri attuali: dalla data di ingresso (tabella dei costituenti) a oggi;
  - ex membri: dall'ultima entrata registrata prima dell'uscita (se nota) fino all'uscita.
Così il backtest a ogni data usa i titoli che erano DAVVERO nell'indice, anche quelli usciti dopo.
Gli ex membri ancora quotati hanno i prezzi su Yahoo; quelli delistati (acquisiti, falliti) no:
quella parte del survivorship bias resta e viene misurata nel backtest (copertura dell'universo).
La tabella dei cambi è completa solo dal 2011 circa: prima la ricostruzione è approssimativa.
Ticker riusati da un'altra azienda dopo l'uscita possono dare prezzi sbagliati (caso raro).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser

import pandas as pd

logger = logging.getLogger(__name__)

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_HISTORY_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def yahoo_symbol(symbol: str) -> str:
    """Simbolo in formato Yahoo (BRK.B -> BRK-B), senza residui di formattazione (es. "ITT |" su Wikipedia)."""
    return re.sub(r"[^A-Z0-9.\-]", "", str(symbol).upper()).replace(".", "-")


# ------------------------------------------------------------------ parsing Wikipedia
class _TableParser(HTMLParser):
    """Estrae le righe della tabella con l'id richiesto (solo libreria standard)."""

    def __init__(self, table_id: str):
        super().__init__()
        self.table_id = table_id
        self.depth = 0          # profondità delle tabelle annidate dentro quella cercata
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.depth == 0 and attrs.get("id") == self.table_id:
                self.depth = 1
            elif self.depth > 0:
                self.depth += 1
        elif self.depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
            elif tag == "br" and self._cell is not None:
                self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "table" and self.depth > 0:
            self.depth -= 1
        elif self.depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None

    def handle_data(self, data):
        if self.depth == 1 and self._cell is not None:
            self._cell.append(data)


def parse_sp500_wikipedia(html: str) -> pd.DataFrame:
    parser = _TableParser("constituents")
    parser.feed(html)
    if len(parser.rows) < 2:
        raise ValueError("Tabella 'constituents' non trovata nella pagina Wikipedia")
    header = [h.lower() for h in parser.rows[0]]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if h.startswith(n):
                    return i
        return None

    idx = {
        "symbol": col("symbol", "ticker"), "name": col("security", "company"),
        "sector": col("gics sector"), "sub_industry": col("gics sub-industry", "gics sub industry"),
        "date_added": col("date added", "date first added"), "cik": col("cik"),
    }
    if idx["symbol"] is None:
        raise ValueError(f"Colonna Symbol non trovata (intestazioni: {parser.rows[0]})")
    records = []
    for row in parser.rows[1:]:
        if len(row) <= idx["symbol"]:
            continue
        get = lambda k: row[idx[k]] if idx[k] is not None and idx[k] < len(row) else None  # noqa: E731
        records.append({
            "ticker": yahoo_symbol(get("symbol")), "name": get("name"), "sector": get("sector"),
            "sub_industry": get("sub_industry"), "date_added": get("date_added"), "cik": get("cik"),
        })
    df = pd.DataFrame(records)
    df["cik"] = pd.to_numeric(df["cik"], errors="coerce").astype("Int64")
    df["date_added"] = pd.to_datetime(df["date_added"].str.extract(r"(\d{4}-\d{2}-\d{2})")[0], errors="coerce")
    return df.drop_duplicates("ticker").reset_index(drop=True)


def parse_sp500_changes(html: str) -> pd.DataFrame:
    """Tabella "changes": data effettiva, ticker e nome di chi entra e di chi esce, motivo.
    Le prime due righe sono l'intestazione su due livelli; righe con 6 o 7 celle (a volte manca il riferimento)."""
    parser = _TableParser("changes")
    parser.feed(html)
    records = []
    for row in parser.rows:
        if len(row) < 6:
            continue
        date = pd.to_datetime(row[0], format="%B %d, %Y", errors="coerce")
        if pd.isna(date):
            continue  # righe di intestazione
        sym = lambda v: yahoo_symbol(v) if v and v.strip() else None  # noqa: E731
        records.append({"date": date, "added": sym(row[1]), "added_name": row[2] or None,
                        "removed": sym(row[3]), "removed_name": row[4] or None, "reason": row[5] or None})
    if not records:
        raise ValueError("Tabella 'changes' non trovata o vuota nella pagina Wikipedia")
    return pd.DataFrame(records).sort_values("date", kind="stable").reset_index(drop=True)


def build_membership(constituents: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """Intervalli di appartenenza all'indice: ticker, name, start (NaT = da sempre), end (NaT = ancora dentro).
    Un ticker uscito e rientrato ha più intervalli."""
    rows = [{"ticker": r.ticker, "name": getattr(r, "name", None), "start": pd.Timestamp(r.date_added)
             if pd.notna(getattr(r, "date_added", None)) else pd.NaT, "end": pd.NaT}
            for r in constituents.itertuples(index=False)]
    if changes is not None and not changes.empty:
        ch = changes.sort_values("date", kind="stable")
        for r in ch[ch["removed"].notna()].itertuples(index=False):
            same = ch[(ch["date"] < r.date)]
            prev_removal = same.loc[same["removed"] == r.removed, "date"].max()
            adds = same[(same["added"] == r.removed)
                        & (same["date"] > prev_removal if pd.notna(prev_removal) else True)]["date"]
            rows.append({"ticker": r.removed, "name": r.removed_name, "start": adds.max() if not adds.empty
                         else pd.NaT, "end": pd.Timestamp(r.date)})
    out = pd.DataFrame(rows, columns=["ticker", "name", "start", "end"])
    return out.drop_duplicates(["ticker", "start", "end"]).reset_index(drop=True)


def members_at(membership: pd.DataFrame, date) -> list[str]:
    """Titoli nell'indice alla data: entrati entro quel giorno e non ancora usciti (chi esce il giorno D è fuori)."""
    d = pd.Timestamp(date)
    m = membership
    inside = (m["start"].isna() | (m["start"] <= d)) & (m["end"].isna() | (m["end"] > d))
    return sorted(set(m.loc[inside, "ticker"]))


def parse_sec_tickers(payload: dict) -> dict[str, int]:
    """company_tickers.json: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}"""
    return {yahoo_symbol(v["ticker"]): int(v["cik_str"]) for v in payload.values()}


class WebUniverseProvider:
    min_expected = 400  # meno titoli di così = pagina cambiata o letta male: non si aggiorna

    def __init__(self, http):
        self.http = http

    def sp500_constituents(self) -> pd.DataFrame:
        return parse_sp500_wikipedia(self.http.get_text(WIKI_SP500_URL))

    def sec_ticker_map(self) -> dict[str, int]:
        return parse_sec_tickers(self.http.get_json(SEC_TICKERS_URL))

    def sp500_changes(self) -> pd.DataFrame:
        return parse_sp500_changes(self.http.get_text(WIKI_HISTORY_URL))


# ------------------------------------------------------------------ gestore
class UniverseManager:
    def __init__(self, db, cfg: dict, provider=None):
        self.db = db
        self.cfg = cfg
        self.u = cfg["universe"]
        self.provider = provider

    @property
    def source(self) -> str:
        return self.u.get("source", "list")

    def refresh(self, force: bool = False) -> None:
        """Aggiorna la lista S&P 500 (al massimo ogni `refresh_days`) e completa i CIK mancanti."""
        if self.provider is None:
            return
        if self.source == "sp500":
            last = self.db.last_fetched("__SP500__", "universe")
            if force or last is None or datetime.now() - last > timedelta(days=self.u.get("refresh_days", 7)):
                logger.info("Aggiornamento lista S&P 500...")
                df = self.provider.sp500_constituents()
                if len(df) < getattr(self.provider, "min_expected", 400):
                    logger.warning("Lista S&P 500 sospetta (%d titoli): non aggiorno", len(df))
                else:
                    self.db.replace_index_members(df)
                    self.db.mark_fetched("__SP500__", "universe")
                    logger.info("S&P 500: %d titoli", len(df))
                    self._refresh_membership(df)
            elif self.u.get("membership", "history") == "history" and self.db.load_membership().empty:
                # lista recente ma storico mai scaricato (es. prima esecuzione dopo l'aggiornamento)
                self._refresh_membership(self.db.load_companies(only_index=True))
        missing = [t for t in self.data_tickers() if self.db.company_ciks().get(t) is None]
        if missing and self.cfg["data"].get("fundamentals_source") == "sec":
            logger.info("Recupero CIK SEC per %d ticker", len(missing))
            try:
                mapping = self.provider.sec_ticker_map()
                found = {t: mapping[t] for t in missing if t in mapping}
                self.db.set_ciks(found)
                still = sorted(set(missing) - set(found))
                if still:
                    logger.warning("CIK non trovato per: %s", still)
            except Exception as exc:
                logger.error("Mappa ticker->CIK SEC non scaricata: %s", exc)

    def _refresh_membership(self, constituents: pd.DataFrame) -> None:
        if self.u.get("membership", "history") != "history" or not hasattr(self.provider, "sp500_changes"):
            return
        try:
            changes = self.provider.sp500_changes()
        except Exception as exc:  # senza storico si ripiega sulle date di ingresso dei membri attuali
            logger.error("Storico della composizione S&P 500 non scaricato: %s", exc)
            return
        membership = build_membership(constituents, changes)
        self.db.replace_membership(membership)
        former = membership.loc[membership["end"].notna(), "ticker"].nunique()
        logger.info("Composizione storica: %d cambi dal %s, %d ex membri",
                    len(changes), changes["date"].min().date(), former)

    def _membership(self) -> pd.DataFrame | None:
        if self.u.get("membership", "history") != "history":
            return None
        m = self.db.load_membership()
        return None if m.empty else m

    def tickers(self, as_of=None) -> list[str]:
        """Membri dell'indice: oggi, oppure (con as_of) quelli che c'erano a quella data, ex membri compresi."""
        if self.source != "sp500":
            return list(self.u["tickers"])
        comp = self.db.load_companies(only_index=True)
        if comp.empty:
            raise RuntimeError("Lista S&P 500 vuota: esegui senza --no-update per scaricarla")
        if as_of is not None and self.u.get("point_in_time_membership", True):
            membership = self._membership()
            if membership is not None:
                return members_at(membership, as_of)
            added = comp["date_added"]
            comp = comp[added.isna() | (added <= pd.Timestamp(as_of))]
        return sorted(comp["ticker"].tolist())

    def data_tickers(self, since=None) -> list[str]:
        """Titoli di cui servono i dati: membri attuali + ex membri usciti dopo `since` (per il backtest) + aziende
        aggiunte dall'utente ed europee (non entrano nell'universo dei backtest, che segue la composizione dell'indice)."""
        current = self.tickers()
        custom = set(self.db.load_custom_companies()["ticker"])  # aziende aggiunte dall'utente
        custom |= set(self.db.load_foreign()["ticker"])         # aziende europee (FTSE MIB, Euro Stoxx 50)
        from modules.portfolio import needed_symbols

        custom |= set(needed_symbols(self.db.load_transactions()))  # titoli del portafoglio e cambi (EURUSD=X)
        membership = self._membership() if self.source == "sp500" else None
        if membership is None:
            return sorted(set(current) | custom)
        since = pd.Timestamp(since) if since is not None else pd.Timestamp.min
        former = membership.loc[membership["end"].notna() & (membership["end"] > since), "ticker"]
        return sorted(set(current) | set(former) | custom)

    def sectors(self) -> dict[str, str]:
        comp = self.db.load_companies(only_index=False)
        return dict(zip(comp["ticker"], comp["sector"])) if not comp.empty else {}
