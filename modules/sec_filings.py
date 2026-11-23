"""
Nuovi 10-Q e 10-K delle aziende seguite, per scaricarne i bilanci appena depositati.

Due fonti della SEC, entrambe con una sola richiesta per giorno (non una per azienda):
- l'indice giornaliero dei depositi per tipo di documento (daily-index/form.AAAAMMGG.idx), pubblicato la
  sera: si leggono tutti i giorni dall'ultimo controllo che compaiono nell'elenco della cartella del trimestre.
  Un giorno senza indice è festivo, o non ancora pubblicato: dopo `sec.filings_index_wait_days` giorni si
  considera festivo;
- l'elenco degli ultimi depositi (browse-edgar "getcurrent"), per i documenti di oggi non ancora nell'indice.

I depositi trovati vanno in `sec_filings` come "da caricare". Dopo il download dei dati dell'azienda
diventano "caricati" se compaiono nei valori XBRL (l'API companyfacts li riporta da pochi minuti a qualche ora
dopo il deposito: altrimenti si riprova all'aggiornamento successivo).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import pandas as pd

from modules.http_client import HttpError

logger = logging.getLogger(__name__)

DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{d:%Y}/QTR{q}/form.{d:%Y%m%d}.idx"
QUARTER_LISTING_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/index.json"
CURRENT_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb="
               "&owner=include&start={start}&count=100&output=atom")
REPORT_FORMS = ("10-Q", "10-K")      # con le varianti: 10-Q/A, 10-K/A, 10-KT...
EVENT_FORM = "8-K"                   # eventi rilevanti: le "notizie ufficiali" (le rettifiche 8-K/A no)
COLUMNS = ["form", "cik", "filed", "accession"]
INDEX_KIND = "sec_daily_index_v2"    # in fetch_log: un "ticker" per giorno controllato (v2: anche gli 8-K)
HEADER_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{acc}.hdr.sgml"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{acc}-index.htm"
# voci degli 8-K (Form 8-K, General Instructions): cosa è successo
ITEM_LABELS = {
    "1.01": "Accordo rilevante firmato", "1.02": "Accordo rilevante chiuso", "1.03": "Fallimento o amministrazione",
    "1.04": "Sicurezza nelle miniere", "1.05": "Incidente di cybersicurezza",
    "2.01": "Acquisizione o cessione completata", "2.02": "Risultati del trimestre o dell'esercizio",
    "2.03": "Nuovo debito", "2.04": "Debito che diventa esigibile prima", "2.05": "Costi di ristrutturazione o uscita",
    "2.06": "Svalutazione rilevante", "3.01": "Rischio di uscita dalla Borsa", "3.02": "Vendita di azioni non registrata",
    "3.03": "Diritti degli azionisti modificati", "4.01": "Cambio del revisore",
    "4.02": "Bilanci passati non più affidabili", "5.01": "Cambio di controllo",
    "5.02": "Cambi di amministratori o dirigenti", "5.03": "Statuto o esercizio modificati",
    "5.04": "Sospensione dei piani pensione", "5.05": "Codice etico modificato", "5.06": "Fine dello stato di shell company",
    "5.07": "Voto degli azionisti", "5.08": "Candidature al consiglio", "6.01": "ABS", "7.01": "Comunicazione al mercato",
    "8.01": "Altri eventi", "9.01": "Allegati (comunicati, bilanci)",
}
# eventi da guardare subito (in rosso nella pagina Notizie)
IMPORTANT_ITEMS = {"1.03", "1.05", "2.04", "2.06", "3.01", "4.01", "4.02", "5.01"}
NY = "America/New_York"

_INDEX_LINE = re.compile(r"^(?P<form>(?:10-[QK]|8-K)\S*)\s+.+?\s+(?P<cik>\d+)\s+(?P<filed>\d{8})\s+\S*?"
                         r"(?P<acc>\d{10}-\d{2}-\d{6})\.txt\s*$")
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def parse_form_index(text: str) -> pd.DataFrame:
    """10-Q e 10-K di un indice giornaliero (form.AAAAMMGG.idx)."""
    rows = []
    for line in text.splitlines():
        m = _INDEX_LINE.match(line)
        if m:
            rows.append((m["form"], int(m["cik"]), pd.Timestamp(m["filed"]), m["acc"]))
    return pd.DataFrame(rows, columns=COLUMNS).astype({"filed": "datetime64[ns]"})


def parse_current(atom: str) -> pd.DataFrame:
    """Depositi dell'elenco "ultimi depositi" (Atom): tipo, CIK, data, numero del documento."""
    rows = []
    for entry in _ENTRY.findall(atom):
        form = re.search(r'label="form type" term="([^"]+)"', entry)
        cik = re.search(r"\((\d{10})\)", entry)
        filed = re.search(r"Filed:&lt;/b&gt;\s*(\d{4}-\d{2}-\d{2})", entry)
        acc = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", entry)
        if form and cik and filed and acc:
            rows.append((form[1], int(cik[1]), pd.Timestamp(filed[1]), acc[1]))
    return pd.DataFrame(rows, columns=COLUMNS).astype({"filed": "datetime64[ns]"})


def _report(forms: pd.Series) -> pd.Series:
    return forms.astype(str).str.upper().str.startswith(REPORT_FORMS)


def parse_header(text: str) -> tuple[list[str], pd.Timestamp | None]:
    """Voci e ora di accettazione dall'intestazione SGML di un deposito (.hdr.sgml)."""
    items = re.findall(r"<ITEMS>\s*([\d.]+)", text)
    acc = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", text)
    return items, (pd.Timestamp(acc[1]) if acc else None)


def describe_items(items: str | None) -> str:
    """"2.02,9.01" -> "Risultati del trimestre o dell'esercizio" (senza gli allegati, se c'è altro)."""
    codes = [c for c in str(items or "").split(",") if c]
    main = [c for c in codes if c != "9.01"] or codes
    return "; ".join(ITEM_LABELS.get(c, f"Voce {c}") for c in main)


def important(items: str | None) -> bool:
    return bool(set(str(items or "").split(",")) & IMPORTANT_ITEMS)


def filing_url(cik: int, accession: str) -> str:
    return FILING_URL.format(cik=int(cik), folder=accession.replace("-", ""), acc=accession)


class FilingWatcher:
    def __init__(self, db, http, cfg: dict):
        self.db, self.http = db, http
        sec = cfg.get("sec", {})
        self.lookback = int(sec.get("filings_lookback_days", 30))
        self.wait_days = int(sec.get("filings_index_wait_days", 4))
        self.max_pages = int(sec.get("filings_current_pages", 10))

    def _available(self, days: list[date]) -> set[str]:
        """Giorni con un indice pubblicato, dall'elenco della cartella del trimestre (per un file mancante la
        SEC risponde 403, non 404: meglio non chiederlo)."""
        names: set[str] = set()
        for year, q in sorted({(d.year, (d.month - 1) // 3 + 1) for d in days}):
            listing = self.http.get_json(QUARTER_LISTING_URL.format(year=year, q=q))
            names |= {i["name"] for i in listing.get("directory", {}).get("item", [])}
        return {d.isoformat() for d in days if f"form.{d:%Y%m%d}.idx" in names}

    def _current(self, today: date) -> pd.DataFrame:
        """Depositi di oggi (ora di New York) dall'elenco degli ultimi depositi, pagina per pagina."""
        frames = []
        for form in REPORT_FORMS + (EVENT_FORM,):
            for page in range(self.max_pages):
                df = parse_current(self.http.get_text(CURRENT_URL.format(form=form, start=100 * page)))
                frames.append(df[df["filed"].dt.date >= today])
                if df.empty or (df["filed"].dt.date < today).any() or len(df) < 100:
                    break
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)

    def check(self, ciks: dict, now: datetime | None = None) -> pd.DataFrame:
        """Cerca i 10-Q/10-K depositati dopo l'ultimo controllo dalle aziende di `ciks` (ticker -> CIK o lista di
        CIK) e li
        registra in sec_filings. Restituisce i depositi "da caricare" (anche quelli rimasti dai giorni prima)."""
        ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
        if ts.tzinfo is None:  # ora locale del computer
            ts = ts.tz_localize(datetime.now().astimezone().tzinfo)
        today = ts.tz_convert(NY).date()
        by_cik: dict[int, list[str]] = {}
        for t, cs in ciks.items():
            for c in (cs if isinstance(cs, (list, tuple, set)) else [cs]):
                if c is not None and not pd.isna(c):
                    by_cik.setdefault(int(c), []).append(t)
        done = {d for d in self.db.fetched_tickers(INDEX_KIND)}
        frames = []
        days = [today - timedelta(days=back) for back in range(self.lookback, -1, -1)]
        days = [d for d in days if d.weekday() < 5 and d.isoformat() not in done]
        available = self._available(days) if days else set()
        for day in days:
            if day.isoformat() not in available:
                if (today - day).days >= self.wait_days:  # ormai l'indice ci sarebbe: giorno festivo
                    self.db.mark_fetched(day.isoformat(), INDEX_KIND)
                continue
            url = DAILY_INDEX_URL.format(d=day, q=(day.month - 1) // 3 + 1)
            frames.append(parse_form_index(self.http.get_text(url)))
            self.db.mark_fetched(day.isoformat(), INDEX_KIND)
        if today.weekday() < 5:
            try:
                frames.append(self._current(today))
            except HttpError as exc:  # l'indice di domani li riporterà comunque
                logger.warning("Elenco degli ultimi depositi SEC non letto: %s", exc)
        found = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
        found = found[found["cik"].isin(by_cik.keys())]
        events = found[found["form"] == EVENT_FORM]
        self.db.add_events(pd.DataFrame([(t, r.accession, r.form, r.filed, r.cik) for r in events.itertuples(index=False)
                                         for t in by_cik[r.cik]], columns=["ticker", "accession", "form", "filed", "cik"]))
        found = found[_report(found["form"])]
        rows = [(t, r.accession, r.form, r.filed) for r in found.itertuples(index=False) for t in by_cik[r.cik]]
        new = self.db.add_filings(pd.DataFrame(rows, columns=["ticker", "accession", "form", "filed"]))
        pending = self.db.pending_filings()
        logger.info("Depositi SEC: %d nuovi 10-Q/10-K delle aziende seguite, %d da caricare (%d aziende); %d 8-K",
                    new, len(pending), pending["ticker"].nunique(), len(events))
        return pending

    def fill_items(self, limit: int = 2000) -> int:
        """Voci e ora degli 8-K registrati senza (un'intestazione di pochi KB per deposito)."""
        todo = self.db.events_without_items(limit)
        done = 0
        for r in todo.itertuples(index=False):
            try:
                text = self.http.get_text(HEADER_URL.format(cik=int(r.cik), folder=r.accession.replace("-", ""),
                                                            acc=r.accession))
            except HttpError as exc:
                logger.debug("Intestazione di %s non letta: %s", r.accession, exc)
                continue
            items, accepted = parse_header(text)
            self.db.set_event_items(r.accession, ",".join(items), accepted)
            done += 1
        if done:
            logger.info("8-K: voci lette per %d depositi", done)
        return done
