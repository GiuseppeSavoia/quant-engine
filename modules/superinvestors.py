"""
Portafogli di una lista di grandi investitori dai 13F della SEC (config.yaml, sezione superinvestors).

Ogni gestore con più di 100 milioni in azioni USA deposita ogni trimestre un 13F con le posizioni a fine trimestre,
entro 45 giorni: è un'istantanea vecchia di 1-3 mesi, non sai cosa ha fatto dopo. Le posizioni sono per CUSIP e
nome dell'emittente (senza ticker): il ticker si ricava dal nome confrontandolo con l'anagrafica delle aziende.
Valori in dollari dai depositi del 2023 in poi, in migliaia prima. Le opzioni (put/call) sono escluse.
Alcuni fondi non rispettano l'unità (Baupost, Duquesne in migliaia dopo il 2023; Lone Pine in dollari prima):
`unit_factors` li riconosce dal prezzo implicito e `fix_units` corregge il database (anche dopo ogni ingestione).
"""
from __future__ import annotations

import difflib
import logging
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OLDER_URL = "https://data.sec.gov/submissions/{name}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
DOLLARS_SINCE = pd.Timestamp("2023-01-03")  # prima di allora i 13F riportavano i valori in migliaia
COLUMNS = ["fund_cik", "fund_name", "accession", "report_date", "filed", "issuer", "cusip", "title", "value",
           "shares", "ticker"]
STOP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LP", "LLC", "THE", "HLDGS",
        "HOLDINGS", "HOLDING", "GROUP", "GRP", "CL", "CLASS", "A", "B", "C", "COM", "NEW", "DEL", "SA", "NV", "AG",
        "SE", "ORD", "SHS", "CAP", "STK", "N", "V", "DE", "INCORPORATED", "TR", "TRUST"}
ABBREV = {"FINL": "FINANCIAL", "INTL": "INTERNATIONAL", "TECHNOLOGIE": "TECHNOLOGIES", "TECH": "TECHNOLOGIES",
          "PHARMACEUTICALS": "PHARMA", "SYS": "SYSTEMS", "SVCS": "SERVICES", "COMMUNICATIONS": "COMMUNICATION",
          "MTRS": "MOTORS", "PPTYS": "PROPERTIES", "BANCORPORATION": "BANCORP", "AMER": "AMERICAN", "MGMT": "MANAGEMENT"}


def _tokens(name: str) -> list[str]:
    # gli apostrofi si tolgono senza spezzare la parola: "Moody's" e "MOODYS" devono coincidere
    plain = str(name).upper().replace("'", "").replace("’", "").replace("&", " AND ")
    words = re.sub(r"[^A-Z0-9 ]", " ", plain).split()
    return [ABBREV.get(w, w) for w in words if w not in STOP]


def match_tickers(issuers: pd.Series, companies: pd.DataFrame) -> dict[str, str]:
    """{nome dell'emittente: ticker} confrontando i nomi (prima parola uguale, poi la somiglianza più alta).
    `companies`: ticker, name. Le azioni di classe C (es. Alphabet) vanno al titolo con "Class C" nel nome."""
    comp = companies.dropna(subset=["name"]).copy()
    comp["tok"] = comp["name"].map(_tokens)
    comp = comp[comp["tok"].map(len) > 0]
    by_first = comp.groupby(comp["tok"].map(lambda t: t[0]))
    groups = {k: g for k, g in by_first}
    out = {}
    for issuer in issuers.dropna().unique():
        tok = _tokens(issuer)
        if not tok or tok[0] not in groups:
            continue
        cand = groups[tok[0]]
        if len(cand) == 1:
            out[issuer] = cand["ticker"].iloc[0]
            continue
        wants_c = bool(re.search(r"\bCL(ASS)? C\b|CAP STK CL C", str(issuer).upper()))
        scores = [(difflib.SequenceMatcher(None, " ".join(tok), " ".join(t)).ratio()
                   + (0.5 if wants_c == ("CLASS C" in n.upper()) else 0), tk)
                  for t, tk, n in zip(cand["tok"], cand["ticker"], cand["name"])]
        best = max(scores)
        if best[0] >= 0.5:
            out[issuer] = best[1]
    return out


def parse_13f(xml: str, filed) -> pd.DataFrame:
    """Posizioni (azioni, non opzioni) di una tabella 13F, sommate per CUSIP."""
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    ns = {"n": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    p = "n:" if ns else ""
    rows = []
    for t in root.findall(f"{p}infoTable", ns):
        if t.find(f"{p}putCall", ns) is not None:
            continue
        get = lambda path: (t.find(path, ns).text or "").strip() if t.find(path, ns) is not None else ""  # noqa: E731
        rows.append({"issuer": get(f"{p}nameOfIssuer"), "cusip": get(f"{p}cusip"), "title": get(f"{p}titleOfClass"),
                     "value": pd.to_numeric(get(f"{p}value"), errors="coerce"),
                     "shares": pd.to_numeric(get(f"{p}shrsOrPrnAmt/{p}sshPrnamt"), errors="coerce")})
    df = pd.DataFrame(rows, columns=["issuer", "cusip", "title", "value", "shares"])
    if df.empty:
        return df
    if pd.Timestamp(filed) < DOLLARS_SINCE:
        df["value"] = df["value"] * 1000
    return df.groupby("cusip", as_index=False).agg(issuer=("issuer", "first"), title=("title", "first"),
                                                  value=("value", "sum"), shares=("shares", "sum"))


UNIT_BAND = 0.5      # log10 del rapporto (valore / azioni) / prezzo: 0 in dollari, -3 in migliaia, +3 se x1.000 di troppo
UNIT_MIN_ROWS = 3    # posizioni con ticker e prezzo necessarie per decidere


def unit_factors(holdings: pd.DataFrame, close: pd.DataFrame | None) -> dict[str, float]:
    """Depositi con l'unità sbagliata -> fattore che la corregge (1.000 o 0,001). Per ogni deposito: rapporto tra
    prezzo implicito (valore / azioni) e chiusura a fine trimestre, in mediana sulle posizioni con un ticker. Si
    corregge solo se il rapporto è a 1.000 volte (± mezzo ordine di grandezza): i fondi con obbligazioni (azioni =
    valore nominale, rapporto ~0,01) e gli split (prezzi rettificati, azioni no: fattore al massimo 20-30) restano
    fuori. Dopo la correzione il rapporto torna ~1, quindi rifarla non cambia nulla."""
    if holdings is None or holdings.empty or close is None or close.empty:
        return {}
    h = holdings[holdings["ticker"].isin(close.columns) & (holdings["shares"] > 0) & (holdings["value"] > 0)]
    if h.empty:
        return {}
    filled = close[sorted(set(h["ticker"]))].ffill()
    ratios = []
    for rd, g in h.groupby("report_date"):
        i = filled.index.searchsorted(pd.Timestamp(rd), side="right") - 1
        if i < 0:
            continue
        px = filled.iloc[i].reindex(g["ticker"]).to_numpy(dtype=float)
        ratios.append(pd.DataFrame({"accession": g["accession"].to_numpy(),
                                    "ratio": (g["value"] / g["shares"]).to_numpy(dtype=float) / px}))
    if not ratios:
        return {}
    r = pd.concat(ratios).replace([np.inf, -np.inf], np.nan).dropna()
    r = r[r["ratio"] > 0]
    stats = np.log10(r["ratio"]).groupby(r["accession"]).agg(["median", "count"])
    stats = stats[stats["count"] >= UNIT_MIN_ROWS]
    return {**{a: 1000.0 for a in stats.index[(stats["median"] + 3).abs() <= UNIT_BAND]},
            **{a: 0.001 for a in stats.index[(stats["median"] - 3).abs() <= UNIT_BAND]}}


def fix_units(db, accessions=None, dry_run: bool = False) -> pd.DataFrame:
    """Corregge nel database i depositi 13F con l'unità sbagliata (tutti, o solo `accessions`). Idempotente.
    Restituisce un deposito per riga (fondo, trimestre, fattore, totale prima e dopo); con `dry_run` non scrive."""
    h = db.load_thirteenf()
    if accessions is not None:
        h = h[h["accession"].isin(set(accessions))]
    cols = ["fund_name", "accession", "report_date", "factor", "value_before", "value_after"]
    tickers = sorted(set(h["ticker"].dropna())) if not h.empty else []
    if not tickers:
        return pd.DataFrame(columns=cols)
    px = db.load_prices(tickers, start=(h["report_date"].min() - pd.Timedelta(days=15)).strftime("%Y-%m-%d"))
    close = px.pivot_table(index="date", columns="ticker", values="close") if not px.empty else None
    factors = unit_factors(h, close)
    if not factors:
        return pd.DataFrame(columns=cols)
    g = h[h["accession"].isin(factors)].groupby("accession").agg(
        fund_name=("fund_name", "first"), report_date=("report_date", "first"), value_before=("value", "sum"))
    g["factor"] = g.index.map(factors)
    g["value_after"] = g["value_before"] * g["factor"]
    out = g.reset_index()[cols].sort_values(["fund_name", "report_date"]).reset_index(drop=True)
    if not dry_run:
        db.scale_thirteenf_values(factors)
        for r in out.itertuples():
            logger.info("13F %s al %s (%s): valori x%g", r.fund_name, r.report_date.date(), r.accession, r.factor)
    return out


def fund_positions(holdings: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Per ogni fondo, l'ultimo 13F depositato entro `as_of` e il precedente: posizione, peso nel portafoglio e
    variazione delle azioni (nuova, aumentata, ridotta, uscita)."""
    h = holdings if as_of is None else holdings[holdings["filed"] <= pd.Timestamp(as_of)]
    if h.empty:
        return pd.DataFrame()
    rows = []
    for fund, g in h.groupby("fund_cik"):
        reports = sorted(g["report_date"].unique())
        last = g[g["report_date"] == reports[-1]]
        last = last[last["filed"] == last["filed"].max()]
        prev = g[g["report_date"] == reports[-2]] if len(reports) > 1 else g.iloc[:0]
        prev = prev[prev["filed"] == prev["filed"].max()] if not prev.empty else prev
        total = last["value"].sum()
        prev_shares = prev.set_index("cusip")["shares"] if not prev.empty else pd.Series(dtype=float)
        for r in last.itertuples():
            before = prev_shares.get(r.cusip, np.nan)
            change = ("nuova" if not len(prev) or pd.isna(before) else
                      "aumentata" if r.shares > before * 1.02 else "ridotta" if r.shares < before * 0.98 else "invariata")
            rows.append({"fund_cik": fund, "fund_name": r.fund_name, "report_date": r.report_date, "filed": r.filed,
                         "issuer": r.issuer, "ticker": r.ticker, "value": r.value, "shares": r.shares,
                         "weight": r.value / total if total else np.nan,
                         "shares_change": r.shares / before - 1 if pd.notna(before) and before else np.nan,
                         "change": change})
        if len(prev):  # posizioni chiuse
            gone = prev[~prev["cusip"].isin(last["cusip"])]
            for r in gone.itertuples():
                rows.append({"fund_cik": fund, "fund_name": r.fund_name, "report_date": reports[-1],
                             "filed": last["filed"].max(), "issuer": r.issuer, "ticker": r.ticker, "value": 0.0,
                             "shares": 0.0, "weight": 0.0, "shares_change": -1.0, "change": "uscita"})
    return pd.DataFrame(rows)


class ThirteenFIngestor:
    def __init__(self, db, http, cfg: dict):
        self.db, self.http = db, http
        self.cfg = cfg.get("superinvestors", {})

    def _filings(self, cik: int, quarters: int) -> list[tuple[str, str, str]]:
        """Ultimi `quarters` 13F-HR (numero, deposito, trimestre): l'elenco "recent" della SEC ha al massimo
        1.000 depositi di ogni tipo, i più vecchi stanno in file a parte (filings.files)."""
        sub = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        pages = [sub["filings"]["recent"]]
        out: list[tuple[str, str, str]] = []
        while pages:
            page = pages.pop(0)
            out += [(a, f, r) for form, a, f, r in zip(page["form"], page["accessionNumber"], page["filingDate"],
                                                       page["reportDate"]) if form == "13F-HR"]
            if len(out) >= quarters:
                break
            if not pages and sub["filings"].get("files"):
                pages = [self.http.get_json(OLDER_URL.format(name=f["name"])) for f in sub["filings"].pop("files")]
        return out[:quarters]

    def update(self, companies: pd.DataFrame, quarters: int | None = None) -> int:
        """Ultimi `quarters` 13F-HR di ogni fondo della lista (solo quelli non ancora salvati)."""
        quarters = int(quarters or self.cfg.get("history_quarters", 8))
        known = self.db.thirteenf_accessions()
        total, new = 0, []
        # nomi già salvati senza ticker: si riprova (anagrafica aggiornata o regole di confronto migliorate)
        missing = self.db.thirteenf_unmatched()
        if missing:
            self.db.set_thirteenf_tickers(match_tickers(pd.Series(missing), companies))
        for fund in self.cfg.get("funds", []):
            cik, name = int(fund["cik"]), fund["name"]
            try:
                filings = self._filings(cik, quarters)
            except Exception as exc:  # noqa: BLE001 - un fondo che non risponde non ferma gli altri
                logger.warning("13F %s: indice non scaricato (%s)", name, exc)
                continue
            for acc, filed, report in filings:
                if acc in known:
                    continue
                folder = acc.replace("-", "")
                try:
                    items = self.http.get_json(INDEX_URL.format(cik=cik, acc=folder))["directory"]["item"]
                    xmls = [i for i in items if i["name"].lower().endswith(".xml") and "primary" not in i["name"].lower()]
                    if not xmls:
                        continue
                    doc = max(xmls, key=lambda i: int(i.get("size") or 0))["name"]
                    pos = parse_13f(self.http.get_text(DOC_URL.format(cik=cik, acc=folder, doc=doc)), filed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("13F %s %s non letto: %s", name, acc, exc)
                    continue
                if pos.empty:
                    continue
                tickers = match_tickers(pos["issuer"], companies)
                pos = pos.assign(fund_cik=cik, fund_name=name, accession=acc, report_date=pd.Timestamp(report),
                                 filed=pd.Timestamp(filed), ticker=pos["issuer"].map(tickers))
                total += self.db.upsert_thirteenf(pos[COLUMNS])
                new.append(acc)
                logger.info("13F %s al %s: %d posizioni (%d riconosciute)", name, report, len(pos),
                            pos["ticker"].notna().sum())
        if new:
            fix_units(self.db, new)
        return total
