"""
Azioni delle aziende con più classi (es. Berkshire A e B), dalla copertina di ogni 10-Q e 10-K.

Queste aziende dichiarano le azioni solo classe per classe (o con etichette proprie, es. le "azioni equivalenti
di classe A" di Berkshire): valori che l'API companyfacts della SEC non riporta, quindi nei bilanci il numero di
azioni manca o è fermo a molti anni prima. La copertina del documento invece c'è sempre, anche nella versione
"R1.htm" che la SEC pubblica per ogni deposito XBRL: una sezione per classe con "Entity Common Stock, Shares
Outstanding".

Il totale è espresso nella classe del prezzo che l'app usa (il ticker): azioni di ogni classe per il suo fattore di
conversione (config.yaml, sec.share_classes; es. per BRK-B: classe A x 1.500 + classe B). Se compare una classe
senza fattore il deposito si scarta (meglio nessun numero che uno sbagliato). Il risultato va in `financials` come
metrica `shares` alla chiusura del periodo, con la data di deposito: fonte `sec_classes` per i 10-K (esercizi)
e `sec_classes_ttm` per i 10-Q (righe "ultimi 12 mesi"), così si unisce alle righe dei bilanci dello stesso periodo.
"""
from __future__ import annotations

import html
import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OLDER_URL = "https://data.sec.gov/submissions/{name}"
R1_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/R1.htm"
SHARES_ROW = "entity common stock, shares outstanding"
SOURCES = ("sec_classes", "sec_classes_ttm")


def _rows(page: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
        rows.append(cells)
    return rows


NUMBER = re.compile(r"[\d,]+(\.\d+)?")
DATE = re.compile(r"^[A-Z][a-z]{2}\.? \d{1,2}, \d{4}")


def parse_cover(page: str) -> list[tuple[str | None, float]]:
    """(classe, azioni) per ogni numero della riga "Entity Common Stock, Shares Outstanding" della copertina
    (R1.htm). Due impaginazioni: una sezione per classe (la classe è l'ultima riga-titolo prima del numero, es.
    "Common Class A [Member]") oppure, nei depositi 2011-2014, una colonna per classe (riga di intestazione che
    comincia con una data, es. "Apr. 19, 2013 Class A Common Stock", sopra il numero)."""
    out, section, columns = [], None, None
    for cells in _rows(page):
        if not cells or not cells[0]:
            continue
        first, rest = cells[0], cells[1:]
        filled = [c for c in rest if c]
        if first.lower().startswith(SHARES_ROW):
            for i, c in enumerate(rest):
                if NUMBER.fullmatch(c):
                    label = DATE.sub("", columns[i]).strip() if columns is not None and len(columns) == len(rest) \
                        else ""
                    out.append((label or section, float(c.replace(",", ""))))
        elif DATE.match(first) and filled:
            columns = cells  # intestazione delle colonne (date, a volte con la classe): tutte le celle sono colonne
        elif not filled and not first.endswith(("[Line Items]", "[Abstract]")) and not first.startswith("- "):
            section = first
    return out


def equivalent_shares(by_class: list[tuple[str | None, float]], factors: dict[str, float]) -> float | None:
    """Totale nella classe quotata. Ogni classe prende il fattore della chiave più lunga contenuta nel suo nome
    (es. "Class B common stock" -> "class b", non "common"). None se una classe non ha fattore."""
    keys = sorted(((k.lower(), float(v)) for k, v in factors.items()), key=lambda kv: -len(kv[0]))
    total = 0.0
    for label, shares in by_class:
        name = (label if isinstance(label, str) else "").lower().replace("[member]", "").strip()
        factor = next((v for k, v in keys if k in name), None)
        if factor is None:
            return None
        total += shares * factor
    return total if total > 0 else None


class ShareClassIngestor:
    def __init__(self, db, http, cfg: dict):
        self.db, self.http = db, http
        self.cfg = cfg.get("sec", {}).get("share_classes") or {}

    def _filings(self, cik: int) -> list[tuple[str, str, str, str]]:
        """Tutti i 10-K e 10-Q (numero, tipo, deposito, chiusura del periodo), anche quelli negli archivi vecchi."""
        sub = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        pages = [sub["filings"]["recent"]] + [self.http.get_json(OLDER_URL.format(name=f["name"]))
                                              for f in sub["filings"].get("files", [])]
        return [(a, f, d, r) for page in pages
                for a, f, d, r in zip(page["accessionNumber"], page["form"], page["filingDate"], page["reportDate"])
                if f in ("10-K", "10-Q") and r]

    def update(self, ciks: dict[str, int]) -> int:
        """Legge le copertine non ancora viste delle aziende in config e riscrive le loro azioni. Restituisce quante
        copertine nuove ha letto."""
        seen = self.db.share_class_accessions()
        read = 0
        for ticker, spec in self.cfg.items():
            cik = ciks.get(ticker)
            if cik is None or pd.isna(cik):
                continue
            since = pd.Timestamp(spec.get("from", "2009-01-01"))
            try:
                filings = [f for f in self._filings(int(cik)) if pd.Timestamp(f[2]) >= since]
            except Exception as exc:  # noqa: BLE001 - un'azienda che non risponde non ferma le altre
                logger.warning("Classi di azioni di %s: elenco dei depositi non letto (%s)", ticker, exc)
                continue
            rows = []
            for acc, form, filed, period in filings:
                if acc in seen:
                    continue
                try:
                    page = self.http.get_text(R1_URL.format(cik=int(cik), folder=acc.replace("-", "")))
                except Exception as exc:  # noqa: BLE001 - es. depositi senza XBRL (prima del 2009-2011)
                    logger.debug("Copertina di %s %s non letta: %s", ticker, acc, exc)
                    by_class = []
                else:
                    by_class = parse_cover(page)
                read += 1
                rows += [(ticker, acc, form, filed, period, label or "", shares) for label, shares in by_class]
                if not by_class:  # niente azioni in copertina: si ricorda comunque di averla letta
                    rows.append((ticker, acc, form, filed, period, "", None))
            self.db.add_share_class_counts(rows)
            self._rebuild(ticker, spec)
        return read

    def _rebuild(self, ticker: str, spec: dict) -> None:
        """Riscrive le azioni di `ticker` dalle copertine salvate, solo per i periodi che hanno già un bilancio
        SEC: finché l'API non riporta un 10-Q nuovo, una riga con le sole azioni diventerebbe "l'ultimo bilancio"."""
        counts = self.db.load_share_class_counts(ticker)
        fin = self.db.load_financials([ticker])
        periods = set(fin.loc[fin["source"].isin(["sec", "sec_ttm"]), "period_end"])
        out, skipped = [], set()
        for (acc, form, filed, period), g in counts.dropna(subset=["shares"]).groupby(
                ["accession", "form", "filed", "period_end"]):
            if pd.Timestamp(period) not in periods:
                continue
            total = equivalent_shares(list(zip(g["class_label"], g["shares"])), spec["classes"])
            if total is None:
                skipped |= set(g["class_label"])
                continue
            source = SOURCES[0] if form.startswith("10-K") else SOURCES[1]
            out.append((ticker, pd.Timestamp(period), "shares", total, pd.Timestamp(filed), source))
        if skipped:
            logger.warning("Azioni di %s: classi senza fattore di conversione in config (depositi scartati): %s",
                           ticker, sorted(skipped))
        df = pd.DataFrame(out, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
        self.db.replace_financials(ticker, df, SOURCES)
        if not df.empty:
            logger.info("Azioni di %s dalle copertine: %d periodi, ultimo %s = %.0f", ticker, len(df),
                        df["period_end"].max().date(), df.sort_values("period_end")["value"].iloc[-1])
