"""
Operazioni degli insider (amministratori, dirigenti, soci oltre il 10%) dai Form 4 della SEC.

Due fonti, stesso formato:
- i dataset trimestrali della SEC ("Insider Transactions Data Sets": tutti i Form 3/4/5 di un trimestre in un
  file zip da 8-15 MB), per tutte le aziende e per il passato;
- i singoli Form 4 depositati dopo l'ultimo trimestre pubblicato, solo per le aziende che servono (quelle che
  segui), dall'indice dei depositi di ogni azienda.

Cosa conta (letteratura: Lakonishok-Lee 2001, Cohen-Malloy-Pomorski 2012): gli ACQUISTI sul mercato (codice P),
soprattutto di più insider insieme. Le vendite dicono poco (tasse, diversificazione), e ancora meno quelle
programmate in anticipo (piani 10b5-1). Assegnazioni (A), esercizi di opzioni (M) e azioni trattenute per le tasse
(F) sono compensi, non decisioni d'investimento.

Ogni operazione ha la data di deposito (`filed`): le metriche si calcolano point-in-time come per i bilanci.
"""
from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
CODES = {
    "P": "acquisto sul mercato", "S": "vendita sul mercato", "A": "assegnazione (compenso)",
    "M": "esercizio di opzioni", "F": "azioni trattenute per le tasse", "G": "regalo", "D": "restituite all'azienda",
    "C": "conversione", "X": "esercizio di opzioni", "J": "altro", "W": "eredità", "I": "operazione discrezionale",
}
COLUMNS = ["accession", "line", "ticker", "owner", "owner_cik", "relationship", "title", "trans_date", "filed",
           "code", "shares", "price", "value", "acquired", "owned_after", "planned"]


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def parse_bulk(source, issuers: dict[int, str]) -> pd.DataFrame:
    """Operazioni (tabella non derivati) dei Form 4 di un dataset trimestrale, solo per gli emittenti in
    `issuers` (CIK -> ticker). `source`: percorso o byte dello zip."""
    z = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    read = lambda name, cols: pd.read_csv(z.open(name), sep="\t", dtype=str, usecols=cols, quoting=3,  # noqa: E731
                                          on_bad_lines="skip", encoding_errors="replace")
    sub = read("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "AFF10B5ONE"])
    sub["cik"] = _num(sub["ISSUERCIK"])
    sub = sub[sub["DOCUMENT_TYPE"].isin(["4", "4/A"]) & sub["cik"].isin(list(issuers))]
    if sub.empty:
        return pd.DataFrame(columns=COLUMNS)
    own = read("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
                                     "RPTOWNER_TITLE"])
    own = own[own["ACCESSION_NUMBER"].isin(sub["ACCESSION_NUMBER"])].drop_duplicates("ACCESSION_NUMBER")
    tr = read("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE",
                                     "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
                                     "SHRS_OWND_FOLWNG_TRANS"])
    tr = tr[tr["ACCESSION_NUMBER"].isin(sub["ACCESSION_NUMBER"])]
    df = tr.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
    out = pd.DataFrame({
        "accession": df["ACCESSION_NUMBER"], "line": df["NONDERIV_TRANS_SK"].astype(str),
        "ticker": df["cik"].map(issuers), "owner": df["RPTOWNERNAME"], "owner_cik": df["RPTOWNERCIK"],
        "relationship": df["RPTOWNER_RELATIONSHIP"], "title": df["RPTOWNER_TITLE"],
        "trans_date": _date(df["TRANS_DATE"]), "filed": _date(df["FILING_DATE"]), "code": df["TRANS_CODE"],
        "shares": _num(df["TRANS_SHARES"]), "price": _num(df["TRANS_PRICEPERSHARE"]),
        "acquired": df["TRANS_ACQUIRED_DISP_CD"].eq("A"), "owned_after": _num(df["SHRS_OWND_FOLWNG_TRANS"]),
        "planned": df["AFF10B5ONE"].isin(["1", "true", "True"]),
    })
    out["value"] = out["shares"] * out["price"]
    return out[COLUMNS].dropna(subset=["filed", "ticker"])


def _text(node, path: str) -> str | None:
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None


def parse_form4(xml: str, ticker: str, accession: str, filed) -> pd.DataFrame:
    """Le operazioni (non derivati) di un singolo Form 4 in XML, nello stesso formato di `parse_bulk`."""
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    owner = root.find("reportingOwner")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = [name for tag, name in (("isDirector", "Director"), ("isOfficer", "Officer"),
                                    ("isTenPercentOwner", "TenPercentOwner"), ("isOther", "Other"))
             if rel is not None and (_text(rel, tag) or "0") in ("1", "true")]
    planned = (_text(root, "aff10b5One") or "0") in ("1", "true")
    rows = []
    for i, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        shares = pd.to_numeric(_text(t, "transactionAmounts/transactionShares/value"), errors="coerce")
        price = pd.to_numeric(_text(t, "transactionAmounts/transactionPricePerShare/value"), errors="coerce")
        rows.append({
            "accession": accession, "line": f"x{i}", "ticker": ticker,
            "owner": _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None,
            "owner_cik": _text(owner, "reportingOwnerId/rptOwnerCik") if owner is not None else None,
            "relationship": ",".join(roles), "title": _text(rel, "officerTitle") if rel is not None else None,
            "trans_date": pd.to_datetime(_text(t, "transactionDate/value"), errors="coerce"),
            "filed": pd.Timestamp(filed), "code": _text(t, "transactionCoding/transactionCode"),
            "shares": shares, "price": price, "value": shares * price if pd.notna(shares) and pd.notna(price) else np.nan,
            "acquired": _text(t, "transactionAmounts/transactionAcquiredDisposedCode/value") == "A",
            "owned_after": pd.to_numeric(_text(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                                         errors="coerce"),
            "planned": planned,
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def quarter_links(html: str) -> dict[str, str]:
    """{"2026q3": url} dalla pagina dei dataset della SEC (i percorsi cambiano negli anni: si leggono dalla pagina)."""
    links = {}
    for path in re.findall(r'href="([^"]*?(\d{4}q[1-4])_form345\.zip)"', html):
        url, quarter = path
        links[quarter] = url if url.startswith("http") else "https://www.sec.gov" + url
    return links


def summary(tx: pd.DataFrame, as_of, days: int = 90) -> pd.DataFrame:
    """Per titolo, nelle operazioni depositate negli ultimi `days` giorni fino ad `as_of`: valore degli acquisti
    sul mercato, quanti insider diversi hanno comprato, valore delle vendite non programmate. Contano dirigenti e
    amministratori; i soli soci oltre il 10% sono esclusi."""
    if tx.empty:
        return pd.DataFrame(columns=["buy_value", "buyers", "sell_value"])
    as_of = pd.Timestamp(as_of)
    w = tx[(tx["filed"] <= as_of) & (tx["filed"] > as_of - pd.Timedelta(days=days))]
    # solo dirigenti e amministratori: un socio oltre il 10% che compra è spesso un fondo (es. la holding di un
    # miliardario), un'altra cosa rispetto a chi l'azienda la guida dall'interno
    w = w[~w["relationship"].fillna("").str.fullmatch("TenPercentOwner")]
    buys = w[w["code"].eq("P") & w["acquired"]]
    sells = w[w["code"].eq("S") & ~w["acquired"] & ~w["planned"]]
    return pd.DataFrame({
        "buy_value": buys.groupby("ticker")["value"].sum(),
        "buyers": buys.groupby("ticker")["owner_cik"].nunique(),
        "sell_value": sells.groupby("ticker")["value"].sum(),
    }).fillna({"buy_value": 0.0, "buyers": 0, "sell_value": 0.0})


class InsiderIngestor:
    """Scarica e salva le operazioni degli insider (tabella insider_transactions)."""

    def __init__(self, db, http, cfg: dict, bulk_dir: Path):
        self.db, self.http, self.cfg = db, http, cfg.get("insiders", {})
        self.bulk_dir = Path(bulk_dir)

    def update_bulk(self, issuers: dict[int, str], quarters: int | None = None) -> int:
        """Carica i dataset trimestrali degli ultimi `quarters` trimestri non ancora caricati."""
        quarters = int(quarters or self.cfg.get("history_quarters", 8))
        links = quarter_links(self.http.get_text(LISTING_URL))
        wanted = sorted(links)[-quarters:]
        loaded = self.db.fetched_tickers("insider_bulk")
        total = 0
        self.bulk_dir.mkdir(parents=True, exist_ok=True)
        for q in wanted:
            if q in loaded:
                continue
            path = self.bulk_dir / f"{q}_form345.zip"
            if not path.exists():
                path.write_bytes(self.http.get(links[q]).content)
            tx = parse_bulk(path, issuers)
            total += self.db.upsert_insiders(tx)
            self.db.mark_fetched(q, "insider_bulk")
            logger.info("Insider: trimestre %s, %d operazioni delle aziende seguite dal sistema", q, len(tx))
        return total

    def update_recent(self, tickers: list[str], ciks: dict[str, int], since) -> int:
        """Form 4 depositati dopo `since` (fine dell'ultimo trimestre caricato), uno per uno."""
        known = self.db.insider_accessions()
        total = 0
        for t in tickers:
            cik = ciks.get(t)
            if cik is None or pd.isna(cik):
                continue
            try:
                recent = self.http.get_json(SUBMISSIONS_URL.format(cik=int(cik)))["filings"]["recent"]
            except Exception as exc:  # noqa: BLE001 - un'azienda che non risponde non ferma le altre
                logger.warning("Insider %s: indice dei depositi non scaricato (%s)", t, exc)
                continue
            frames = []
            for form, acc, filed, doc in zip(recent["form"], recent["accessionNumber"], recent["filingDate"],
                                             recent["primaryDocument"]):
                if form not in ("4", "4/A") or pd.Timestamp(filed) <= pd.Timestamp(since) or acc in known:
                    continue
                url = ARCHIVE_URL.format(cik=int(cik), acc=acc.replace("-", ""), doc=doc.split("/")[-1])
                try:
                    frames.append(parse_form4(self.http.get_text(url), t, acc, filed))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Form 4 %s non letto: %s", url, exc)
            if frames:
                total += self.db.upsert_insiders(pd.concat(frames, ignore_index=True))
            self.db.mark_fetched(t, "insiders")
        return total
