"""
Notizie in tempo (quasi) reale da feed RSS/Atom pubblici e gratuiti: solo titolo, fonte, ora e link all'articolo
originale (mai il testo). Non è un flusso professionale: tra l'evento e il titolo nel feed passano di solito da 1 a
15 minuti (comunicati stampa e banche centrali i più rapidi, Google News e i quotidiani italiani i più lenti).

- `parse_feed`: RSS 2.0 / Atom / RDF con la libreria standard, date in UTC, HTML tolto dai titoli; per Google News
  la testata vera (tag <source> o " - Testata" in fondo al titolo).
- `TickerMatcher`: collega un titolo ai ticker dell'universo in modo prudente (nome dell'azienda con maiuscola,
  "(NYSE: XYZ)", "$XYZ", ticker in maiuscolo di almeno 3 lettere che non sia una parola comune, alias in config).
- `NewsStore`: notizie viste, deduplicate (stesso link o titolo quasi uguale da fonti diverse), salvate in un file
  JSON sotto data/cache/news/ con scadenza in giorni: al riavvio la giornata resta.
- `NewsFeed`: scarica i feed in un thread (come `quotes.QuoteFeed`): feed generali ogni `every_seconds`, feed per
  azienda (le mie aziende e il portafoglio) più di rado; ETag/If-Modified-Since, pausa tra le richieste, un feed
  che fallisce non ferma gli altri e si riprova con attesa crescente. `snapshot()` -> tabella delle notizie.
- `latest(table, tickers, limit)`: le ultime notizie (anche di alcune aziende) per pannelli di Monitor e scheda.

Configurazione: sezione `news:` di config.yaml (default in modules/config.py).
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import pandas as pd

logger = logging.getLogger(__name__)

USER_AGENT = "QUANT_ENGINE (uso personale)"
COLUMNS = ["published", "first_seen", "source", "feed", "group", "title", "link", "tickers", "lang"]

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
RSS1 = "{http://purl.org/rss/1.0/}"


# ---------------------------------------------------------------- lettura dei feed
def clean_text(text: str | None) -> str:
    """Titolo leggibile: entità HTML decodificate (anche doppie), tag tolti, spazi compattati."""
    if not text:
        return ""
    s = html.unescape(html.unescape(str(text)))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(text: str | None) -> datetime | None:
    """Data di un feed in UTC: RFC 822 (anche "UT"/"GMT"/"+0100") o ISO 8601; senza fuso = UTC."""
    if not text:
        return None
    text = text.strip()
    d = None
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)", text)
            if m:
                try:
                    d = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
                except ValueError:
                    d = None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


_TRACKING = re.compile(r"^(utm_|\.tsrc$|ncid$|cmpid$|taid$|mod$|ref$|rss$|feed$|src$)")


def canonical_link(url: str) -> str:
    """Link per riconoscere lo stesso articolo: senza parametri di tracciamento, frammento e "/" finale."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    host = p.netloc.lower().removeprefix("www.")
    return urlunsplit(("https", host, p.path.rstrip("/"), query, ""))


def _text(el, *tags) -> str:
    for tag in tags:
        found = el.find(tag)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _atom_link(entry) -> str:
    links = entry.findall(f"{ATOM}link")
    for ln in links:
        if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
            return ln.get("href")
    return links[0].get("href", "") if links else ""


def _strip_publisher(title: str, publisher: str) -> str:
    """Google News aggiunge " - Testata" in fondo al titolo."""
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3].strip()
    return title


def parse_feed(content: bytes | str) -> tuple[dict, list[dict]]:
    """Legge un feed RSS 2.0, Atom o RSS 1.0 (RDF). Restituisce (canale, voci); ogni voce: title, link,
    published (datetime UTC o None), publisher (testata, se il feed la indica). Il testo non si tiene."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    root = ET.fromstring(content.lstrip())
    channel = {"title": "", "language": ""}
    items: list[dict] = []
    if root.tag == f"{ATOM}feed":
        channel["title"] = clean_text(_text(root, f"{ATOM}title"))
        channel["language"] = root.get("{http://www.w3.org/XML/1998/namespace}lang", "")
        for e in root.findall(f"{ATOM}entry"):
            items.append({"title": clean_text(_text(e, f"{ATOM}title")), "link": _atom_link(e),
                          "published": parse_date(_text(e, f"{ATOM}published", f"{ATOM}updated")),
                          "publisher": clean_text(_text(e, f"{ATOM}source/{ATOM}title", f"{ATOM}author/{ATOM}name"))})
    else:
        ch = root.find("channel")
        if ch is None:
            ch = root.find(f"{RSS1}channel")
        if ch is not None:
            channel["title"] = clean_text(_text(ch, "title", f"{RSS1}title"))
            channel["language"] = _text(ch, "language", f"{DC}language")
        for it in root.iter():
            if it.tag not in ("item", f"{RSS1}item"):
                continue
            link = _text(it, "link", f"{RSS1}link")
            if not link:
                guid = it.find("guid")
                if guid is not None and guid.get("isPermaLink", "true") != "false":
                    link = (guid.text or "").strip()
            publisher = clean_text(_text(it, "source"))
            items.append({"title": _strip_publisher(clean_text(_text(it, "title", f"{RSS1}title")), publisher),
                          "link": link, "publisher": publisher,
                          "published": parse_date(_text(it, "pubDate", f"{DC}date", f"{ATOM}updated"))})
    return channel, [i for i in items if i["title"] and i["link"]]


# ---------------------------------------------------------------- collegamento titolo -> ticker
NAME_SUFFIXES = re.compile(
    r"[\s,]+(inc\.?|incorporated|corp\.?|corporation|co\.?|company|companies|ltd\.?|limited|plc|p\.l\.c\.|holdings?|group|"
    r"s\.?p\.?a\.?|n\.?v\.?|s\.?a\.?|s\.?e\.?|se|ag|a/s|asa|oyj|ab|the|class [a-c]|\(the\)|\.com)$", re.IGNORECASE)
# nomi (o loro prime parole) che sono anche parole comuni: da soli non bastano a collegare un titolo
COMMON_NAMES = {
    "Target", "Ball", "Block", "Match", "Progressive", "Southern", "Public", "Republic", "General", "Global",
    "American", "United", "First", "National", "International", "Western", "Eastern", "Northern", "Digital",
    "Energy", "Health", "Universal", "Advanced", "Applied", "Best", "Dollar", "Booking", "Boston", "Charles", "Church",
    "Consolidated", "Federal", "Fifth", "Franklin", "Hartford", "Live", "Marathon", "Old", "Pool", "Principal",
    "Regions", "Snap", "State", "Texas", "Trade", "Union", "Waste", "West", "Willis", "Zebra", "Fox", "News",
    "Apollo", "Arch", "Ares", "Cardinal", "Carrier", "Crown", "Edison", "Equity", "Essex", "Fair", "Fortune", "Gap",
    "Intercontinental", "Interactive", "Lincoln", "Monster", "Pacific", "Pioneer", "Quest", "Ralph", "Royal", "Simon",
    "Stanley", "Steel", "Super", "Warner", "Bank", "Air", "Delta", "Prudential", "Mid", "Expand", "Builders", "Coca",
    "Poste", "Banca", "Banco", "Deutsche", "Credit", "Societe", "Dominion", "Realty", "Mosaic",
    "Capital", "Williams", "Host", "Jack", "Robert", "Henry", "Lamb", "Ross", "Martin", "Cooper", "Baker", "Philip",
    "Eli", "Carnival", "Genuine", "Mohawk", "Vulcan", "Monolithic", "Palo", "Charter", "Electronic", "Everest", "Globe",
    "Travelers", "Discover", "Wells", "Raymond", "Nasdaq", "Fidelity", "Huntington", "Citizens", "Cincinnati", "Marsh",
    "Brown", "Arthur", "Erie", "Camden", "Regency", "Las", "Home", "Edwards", "Morgan", "Norfolk", "Phillips", "Tyler",
    "Dover", "Avery", "Devon", "Duke", "Packaging", "Sealed", "Dow", "Illinois", "Automatic", "Intuitive", "Waters",
    "Vertex", "Alexandria", "Extra", "Iron", "Invitation", "Norwegian", "Southwest", "Alaska", "Tractor", "Bath",
    "Davide", "Schneider", "Munich", "Emerson", "Constellation", "Johnson", "Hilton",
}
# sigle in maiuscolo che sono anche ticker: in un titolo quasi sempre vogliono dire altro
COMMON_UPPER = {
    "ALL", "NOW", "KEY", "CAT", "DOW", "MET", "LOW", "HAS", "ARE", "BALL", "FAST", "ICE", "PEG", "CEO", "CFO", "USA",
    "GDP", "CPI", "ETF", "IPO", "SEC", "FED", "ECB", "BCE", "USD", "EUR", "AI", "IT", "ON", "SO", "PM", "AM", "TV",
    "AND", "THE", "FOR", "NEW", "BIG", "CAN", "OUT", "NOT", "ONE", "TWO", "WAS", "WAY", "WHO", "WHY", "YOU", "UPS",
    "CEG", "BIO", "HUM", "DAY", "TECH", "GOOD", "LIFE", "MAIN", "PLAY", "REAL", "SAFE", "WELL", "AMP", "EVER", "FUN",
    "NWS", "COST", "TRV", "LUV", "J", "K", "OKE", "PAYC", "SNA", "AIZ", "ESS", "RE", "EG", "LIN", "ROL", "WAT", "WDC",
    "OTIS", "EBAY", "FOX", "IRM", "TAP", "VTR", "BXP", "HST", "FE", "ED", "DG", "DE", "GE", "GM", "HD", "MS", "MO",
    "NI", "PG", "PH", "SPG", "TT", "UNP", "ZION",
}
_EXCHANGE_TAG = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NYSE American|NYSE Arca|AMEX|Cboe|BATS|OTC|TSX|LSE)\s*:\s*"
                           r"([A-Z][A-Z.\-]{0,6})\)")
_CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
_UPPER_WORD = re.compile(r"(?<![A-Za-z0-9$.\-])([A-Z]{3,5})(?![A-Za-z0-9])")


def short_name(name: str) -> str:
    """Nome come compare nei titoli: senza forma societaria, classe di azioni, parentesi ("Apple Inc." -> "Apple")."""
    n = re.sub(r"\(.*?\)", " ", str(name or ""))
    n = re.sub(r"\s+", " ", n.replace("&amp;", "&")).strip(" ,.")
    if n.lower().startswith("the "):
        n = n[4:]
    prev = None
    while prev != n:
        prev, n = n, NAME_SUFFIXES.sub("", n).strip(" ,.")
    return n


class TickerMatcher:
    """Trova i ticker di un universo in un titolo. Prudente: meglio perdere un collegamento che sbagliarlo."""

    def __init__(self, names: dict[str, str] | None = None, aliases: dict[str, Iterable[str]] | None = None):
        self.tickers: set[str] = set()
        self._by_name: dict[str, list[str]] = {}
        self._regex: re.Pattern | None = None
        self.set_universe(names or {}, aliases or {})

    def set_universe(self, names: dict[str, str], aliases: dict[str, Iterable[str]] | None = None) -> None:
        by_name: dict[str, set[str]] = {}

        def add(label: str, ticker: str):
            label = label.strip()
            if len(label) >= 3 and label not in COMMON_NAMES and label[0].isupper():
                by_name.setdefault(label, set()).add(ticker)

        firsts: dict[str, set[str]] = {}
        for ticker, name in names.items():
            sn = short_name(name)
            if not sn:
                continue
            add(sn, ticker)
            words = sn.split()
            # prima parola da sola ("Meta Platforms" -> "Meta"), se lunga, non comune e di una sola azienda
            if len(words) >= 2 and len(words[0]) >= 4 and words[0].isalpha():
                firsts.setdefault(words[0], set()).add(short_name(name))
        for word, owners in firsts.items():
            if len(owners) == 1 and word not in by_name:
                owner = next(iter(owners))
                for ticker, name in names.items():
                    if short_name(name) == owner:
                        add(word, ticker)
        for ticker, labels in (aliases or {}).items():
            if ticker in names:  # alias di aziende fuori dall'universo: ignorati
                for label in labels or []:
                    by_name.setdefault(str(label), set()).add(ticker)
        self.tickers = set(names)
        # azioni di più classi con lo stesso nome (GOOG/GOOGL): un solo ticker, il primo in ordine alfabetico
        self._by_name = {k: sorted(v)[:1] if len(v) > 1 and len({short_name(names.get(t, t)) for t in v}) == 1
                         else sorted(v) for k, v in by_name.items()}
        labels = sorted(self._by_name, key=len, reverse=True)
        self._regex = (re.compile(r"(?<![\w&])(" + "|".join(re.escape(x) for x in labels) + r")(?:'s|’s)?(?![\w&])")
                       if labels else None)

    def match(self, title: str) -> list[str]:
        found: list[str] = []

        def add(t):
            if t in self.tickers and t not in found:
                found.append(t)

        for m in _EXCHANGE_TAG.finditer(title):
            add(m.group(1).replace(".", "-"))
        for m in _CASHTAG.finditer(title):
            add(m.group(1).replace(".", "-"))
        if self._regex is not None:
            for m in self._regex.finditer(title):
                for t in self._by_name.get(m.group(1), []):
                    add(t)
        letters = [c for c in title if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) < 0.5:  # titoli tutti in maiuscolo: no
            for m in _UPPER_WORD.finditer(title):
                if m.group(1) not in COMMON_UPPER:
                    add(m.group(1))
        return found


# ---------------------------------------------------------------- memoria delle notizie
def _norm_title(title: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]+", " ", title.lower()).strip()


@dataclass
class NewsStore:
    """Notizie viste, una per articolo. `add` restituisce quelle nuove; duplicati = stesso link canonico o titolo
    quasi uguale (somiglianza >= `similarity`) pubblicato entro `dup_hours` ore: i ticker si uniscono."""

    path: Path | None = None
    keep_days: float = 3.0
    similarity: float = 0.92
    dup_hours: float = 36.0
    max_items: int = 6000
    items: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self._links: dict[str, dict] = {}
        self._titles: dict[str, dict] = {}
        if self.path is not None:
            self.load()

    def _index(self, it: dict) -> None:
        self._links[canonical_link(it["link"])] = it
        self._titles[_norm_title(it["title"])] = it

    def load(self) -> None:
        try:
            raw = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for it in raw.get("items", []):
            try:
                it["published"] = datetime.fromisoformat(it["published"])
                it["first_seen"] = datetime.fromisoformat(it["first_seen"])
            except (KeyError, TypeError, ValueError):
                continue
            self.items.append(it)
        self.prune()

    def save(self) -> None:
        if self.path is None:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{**it, "published": it["published"].isoformat(), "first_seen": it["first_seen"].isoformat()}
                for it in self.items]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": rows}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def prune(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        limit = now - timedelta(days=self.keep_days)
        self.items = sorted((it for it in self.items if it["published"] >= limit),
                            key=lambda it: it["published"], reverse=True)[: self.max_items]
        self._links, self._titles = {}, {}
        for it in reversed(self.items):
            self._index(it)

    def _duplicate(self, it: dict) -> dict | None:
        same = self._links.get(canonical_link(it["link"])) or self._titles.get(_norm_title(it["title"]))
        if same is not None:
            return same
        norm = _norm_title(it["title"])
        if len(norm) < 25:  # titoli brevi ("Market update"): solo uguaglianza esatta
            return None
        window = timedelta(hours=self.dup_hours)
        for other_norm, other in self._titles.items():
            if abs(other["published"] - it["published"]) > window:
                continue
            sm = SequenceMatcher(None, norm, other_norm, autojunk=False)
            if sm.real_quick_ratio() >= self.similarity and sm.quick_ratio() >= self.similarity \
                    and sm.ratio() >= self.similarity:
                return other
        return None

    def add(self, new_items: Iterable[dict], now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        limit = now - timedelta(days=self.keep_days)
        added = []
        for it in new_items:
            if it["published"] < limit:
                continue
            dup = self._duplicate(it)
            if dup is not None:
                dup["tickers"] = sorted(set(dup["tickers"]) | set(it["tickers"]))
                continue
            self.items.append(it)
            self._index(it)
            added.append(it)
        if added:
            self.items.sort(key=lambda x: x["published"], reverse=True)
            if len(self.items) > self.max_items:
                self.prune(now)
        return added

    def frame(self) -> pd.DataFrame:
        if not self.items:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame([{c: it.get(c) for c in COLUMNS} for it in self.items], columns=COLUMNS)
        df["published"] = pd.to_datetime(df["published"], utc=True)
        df["first_seen"] = pd.to_datetime(df["first_seen"], utc=True)
        df["tickers"] = [" ".join(t or []) for t in df["tickers"]]
        return df.sort_values("published", ascending=False, kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- feed e thread
@dataclass
class FeedSpec:
    name: str
    url: str
    lang: str = ""
    group: str = "mercati"
    every: float = 120.0
    tickers: tuple[str, ...] = ()   # feed di un'azienda: ticker noti


@dataclass
class FeedState:
    spec: FeedSpec
    etag: str | None = None
    modified: str | None = None
    next_due: float = 0.0
    failures: int = 0
    error: str | None = None
    last_ok: datetime | None = None
    last_items: int = 0


def http_fetch(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    import requests

    r = requests.get(url, headers=headers, timeout=timeout)
    return r.status_code, dict(r.headers), r.content


def general_feeds(cfg_news: dict) -> list[FeedSpec]:
    every = float(cfg_news.get("every_seconds", 120))
    out = []
    for f in cfg_news.get("feeds") or []:
        if not f.get("url") or f.get("enabled", True) is False:
            continue
        out.append(FeedSpec(name=str(f.get("name") or f["url"]), url=str(f["url"]), lang=str(f.get("lang") or ""),
                            group=str(f.get("group") or "mercati"), every=float(f.get("every") or every)))
    return out


def company_feeds(cfg_news: dict, companies: dict[str, str]) -> list[FeedSpec]:
    """Feed per azienda: Yahoo Finance per simbolo; per i simboli con suffisso di borsa (ENI.MI, MC.PA) anche
    Google News con il nome (il feed Yahoo dei titoli europei resta spesso fermo per giorni)."""
    c = cfg_news.get("company") or {}
    every = float(c.get("every_seconds", 900))
    out = []
    for ticker, name in sorted(companies.items()):
        symbol = ticker  # Yahoo usa lo stesso simbolo del database (BRK-B, ENI.MI)
        foreign = "." in ticker
        if c.get("yahoo_url"):
            out.append(FeedSpec(name="Yahoo Finance", url=c["yahoo_url"].format(symbol=quote_plus(symbol)),
                                lang="en", group="aziende", every=every, tickers=(ticker,)))
        if c.get("google_url") and name and (foreign or c.get("google_for_us", False)):
            sn = short_name(name) or name
            lang = str(c.get("google_lang_foreign", "it")) if foreign else "en"
            tpl = c["google_url"] if foreign else c.get("google_url_us", c["google_url"])
            out.append(FeedSpec(name="Google News", url=tpl.format(query=quote_plus(f'"{sn}"')), lang=lang,
                                group="aziende", every=every, tickers=(ticker,)))
    return out


class NewsFeed:
    """Feed RSS letti in un thread. `snapshot()` -> (tabella delle notizie, ora dell'ultimo giro, stato dei feed)."""

    def __init__(self, feeds: list[FeedSpec], store: NewsStore | None = None, matcher: TickerMatcher | None = None,
                 pause: float = 1.0, timeout: float = 15.0, max_backoff: float = 3600.0,
                 fetch: Callable[[str, dict, float], tuple[int, dict, bytes]] = http_fetch,
                 user_agent: str = USER_AGENT, clock: Callable[[], float] = time.monotonic):
        self.store = store or NewsStore()
        self.matcher = matcher or TickerMatcher()
        self.pause, self.timeout, self.max_backoff = pause, timeout, max_backoff
        self.fetch, self.user_agent, self.clock = fetch, user_agent, clock
        self._states: dict[tuple[str, tuple], FeedState] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._updated: datetime | None = None
        self._universe_key = None
        self.set_feeds(feeds)

    # -- configurazione (si può cambiare mentre il thread gira)
    def set_feeds(self, feeds: list[FeedSpec]) -> None:
        with self._lock:
            old = self._states
            self._states = {}
            for spec in feeds:
                key = (spec.url, spec.tickers)
                st = old.get(key)
                if st is not None:
                    st.spec = spec
                self._states[key] = st or FeedState(spec)
        self._wake.set()

    def add_feeds(self, feeds: list[FeedSpec], kind: str) -> None:
        """Sostituisce solo i feed del gruppo `kind` (es. "aziende"), tiene gli altri."""
        with self._lock:
            keep = [s.spec for s in self._states.values() if s.spec.group != kind]
        self.set_feeds(keep + [f for f in feeds if f.group == kind])

    def set_universe(self, names: dict[str, str], aliases: dict | None = None) -> None:
        """Universo per collegare i titoli ai ticker; le notizie già in memoria si ricollegano."""
        key = (tuple(sorted(names.items())), json.dumps(aliases or {}, sort_keys=True, default=str))
        if key == self._universe_key:
            return
        with self._lock:
            self._universe_key = key
            self.matcher.set_universe(names, aliases)
            for it in self.store.items:
                it["tickers"] = sorted(set(it.get("feed_tickers") or []) | set(self.matcher.match(it["title"])))

    # -- un feed
    def _headers(self, st: FeedState) -> dict:
        h = {"User-Agent": self.user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, "
             "text/xml;q=0.9, */*;q=0.5", "Accept-Encoding": "gzip, deflate"}
        if st.etag:
            h["If-None-Match"] = st.etag
        if st.modified:
            h["If-Modified-Since"] = st.modified
        return h

    def _fail(self, st: FeedState, error: str, now: float) -> None:
        st.failures += 1
        st.error = error
        st.next_due = now + min(self.max_backoff, st.spec.every * 2 ** st.failures)
        logger.info("Feed %s non letto (%s): riprovo tra %.0f s", st.spec.name, error, st.next_due - now)

    def _read(self, st: FeedState, now_utc: datetime) -> list[dict]:
        now = self.clock()
        try:
            status, headers, content = self.fetch(st.spec.url, self._headers(st), self.timeout)
        except Exception as exc:  # noqa: BLE001 - rete, timeout, DNS: il feed si riprova più tardi
            self._fail(st, f"{type(exc).__name__}: {str(exc)[:80]}", now)
            return []
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if status == 304:
            st.failures, st.error, st.last_ok = 0, None, now_utc
            st.next_due = now + st.spec.every
            return []
        if status != 200:
            self._fail(st, f"HTTP {status}", now)
            return []
        try:
            channel, entries = parse_feed(content)
        except ET.ParseError as exc:
            self._fail(st, f"XML non valido: {str(exc)[:60]}", now)
            return []
        st.etag, st.modified = headers.get("etag"), headers.get("last-modified")
        st.failures, st.error, st.last_ok, st.last_items = 0, None, now_utc, len(entries)
        st.next_due = now + st.spec.every
        lang = st.spec.lang or (channel.get("language") or "")[:2].lower()
        out = []
        for e in entries:
            published = e["published"] or now_utc
            if published > now_utc + timedelta(minutes=5):  # fuso sbagliato nel feed: vale l'ora di arrivo
                published = now_utc
            feed_tickers = list(st.spec.tickers)
            out.append({"published": published, "first_seen": now_utc, "title": e["title"], "link": e["link"],
                        "source": e["publisher"] or st.spec.name, "feed": st.spec.name, "group": st.spec.group,
                        "lang": lang, "feed_tickers": feed_tickers,
                        "tickers": sorted(set(feed_tickers) | set(self.matcher.match(e["title"])))})
        return out

    def refresh(self, force: bool = False) -> int:
        """Un giro sui feed scaduti (tutti con `force`); restituisce quante notizie nuove sono arrivate."""
        with self._lock:
            due = [s for s in self._states.values() if force or s.next_due <= self.clock()]
        due.sort(key=lambda s: (s.spec.group == "aziende", s.next_due))  # prima i generali
        added = 0
        for i, st in enumerate(due):
            if i and self.pause:
                time.sleep(self.pause)
            now_utc = datetime.now(timezone.utc)
            items = self._read(st, now_utc)
            with self._lock:
                if items:
                    added += len(self.store.add(items, now_utc))
                self._updated = datetime.now()
        if due:
            with self._lock:
                self.store.prune()
                if added:
                    try:
                        self.store.save()
                    except OSError as exc:
                        logger.warning("Notizie non salvate: %s", exc)
        return added

    def _loop(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - il thread non deve morire
                logger.exception("Giro dei feed di notizie fallito")
            with self._lock:
                wait = min((s.next_due for s in self._states.values()), default=self.clock() + 60) - self.clock()
            self._wake.wait(timeout=max(1.0, min(wait, 60.0)))
            self._wake.clear()

    def start(self) -> "NewsFeed":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="news-feed", daemon=True)
            self._thread.start()
        return self

    # -- lettura
    def status(self) -> pd.DataFrame:
        with self._lock:
            rows = [{"feed": s.spec.name, "group": s.spec.group, "tickers": " ".join(s.spec.tickers),
                     "url": s.spec.url, "ok": s.error is None and s.last_ok is not None, "error": s.error,
                     "failures": s.failures, "last_ok": s.last_ok, "items": s.last_items}
                    for s in self._states.values()]
        return pd.DataFrame(rows, columns=["feed", "group", "tickers", "url", "ok", "error", "failures", "last_ok",
                                           "items"])

    def snapshot(self) -> tuple[pd.DataFrame, datetime | None, pd.DataFrame]:
        with self._lock:
            return self.store.frame(), self._updated, self.status()


def latest(table: pd.DataFrame, tickers: Iterable[str] | str | None = None, limit: int = 10,
           since: datetime | pd.Timestamp | None = None) -> pd.DataFrame:
    """Le ultime notizie della tabella di `NewsFeed.snapshot()`, eventualmente solo di alcuni ticker o dopo `since`."""
    if table is None or table.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = table
    if tickers is not None:
        wanted = {tickers} if isinstance(tickers, str) else set(tickers)
        out = out[[bool(wanted & set(str(t).split())) for t in out["tickers"]]]
    if since is not None:
        since = pd.Timestamp(since)
        out = out[out["published"] >= (since.tz_localize("UTC") if since.tzinfo is None else since)]
    return out.sort_values("published", ascending=False, kind="stable").head(limit).reset_index(drop=True)


GROUPS = ["mercati", "comunicati", "banche centrali", "italia", "aziende"]


def filter_table(table: pd.DataFrame, tickers: Iterable[str] | None = None, groups: Iterable[str] | None = None,
                 lang: str | None = None, query: str | None = None) -> pd.DataFrame:
    """Filtri della pagina Notizie: alcuni ticker (elenco vuoto = nessuna notizia), gruppi di fonti, lingua, testo nel
    titolo (senza maiuscole). Ordine e colonne restano quelli della tabella."""
    if table is None or table.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = table
    if tickers is not None:
        wanted = set(tickers)
        out = out[[bool(wanted & set(str(t).split())) for t in out["tickers"]]]
    if groups:
        out = out[out["group"].isin(list(groups))]
    if lang:
        out = out[out["lang"] == lang]
    if query and query.strip():
        out = out[out["title"].str.contains(query.strip(), case=False, regex=False)]
    return out


def store_path(cfg_news: dict, project_root: Path | str) -> Path:
    """File della memoria delle notizie (`news.cache_dir`, relativa al progetto)."""
    cache = Path((cfg_news or {}).get("cache_dir", "data/cache/news"))
    return (cache if cache.is_absolute() else Path(project_root) / cache) / "notizie.json"


def saved_table(cfg_news: dict, project_root: Path | str) -> tuple[pd.DataFrame, datetime | None]:
    """Notizie dalla copia salvata dal thread dei feed (niente rete) e ora dell'ultima scrittura (None se non c'è)."""
    path = store_path(cfg_news, project_root)
    try:
        saved = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return pd.DataFrame(columns=COLUMNS), None
    return NewsStore(path, keep_days=float((cfg_news or {}).get("keep_days", 3))).frame(), saved


def build_feed(cfg: dict, project_root: Path | str, start: bool = True, **kwargs) -> NewsFeed:
    """NewsFeed dalla configurazione (sezione news); memoria in `news.cache_dir` (relativa al progetto)."""
    n = cfg.get("news") or {}
    store = NewsStore(store_path(n, project_root), keep_days=float(n.get("keep_days", 3)),
                      similarity=float(n.get("similarity", 0.92)))
    feed = NewsFeed(general_feeds(n), store, pause=float(n.get("pause_seconds", 1.0)),
                    timeout=float(n.get("timeout_seconds", 15)), max_backoff=float(n.get("max_backoff_seconds", 3600)),
                    **kwargs)
    return feed.start() if start else feed


# ---------------------------------------------------------------- account X (solo riquadro ufficiale, niente API)
_X_USER = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def x_username(text: str) -> str | None:
    """Nome utente X valido (lettere, cifre, underscore, max 15) da "@nome", "nome" o un link al profilo; None se
    non valido."""
    s = str(text or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)", s, re.IGNORECASE)
    if m:
        s = m.group(1)
    s = s.lstrip("@")
    return s if _X_USER.match(s) else None


def load_x_accounts(path: Path | str, defaults: Iterable[str] = ()) -> list[str]:
    """Account X scelti dall'utente (file JSON); se il file non c'è, l'elenco iniziale di config.yaml."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8")).get("accounts", [])
    except (OSError, ValueError, AttributeError):
        raw = list(defaults or [])
    out: list[str] = []
    for a in raw:
        u = x_username(a)
        if u and u.lower() not in {x.lower() for x in out}:
            out.append(u)
    return out


def save_x_accounts(path: Path | str, accounts: Iterable[str]) -> list[str]:
    clean: list[str] = []
    for a in accounts:
        u = x_username(a)
        if u and u.lower() not in {x.lower() for x in clean}:
            clean.append(u)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": clean}, ensure_ascii=False), encoding="utf-8")
    return clean


def x_timeline_html(user: str, dark: bool = True, height: int = 600) -> str:
    """Riquadro ufficiale della timeline di un account (widget di incorporamento di X). Se X non lo carica resta
    visibile il link al profilo."""
    u = x_username(user)
    if u is None:
        raise ValueError(f"nome utente X non valido: {user!r}")
    theme = "dark" if dark else "light"
    bg, fg = ("#1c1c1e", "#0a84ff") if dark else ("#ffffff", "#0071e3")
    return (f'<html><body style="margin:0;background:{bg};font:14px -apple-system,system-ui,sans-serif">'
            f'<a class="twitter-timeline" data-theme="{theme}" data-height="{int(height)}" data-dnt="true" '
            f'data-chrome="noborders transparent" href="https://twitter.com/{u}?ref_src=twsrc%5Etfw" target="_blank" '
            f'rel="noopener" style="color:{fg}">Post di @{u} su X (se il riquadro non compare, apri il profilo)</a>'
            f'<p id="qe-x-msg" style="display:none;color:{fg}">X non ha mostrato i post: di solito succede se in '
            f'questo browser non sei collegato a X (oppure X limita le richieste). Usa il link al profilo.</p>'
            f'<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>'
            # dopo 12 s: se il riquadro di X è ancora nascosto, lo si dice in chiaro (il link resta)
            '<script>setTimeout(function(){var f=document.querySelector("iframe[id^=twitter-widget]");'
            'if(!f||f.style.visibility==="hidden"||f.offsetHeight<50){'
            'document.getElementById("qe-x-msg").style.display="block";}},12000);</script></body></html>')
