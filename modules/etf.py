"""
Guardare dentro gli ETF: quanto si possiede DAVVERO di ogni azienda, settore e paese sommando ETF e azioni dirette,
e quanto si sovrappongono due ETF.

Fonte delle composizioni: Yahoo (yfinance `Ticker.funds_data`, modulo quoteSummary "topHoldings"). Cosa c'è davvero
(verificato a novembre 2026 su SPY, VWCE.DE, SXR8.DE, CSPX.L, EQQQ.MI, VUAA.MI, IWDA.AS, IUSN.DE, AGGH.MI):
- solo le PRIME 10 posizioni (simbolo Yahoo, nome, peso): ~43% di SPY, ~25% di VWCE, ~2% di un ETF small cap;
- pesi per settore dell'intero fondo (11 settori Morningstar, sommano a 1 sulla parte azionaria);
- ripartizione per classe di attività (azioni, obbligazioni, liquidità, altro);
- NIENTE paesi, niente data di riferimento della composizione (si registra la data dello scarico), niente posizioni
  per gli ETF obbligazionari (AGGH.MI: posizioni e settori vuoti, solo i rating).
Quindi ogni risultato dichiara la quota "non dettagliata" (il resto del fondo) invece di fingere completezza:
l'esposizione per azienda tramite gli ETF è un LIMITE INFERIORE, la sovrapposizione tra due ETF anche.

Simboli della stessa azienda scritti in modo diverso (normalizzazione prudente, `canonical_map`):
- classi: BRK.B, BRK/B, "BRK B" -> BRK-B (formato Yahoo), solo per una lettera A-E dopo il separatore (".L", ".F",
  ".V", ".T" sono borse, non classi);
- quotazioni secondarie (NVDA.DE, AAPL.MI...): ricondotte al simbolo senza suffisso SOLO se quel simbolo è presente e i
  nomi coincidono (stessa prima parola significativa o nomi molto simili). Senza nomi non si unisce nulla: ALV.DE è
  Allianz, ALV è Autoliv. I casi dubbi finiscono in `possible_duplicates`; `aliases` forza un'unione a mano;
- classi diverse della stessa azienda (GOOGL e GOOG, "Alphabet Inc Class A/C"): restano simboli distinti ma con la
  stessa chiave `company` (nome senza forma societaria e classe), e la tabella per azienda le somma.

Paese: dal suffisso del simbolo Yahoo = paese di QUOTAZIONE, non di sede (TSM e ASML quotate negli USA risultano
"Stati Uniti"); per le azioni dirette si può passare il paese vero (`company_country`).
Settori: quelli di Yahoo (Morningstar) tradotti nei nomi GICS usati nel resto del progetto (`YAHOO_SECTORS`); le due
classificazioni non coincidono del tutto (es. alcuni titoli di pagamento o di commercio), le differenze sono piccole.

Funzioni pure su tabelle; l'ingestione (`EtfIngestor`) ha la sorgente e la cache iniettabili (test senza rete).
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# parametri (da spostare in config.yaml, sezione etf:, vedi rapporto)
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "etf"   # ignorata da git
CACHE_FILE = "composizioni.json"
CACHE_MAX_AGE_DAYS = 30          # Yahoo aggiorna le composizioni circa una volta al mese
NOT_FUND_MAX_AGE_DAYS = 180      # un simbolo riconosciuto come azione si ricontrolla di rado
FETCH_PAUSE_SECONDS = 0.5        # pausa tra due simboli scaricati (Yahoo risponde "Too Many Requests")
TOP_N = 10                       # nomi mostrati in `concentration`
NAME_SIMILARITY = 0.85           # soglia di difflib per considerare uguali due nomi (oltre alla prima parola)
SECTOR_SUM_TOLERANCE = 0.03      # pesi per settore che sommano a 1 +- questo: si riportano a 1

DIRECT = "diretta"
UNDETAILED = "Non dettagliato"
UNCLASSIFIED = "Non classificato"
BONDS, CASH, OTHER = "Obbligazioni", "Liquidità", "Altro"
FUND_TYPES = {"ETF", "MUTUALFUND"}

# settori Morningstar di Yahoo -> nomi GICS del progetto
YAHOO_SECTORS = {"technology": "Information Technology", "communication_services": "Communication Services",
                 "consumer_cyclical": "Consumer Discretionary", "consumer_defensive": "Consumer Staples",
                 "financial_services": "Financials", "healthcare": "Health Care", "industrials": "Industrials",
                 "energy": "Energy", "basic_materials": "Materials", "realestate": "Real Estate",
                 "utilities": "Utilities"}

# suffisso Yahoo -> paese della borsa (di quotazione, non di sede)
SUFFIX_COUNTRY = {"": "Stati Uniti", "TW": "Taiwan", "TWO": "Taiwan", "T": "Giappone", "HK": "Hong Kong",
                  "SS": "Cina", "SZ": "Cina", "KS": "Corea del Sud", "KQ": "Corea del Sud", "L": "Regno Unito",
                  "PA": "Francia", "DE": "Germania", "F": "Germania", "AS": "Paesi Bassi", "MI": "Italia",
                  "MC": "Spagna", "SW": "Svizzera", "TO": "Canada", "V": "Canada", "AX": "Australia",
                  "CO": "Danimarca", "ST": "Svezia", "HE": "Finlandia", "OL": "Norvegia", "BR": "Belgio",
                  "LS": "Portogallo", "VI": "Austria", "IR": "Irlanda", "NS": "India", "BO": "India",
                  "SA": "Brasile", "MX": "Messico", "JO": "Sudafrica", "SI": "Singapore", "NZ": "Nuova Zelanda"}

# borse dove si comprano quotazioni secondarie di azioni estere (NVDA.DE, AAPL.MI): candidate all'unione col simbolo
# senza suffisso, sempre con il controllo dei nomi
SECONDARY_SUFFIXES = {"DE", "F", "MI", "VI", "MU", "BE", "DU", "HM", "SG", "SW", "AS", "PA", "MC"}

_CLASS_RE = re.compile(r"^([A-Z]{1,5})[./ ]([A-E])$")
_SUFFIX_RE = re.compile(r"^(.+)\.([A-Z]{1,3})$")
_LEGAL_FORMS = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "sa", "spa",
                "ag", "nv", "se", "the", "holdings", "holding", "group", "class", "cl", "shs", "shares", "ord",
                "adr", "ads", "reg", "registered", "de", "and", "&", "a", "b", "c", "d", "e"}


# ---------------------------------------------------------------------------------------------- composizioni
@dataclass
class EtfComposition:
    """Composizione nota di un fondo. `holdings`: colonne symbol, name, weight (frazioni, di solito le prime 10);
    `sectors`: settore GICS -> peso sulla parte azionaria; `asset_classes`: stock/bond/cash/other -> peso;
    `fetched`: data dello scarico (Yahoo non dà la data della composizione)."""
    symbol: str
    name: str = ""
    quote_type: str = "ETF"
    currency: str | None = None
    holdings: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["symbol", "name", "weight"]))
    sectors: dict[str, float] = field(default_factory=dict)
    asset_classes: dict[str, float] = field(default_factory=dict)
    fetched: str | None = None
    source: str = "yahoo"

    @property
    def is_fund(self) -> bool:
        return self.quote_type in FUND_TYPES

    @property
    def detailed_weight(self) -> float:
        """Quota del fondo coperta dalle posizioni note (il resto è "non dettagliato")."""
        return float(self.holdings["weight"].sum()) if not self.holdings.empty else 0.0

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "name": self.name, "quote_type": self.quote_type, "currency": self.currency,
                "holdings": self.holdings[["symbol", "name", "weight"]].to_dict("records"),
                "sectors": dict(self.sectors), "asset_classes": dict(self.asset_classes),
                "fetched": self.fetched, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> EtfComposition:
        h = pd.DataFrame(d.get("holdings") or [], columns=["symbol", "name", "weight"])
        h["weight"] = h["weight"].astype(float)
        return cls(symbol=d["symbol"], name=d.get("name") or "", quote_type=d.get("quote_type") or "",
                   currency=d.get("currency"), holdings=h, sectors=dict(d.get("sectors") or {}),
                   asset_classes=dict(d.get("asset_classes") or {}), fetched=d.get("fetched"),
                   source=d.get("source") or "yahoo")


def parse_yahoo(raw: dict, fetched: str) -> EtfComposition:
    """Dal formato grezzo di `YahooFundsSource.fetch` a una composizione: settori tradotti in GICS, pesi in frazioni,
    posizioni ordinate per peso (simboli mancanti = nome in maiuscolo)."""
    rows = []
    for h in raw.get("holdings") or []:
        w = h.get("weight")
        if w is None or not np.isfinite(float(w)):
            continue
        sym = (h.get("symbol") or "").strip() or (h.get("name") or "?").upper()
        rows.append({"symbol": sym, "name": h.get("name") or "", "weight": float(w)})
    holdings = pd.DataFrame(rows, columns=["symbol", "name", "weight"])
    if not holdings.empty and holdings["weight"].sum() > 1.5:            # percentuali invece di frazioni
        holdings["weight"] = holdings["weight"] / 100.0
    holdings = holdings.sort_values(["weight", "symbol"], ascending=[False, True]).reset_index(drop=True)
    sectors = {}
    for k, w in (raw.get("sectors") or {}).items():
        if w is None or not np.isfinite(float(w)) or float(w) <= 0:
            continue
        name = YAHOO_SECTORS.get(k, k)
        sectors[name] = sectors.get(name, 0.0) + float(w)
    ac = raw.get("asset_classes") or {}
    asset_classes = {"stock": ac.get("stockPosition"), "bond": ac.get("bondPosition"), "cash": ac.get("cashPosition"),
                     "other": sum(float(ac.get(k) or 0.0) for k in
                                  ("otherPosition", "preferredPosition", "convertiblePosition"))}
    asset_classes = {k: float(v) for k, v in asset_classes.items() if v is not None and np.isfinite(float(v))}
    return EtfComposition(symbol=raw["symbol"], name=raw.get("name") or "", quote_type=raw.get("quote_type") or "",
                          currency=raw.get("currency"), holdings=holdings, sectors=sectors,
                          asset_classes=asset_classes, fetched=fetched, source=raw.get("source") or "yahoo")


class YahooFundsSource:
    """Composizioni da Yahoo con yfinance (due richieste per simbolo: tipo e nome da `info`, poi `funds_data` solo
    per i fondi). Restituisce un dizionario grezzo o None se il simbolo non esiste."""

    def fetch(self, symbol: str) -> dict | None:
        import yfinance as yf

        t = yf.Ticker(symbol)
        try:
            info = t.info or {}
        except Exception as exc:                                         # 404: simbolo inesistente
            logger.warning("ETF %s: dati Yahoo non disponibili (%s)", symbol, exc)
            return None
        qt = (info.get("quoteType") or "").upper()
        if not qt:
            return None
        raw = {"symbol": symbol, "quote_type": qt, "name": info.get("longName") or info.get("shortName") or "",
               "currency": info.get("currency"), "holdings": [], "sectors": {}, "asset_classes": {},
               "source": "yahoo"}
        if qt not in FUND_TYPES:
            return raw
        try:
            fd = t.funds_data
            top = fd.top_holdings
            raw["holdings"] = [{"symbol": str(s), "name": str(r["Name"]), "weight": float(r["Holding Percent"])}
                               for s, r in top.iterrows()]
            raw["sectors"] = dict(fd.sector_weightings or {})
            raw["asset_classes"] = dict(fd.asset_classes or {})
        except Exception as exc:
            logger.warning("ETF %s: composizione non disponibile (%s)", symbol, exc)
        return raw


class JsonCache:
    """Cache delle composizioni in un file JSON (default data/cache/etf/composizioni.json). Si comporta come un
    dizionario simbolo -> dizionario di `EtfComposition.to_dict`; `save()` scrive il file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else CACHE_DIR / CACHE_FILE
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError) as exc:
            logger.warning("Cache ETF illeggibile (%s): si riparte da vuota", exc)
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __setitem__(self, key, value):
        self.data[key] = value

    def __contains__(self, key):
        return key in self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class EtfIngestor:
    """Composizioni con cache e scadenza. `source`: oggetto con `fetch(symbol) -> dict | None` (Yahoo o finta nei
    test); `cache`: qualunque mappatura simbolo -> dict (un dict, `JsonCache`, in futuro una tabella SQLite)."""

    def __init__(self, source=None, cache=None, max_age_days: int = CACHE_MAX_AGE_DAYS,
                 not_fund_max_age_days: int = NOT_FUND_MAX_AGE_DAYS, pause: float = FETCH_PAUSE_SECONDS):
        self.source = source if source is not None else YahooFundsSource()
        self.cache = cache if cache is not None else {}
        self.max_age_days = max_age_days
        self.not_fund_max_age_days = not_fund_max_age_days
        self.pause = pause

    def _fresh(self, entry: dict, today: pd.Timestamp) -> bool:
        if not entry or not entry.get("fetched"):
            return False
        limit = self.max_age_days if entry.get("quote_type") in FUND_TYPES else self.not_fund_max_age_days
        return (today - pd.Timestamp(entry["fetched"])).days <= limit

    def get(self, symbol: str, today=None, refresh: bool = False) -> EtfComposition | None:
        """Composizione di `symbol` (anche per un'azione: `quote_type` EQUITY e niente posizioni). Dalla cache se
        non scaduta; se lo scarico fallisce si usa la copia scaduta, se c'è (meglio vecchia che niente)."""
        today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
        entry = self.cache.get(symbol)
        if entry and not refresh and self._fresh(entry, today):
            return EtfComposition.from_dict(entry)
        try:
            raw = self.source.fetch(symbol)
        except Exception as exc:
            logger.warning("ETF %s: scarico fallito (%s)", symbol, exc)
            raw = None
        if raw is None:
            return EtfComposition.from_dict(entry) if entry else None
        comp = parse_yahoo(raw, today.strftime("%Y-%m-%d"))
        self.cache[symbol] = comp.to_dict()
        return comp

    def get_many(self, symbols, today=None, refresh: bool = False) -> dict[str, EtfComposition]:
        """Composizioni di più simboli (in ordine alfabetico, pausa solo tra due scarichi veri). Salva la cache se
        ha un metodo `save`."""
        out, fetched_before = {}, False
        today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
        for s in sorted(set(symbols)):
            entry = self.cache.get(s)
            will_fetch = refresh or not (entry and self._fresh(entry, today))
            if will_fetch and fetched_before and self.pause:
                time.sleep(self.pause)
            comp = self.get(s, today=today, refresh=refresh)
            fetched_before = fetched_before or will_fetch
            if comp is not None:
                out[s] = comp
        if hasattr(self.cache, "save"):
            self.cache.save()
        return out

    def is_etf(self, symbol: str, today=None) -> bool | None:
        """True per ETF e fondi, False per le azioni, None se il simbolo è sconosciuto."""
        comp = self.get(symbol, today=today)
        return None if comp is None else comp.is_fund


def settings(cfg: dict | None) -> dict:
    """Parametri della sezione `etf:` di config.yaml, con i valori di questo modulo come riserva."""
    e = dict((cfg or {}).get("etf") or {})
    cache_dir = Path(e.get("cache_dir") or CACHE_DIR)
    if not cache_dir.is_absolute():
        cache_dir = CACHE_DIR.parent.parent.parent / cache_dir
    return {"cache_dir": cache_dir,
            "max_age_days": int(e.get("max_age_days", CACHE_MAX_AGE_DAYS)),
            "not_fund_max_age_days": int(e.get("not_fund_max_age_days", NOT_FUND_MAX_AGE_DAYS)),
            "pause": float(e.get("fetch_pause_seconds", FETCH_PAUSE_SECONDS)),
            "top_n": int(e.get("top_n", TOP_N)),
            "aliases": {str(k): str(v) for k, v in (e.get("aliases") or {}).items()}}


def ingestor_from_config(cfg: dict | None, source=None, cache=None, cache_file: str = CACHE_FILE) -> EtfIngestor:
    """`EtfIngestor` con scadenze e pausa della config; cache JSON in `cache_dir/cache_file` se non passata."""
    s = settings(cfg)
    return EtfIngestor(source=source, cache=cache if cache is not None else JsonCache(s["cache_dir"] / cache_file),
                       max_age_days=s["max_age_days"], not_fund_max_age_days=s["not_fund_max_age_days"],
                       pause=s["pause"])


def split_compositions(comps: dict[str, EtfComposition]) -> tuple[dict, dict, dict]:
    """(posizioni, settori, classi di attività) dei soli fondi, nel formato di `look_through`."""
    funds = {s: c for s, c in comps.items() if c.is_fund}
    return ({s: c.holdings for s, c in funds.items()}, {s: c.sectors for s, c in funds.items()},
            {s: c.asset_classes for s, c in funds.items()})


# ---------------------------------------------------------------------------------------------- simboli e nomi
def name_key(name: str | None) -> str:
    """Nome senza punteggiatura, forma societaria e classe: "Alphabet Inc Class A" -> "alphabet"."""
    if not name:
        return ""
    words = re.sub(r"[^a-z0-9& ]", " ", str(name).lower().replace("'", "")).split()
    return " ".join(w for w in words if w not in _LEGAL_FORMS)


def names_match(a: str | None, b: str | None, threshold: float = NAME_SIMILARITY) -> bool:
    """Stessa azienda? Serve un nome per entrambi: stessa prima parola significativa o nomi molto simili."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return False
    if ka.split()[0] == kb.split()[0]:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= threshold


def _class_form(symbol: str) -> str:
    s = str(symbol).strip().upper()
    m = _CLASS_RE.match(s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


def canonical_map(names: dict[str, str | None], aliases: dict[str, str] | None = None) -> tuple[dict, list]:
    """Simbolo -> simbolo canonico, e i possibili doppioni non uniti per prudenza (nomi mancanti o diversi).
    Ordine delle regole: alias a mano, forma della classe (BRK.B -> BRK-B), quotazione secondaria (NVDA.DE -> NVDA se
    NVDA c'è e i nomi coincidono)."""
    aliases = {k.strip().upper(): v.strip().upper() for k, v in (aliases or {}).items()}
    first = {}
    for s in sorted(names):
        up = str(s).strip().upper()
        first[s] = aliases.get(up, _class_form(up))
    # nomi per il simbolo nella forma della classe (il primo non vuoto in ordine alfabetico)
    name_of: dict[str, str] = {}
    for s in sorted(names):
        if names[s] and first[s] not in name_of:
            name_of[first[s]] = names[s]
    targets = set(first.values())
    mapping, doubtful = {}, []
    for s in sorted(names):
        c = first[s]
        m = _SUFFIX_RE.match(c)
        if str(s).strip().upper() not in aliases and m and m.group(2) in SECONDARY_SUFFIXES:
            base = _class_form(m.group(1))
            if base in targets and not _SUFFIX_RE.match(base):
                if names_match(names.get(s) or name_of.get(c), name_of.get(base)):
                    c = base
                else:
                    doubtful.append({"symbol": s, "candidate": base, "name": names.get(s) or name_of.get(c) or "",
                                     "candidate_name": name_of.get(base, "")})
        mapping[s] = c
    return mapping, doubtful


def country_of(symbol: str) -> str:
    """Paese della borsa dal suffisso Yahoo (nessun suffisso = USA). Sconosciuto -> "Non classificato"."""
    m = _SUFFIX_RE.match(str(symbol).strip().upper())
    if not m:
        return SUFFIX_COUNTRY[""]
    return SUFFIX_COUNTRY.get(m.group(2), UNCLASSIFIED)


# ---------------------------------------------------------------------------------------------- analisi
def _values(positions) -> pd.DataFrame:
    """Posizioni come tabella ticker, value_eur, name (accetta la tabella di `portfolio.positions` o un dict)."""
    if isinstance(positions, dict):
        df = pd.DataFrame({"ticker": list(positions), "value_eur": [float(v) for v in positions.values()]})
    else:
        df = positions.copy()
    if "name" not in df:
        df["name"] = ""
    df["name"] = df["name"].fillna("")
    return df[["ticker", "value_eur", "name"]]


def _fund_sectors(sectors: dict[str, float] | None, asset_classes: dict[str, float] | None) -> dict[str, float]:
    """Pesi del fondo per settore sull'INTERO fondo: settori x parte azionaria, poi obbligazioni, liquidità, altro;
    quello che manca per arrivare a 1 è "Non classificato"."""
    ac = asset_classes or {}
    sec = {k: v for k, v in (sectors or {}).items() if v and v > 0}
    total = sum(sec.values())
    if sec and abs(total - 1.0) <= SECTOR_SUM_TOLERANCE:
        sec = {k: v / total for k, v in sec.items()}
    stock = ac.get("stock", 1.0 if sec else 0.0)
    out = {k: v * stock for k, v in sec.items()}
    for key, label in (("bond", BONDS), ("cash", CASH), ("other", OTHER)):
        if ac.get(key, 0.0) > 0:
            out[label] = out.get(label, 0.0) + ac[key]
    rest = 1.0 - sum(out.values())
    if rest > 1e-6:
        out[UNCLASSIFIED] = out.get(UNCLASSIFIED, 0.0) + rest
    elif rest < -1e-6:                                                     # somme sopra 1 (arrotondamenti di Yahoo)
        s = sum(out.values())
        out = {k: v / s for k, v in out.items()}
    return out


def look_through(positions, holdings: dict[str, pd.DataFrame], sectors: dict[str, dict[str, float]],
                 company_sector: dict[str, str], asset_classes: dict[str, dict[str, float]] | None = None,
                 company_country: dict[str, str] | None = None, etfs=None,
                 aliases: dict[str, str] | None = None) -> dict:
    """Esposizione effettiva del portafoglio.

    `positions`: tabella con ticker, value_eur (e name, facoltativo) o dict ticker -> euro.
    `holdings`: ETF -> tabella symbol, name, weight (posizioni note, di solito le prime 10).
    `sectors`: ETF -> settore -> peso sulla parte azionaria; `asset_classes`: ETF -> stock/bond/cash/other.
    `company_sector` / `company_country`: settore e paese delle azioni dirette (e, se presenti, anche dei titoli dentro
    gli ETF); per i titoli dentro gli ETF senza settore noto si usa "Non classificato", per il paese il suffisso.
    `etfs`: altri simboli da trattare come ETF anche senza composizione (tutto "non dettagliato").
    Un ticker è un ETF se è in `holdings`, `sectors` o `etfs`; tutto il resto è un'azione diretta.

    Restituisce un dizionario:
    - companies: per azienda (simbolo canonico) direct_eur, etf_eur, total_eur, weight (sul totale del portafoglio),
      sources ("da dove viene"), company (chiave per sommare le classi), sector, country;
    - by_company: le stesse cifre sommate per `company` (GOOGL + GOOG = Alphabet);
    - sources: tabella lunga symbol, source (ETF o "diretta"), value_eur;
    - sectors / countries: direct_eur, etf_eur, total_eur, weight (per paese il non dettagliato è una riga a parte);
    - funds: per ETF value_eur, detailed_pct, detailed_eur, undetailed_eur, n_holdings;
    - summary: total_eur, direct_eur, etf_eur, detailed_eur, undetailed_eur, undetailed_pct, missing_value;
    - possible_duplicates: simboli forse della stessa azienda non uniti per prudenza.
    """
    pos = _values(positions)
    missing = sorted(pos.loc[~np.isfinite(pos["value_eur"].astype(float)), "ticker"].astype(str))
    pos = pos[np.isfinite(pos["value_eur"].astype(float))]
    pos = pos.groupby("ticker", as_index=False, sort=True).agg(value_eur=("value_eur", "sum"), name=("name", "first"))
    fund_set = set(holdings) | set(sectors) | set(etfs or [])
    asset_classes = asset_classes or {}
    company_sector = company_sector or {}
    company_country = company_country or {}

    # nomi di tutti i simboli (dirette + posizioni degli ETF) per la normalizzazione
    names: dict[str, str | None] = {}
    for r in pos.itertuples(index=False):
        if r.ticker not in fund_set:
            names[r.ticker] = r.name or names.get(r.ticker)
    for etf in sorted(holdings):
        for h in holdings[etf].itertuples(index=False):
            names[h.symbol] = names.get(h.symbol) or h.name
    canon, doubtful = canonical_map(names, aliases)

    rows = []                                                              # (symbol, source, value)
    funds = []
    sector_rows = []                                                       # (sector, kind, value)
    country_rows = []
    for r in pos.itertuples(index=False):
        t, v = r.ticker, float(r.value_eur)
        if t in fund_set:
            h = holdings.get(t)
            h = h if h is not None else pd.DataFrame(columns=["symbol", "name", "weight"])
            detailed = 0.0
            for x in h.itertuples(index=False):
                val = v * float(x.weight)
                rows.append((canon[x.symbol], t, val))
                country_rows.append((company_country.get(canon[x.symbol]) or country_of(canon[x.symbol]), "etf", val))
                detailed += float(x.weight)
            funds.append({"etf": t, "value_eur": v, "detailed_pct": detailed, "detailed_eur": v * detailed,
                          "undetailed_eur": v * (1 - detailed), "n_holdings": len(h),
                          "has_sectors": bool(sectors.get(t))})
            country_rows.append((UNDETAILED, "etf", v * (1 - detailed)))
            for sec, w in _fund_sectors(sectors.get(t), asset_classes.get(t)).items():
                sector_rows.append((sec, "etf", v * w))
        else:
            c = canon[t]
            rows.append((c, DIRECT, v))
            sector_rows.append((company_sector.get(c) or company_sector.get(t) or UNCLASSIFIED, "direct", v))
            country_rows.append((company_country.get(c) or company_country.get(t) or country_of(c), "direct", v))

    total = float(pos["value_eur"].sum())
    src = pd.DataFrame(rows, columns=["symbol", "source", "value_eur"])
    src = src.groupby(["symbol", "source"], as_index=False, sort=True)["value_eur"].sum()

    name_of = {}
    for s in sorted(names):                                                # nome più lungo = più descrittivo
        n = names[s] or ""
        if len(n) > len(name_of.get(canon[s], "")):
            name_of[canon[s]] = n

    comp_rows = []
    for sym, g in src.groupby("symbol", sort=True):
        direct = float(g.loc[g["source"] == DIRECT, "value_eur"].sum())
        via = float(g.loc[g["source"] != DIRECT, "value_eur"].sum())
        g = g.assign(_d=g["source"] != DIRECT).sort_values(["_d", "value_eur"], ascending=[True, False])
        comp_rows.append({"symbol": sym, "name": name_of.get(sym, ""), "company": name_key(name_of.get(sym)) or sym,
                          "sector": company_sector.get(sym) or UNCLASSIFIED,
                          "country": company_country.get(sym) or country_of(sym),
                          "direct_eur": direct, "etf_eur": via, "total_eur": direct + via,
                          "sources": " · ".join(f"{s} {v:,.0f} €".replace(",", ".")
                                                for s, v in zip(g["source"], g["value_eur"]))})
    comp_cols = ["symbol", "name", "company", "sector", "country", "direct_eur", "etf_eur", "total_eur", "weight",
                 "sources"]
    companies = pd.DataFrame(comp_rows, columns=[c for c in comp_cols if c != "weight"])
    companies["weight"] = companies["total_eur"] / total if total > 0 else np.nan
    companies = companies[comp_cols].sort_values(["total_eur", "symbol"], ascending=[False, True]).reset_index(drop=True)

    by_company = (companies.groupby("company", as_index=False, sort=True)
                  .agg(name=("name", "first"), symbols=("symbol", lambda s: ", ".join(sorted(s))),
                       sector=("sector", "first"), direct_eur=("direct_eur", "sum"), etf_eur=("etf_eur", "sum"),
                       total_eur=("total_eur", "sum")))
    by_company["weight"] = by_company["total_eur"] / total if total > 0 else np.nan
    by_company = by_company.sort_values(["total_eur", "company"], ascending=[False, True]).reset_index(drop=True)

    def _table(rows_, label):
        df = pd.DataFrame(rows_, columns=[label, "kind", "value_eur"])
        out = df.pivot_table(index=label, columns="kind", values="value_eur", aggfunc="sum", fill_value=0.0)
        out = out.reindex(columns=["direct", "etf"], fill_value=0.0).rename(columns={"direct": "direct_eur",
                                                                                   "etf": "etf_eur"})
        out["total_eur"] = out["direct_eur"] + out["etf_eur"]
        out["weight"] = out["total_eur"] / total if total > 0 else np.nan
        out = out[out["total_eur"].abs() > 1e-9].reset_index()
        out.columns.name = None
        return out.sort_values(["total_eur", label], ascending=[False, True]).reset_index(drop=True)

    funds_df = pd.DataFrame(funds, columns=["etf", "value_eur", "detailed_pct", "detailed_eur", "undetailed_eur",
                                            "n_holdings", "has_sectors"])
    direct_eur = float(src.loc[src["source"] == DIRECT, "value_eur"].sum())
    etf_eur = float(funds_df["value_eur"].sum())
    undetailed = float(funds_df["undetailed_eur"].sum())
    summary = {"total_eur": total, "direct_eur": direct_eur, "etf_eur": etf_eur,
               "detailed_eur": total - undetailed, "undetailed_eur": undetailed,
               "undetailed_pct": undetailed / total if total > 0 else np.nan, "missing_value": missing}
    return {"companies": companies, "by_company": by_company, "sources": src,
            "sectors": _table(sector_rows, "sector"), "countries": _table(country_rows, "country"),
            "funds": funds_df, "summary": summary, "possible_duplicates": doubtful}


def _holdings_of(x) -> pd.DataFrame:
    if isinstance(x, EtfComposition):
        return x.holdings
    if isinstance(x, dict):
        return pd.DataFrame({"symbol": list(x), "name": "", "weight": [float(v) for v in x.values()]})
    return x


def overlap(a, b, aliases: dict[str, str] | None = None) -> dict:
    """Sovrapposizione tra due ETF = somma dei minimi dei pesi sulle posizioni note (stessa azienda, simboli
    normalizzati). `a`/`b`: `EtfComposition`, tabella symbol/name/weight o dict simbolo -> peso.

    Con le sole prime 10 posizioni è un LIMITE INFERIORE (`lower_bound`): le aziende fuori dalle prime 10 non si
    vedono. `upper_bound` = noto + min(peso di A fuori dalle comuni, peso di B fuori dalle comuni): vale sempre,
    perché sulle altre aziende la sovrapposizione non può superare il peso che resta a ciascuno dei due fondi."""
    ha, hb = _holdings_of(a), _holdings_of(b)
    names = {}
    for h in (ha, hb):
        for x in h.itertuples(index=False):
            names[x.symbol] = names.get(x.symbol) or (x.name or None)
    canon, _ = canonical_map(names, aliases)
    wa = ha.assign(symbol=ha["symbol"].map(canon)).groupby("symbol")["weight"].sum()
    wb = hb.assign(symbol=hb["symbol"].map(canon)).groupby("symbol")["weight"].sum()
    common = sorted(set(wa.index) & set(wb.index))
    name_of = {canon[s]: n for s, n in sorted(names.items()) if n}
    tab = pd.DataFrame({"symbol": common, "name": [name_of.get(s, "") for s in common],
                        "weight_a": [float(wa[s]) for s in common], "weight_b": [float(wb[s]) for s in common]})
    tab["overlap"] = np.minimum(tab["weight_a"], tab["weight_b"])
    tab = tab.sort_values(["overlap", "symbol"], ascending=[False, True]).reset_index(drop=True)
    known = float(tab["overlap"].sum())
    cov_a, cov_b = float(wa.sum()), float(wb.sum())
    complete = cov_a >= 0.995 and cov_b >= 0.995
    upper = min(1.0, known + min(max(0.0, 1 - float(tab["weight_a"].sum())), max(0.0, 1 - float(tab["weight_b"].sum()))))
    note = ("composizioni complete: valore esatto" if complete else
            f"limite inferiore: note solo {cov_a:.0%} di A e {cov_b:.0%} di B (prime posizioni); "
            f"la sovrapposizione vera sta tra {known:.0%} e {upper:.0%}")
    return {"overlap": known, "upper_bound": upper if not complete else known, "lower_bound": not complete,
            "coverage_a": cov_a, "coverage_b": cov_b, "common": tab, "note": note}


def sector_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """Somma dei minimi dei pesi per settore: misura grossolana ma su tutto il fondo (Yahoo dà i settori interi).
    Due ETF con settori identici possono avere aziende diverse: è un'indicazione, non la sovrapposizione."""
    return float(sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in sorted(set(a) | set(b))))


def concentration(exposure, n: int = TOP_N, value_col: str = "total_eur", total: float | None = None) -> dict:
    """Primi `n` nomi, peso del primo e dei primi `n`, indice di Herfindahl (somma dei pesi al quadrato) e numero
    equivalente di aziende (1 / Herfindahl).

    `exposure`: tabella `companies` o `by_company` di `look_through` (o una serie nome -> euro). `total`: valore
    dell'intero portafoglio (default: la somma delle righe). Se il portafoglio contiene una parte non dettagliata,
    passare il totale vero: i pesi sono allora sul totale e l'Herfindahl calcolato sui soli nomi noti è un LIMITE
    INFERIORE (il resto, sparso su molte aziende, aggiunge qualcosa) e il numero equivalente un limite superiore."""
    if isinstance(exposure, pd.Series):
        df = exposure.rename(value_col).rename_axis("symbol").reset_index()
    else:
        df = exposure.copy()
    df = df[df[value_col] > 0].sort_values(value_col, ascending=False, kind="mergesort").reset_index(drop=True)
    known = float(df[value_col].sum())
    tot = float(total) if total else known
    if tot <= 0 or df.empty:
        return {"top": df.head(0), "top_weight": np.nan, "top_n_weight": np.nan, "hhi": np.nan, "effective_n": np.nan,
                "known_share": np.nan, "partial": False}
    w = df[value_col] / tot
    df["weight"] = w
    hhi = float((w ** 2).sum())
    return {"top": df.head(n), "top_weight": float(w.iloc[0]), "top_n_weight": float(w.head(n).sum()),
            "hhi": hhi, "effective_n": 1.0 / hhi if hhi > 0 else np.nan, "known_share": known / tot,
            "partial": known < tot * 0.995}
