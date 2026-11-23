"""Test di insider (Form 4) e grandi investitori (13F) su esempi nel formato reale della SEC."""
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from modules import insiders as ins
from modules import superinvestors as sup
from modules.market_data import MarketData

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000354950</issuerCik><issuerTradingSymbol>HD</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0001650122</rptOwnerCik><rptOwnerName>Rossi Maria</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-20</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>300.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4():
    df = ins.parse_form4(FORM4, "HD", "0000354950-26-000177", "2026-09-22")
    r = df.iloc[0]
    assert r["code"] == "P" and r["acquired"] and not r["planned"] and r["relationship"] == "Director"
    assert r["value"] == pytest.approx(300500.0) and r["owned_after"] == 5000
    assert r["filed"] == pd.Timestamp("2026-09-22") and r["owner"] == "Rossi Maria"


def _bulk_zip() -> bytes:
    tsv = lambda header, rows: "\t".join(header) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n"  # noqa: E731
    files = {
        "SUBMISSION.tsv": tsv(["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "AFF10B5ONE"],
                              [["A1", "05-AUG-2026", "4", "0000000111", "0"], ["A2", "06-AUG-2026", "4", "0000000111", "1"],
                               ["A3", "06-AUG-2026", "4", "0000000999", "0"], ["A4", "06-AUG-2026", "3", "0000000111", "0"]]),
        "REPORTINGOWNER.tsv": tsv(["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
                                   "RPTOWNER_TITLE"], [["A1", "1", "Bianchi", "Officer", "CEO"],
                                                       ["A2", "2", "Verdi", "Director", ""]]),
        "NONDERIV_TRANS.tsv": tsv(["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                   "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS"],
                                  [["A1", "10", "04-AUG-2026", "P", "100", "50", "A", "1100"],
                                   ["A2", "11", "05-AUG-2026", "S", "200", "51", "D", "0"],
                                   ["A3", "12", "05-AUG-2026", "P", "100", "50", "A", "100"]]),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_parse_bulk_and_summary():
    tx = ins.parse_bulk(_bulk_zip(), {111: "XYZ"})
    assert list(tx["accession"]) == ["A1", "A2"]                   # altri emittenti e Form 3 esclusi
    assert tx.set_index("accession").at["A2", "planned"]           # vendita programmata (10b5-1)
    s = ins.summary(tx, "2026-08-10", days=90).loc["XYZ"]
    assert s["buy_value"] == 5000 and s["buyers"] == 1 and s["sell_value"] == 0   # la vendita programmata non conta
    assert ins.summary(tx, "2026-08-05", days=90).loc["XYZ", "buy_value"] == 5000  # depositata il 5: c'è
    assert "XYZ" not in ins.summary(tx, "2026-08-04", days=90).index                  # prima del deposito: no


def test_quarter_links():
    html = ('<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip">x</a>'
            '<a href="/files/structureddata/data/insider-transactions-data-sets/2026q1_form345.zip">y</a>')
    links = ins.quarter_links(html)
    assert set(links) == {"2026q3", "2026q1"} and links["2026q3"].startswith("https://www.sec.gov/files/")


TABLE = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip>
    <value>1000</value><shrsOrPrnAmt><sshPrnamt>10</sshPrnamt></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip>
    <value>500</value><shrsOrPrnAmt><sshPrnamt>5</sshPrnamt></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>ALLY FINL INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>02005N100</cusip>
    <value>300</value><shrsOrPrnAmt><sshPrnamt>3</sshPrnamt></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
</informationTable>"""


def test_parse_13f_and_tickers():
    pos = sup.parse_13f(TABLE, "2026-08-14")
    assert len(pos) == 1 and pos.iloc[0]["value"] == 1500 and pos.iloc[0]["shares"] == 15   # somma; put esclusa
    old = sup.parse_13f(TABLE, "2022-11-14")
    assert old.iloc[0]["value"] == 1_500_000                                               # prima del 2023: migliaia
    comp = pd.DataFrame({"ticker": ["AAPL", "ALLY", "GOOGL", "GOOG", "AMAT"],
                         "name": ["Apple Inc.", "Ally Financial", "Alphabet Inc. (Class A)", "Alphabet Inc. (Class C)",
                                  "Applied Materials"]})
    m = sup.match_tickers(pd.Series(["APPLE INC", "ALLY FINL INC", "ALPHABET INC CAP STK CL C", "NONEXISTENT CORP"]),
                          comp)
    assert m["APPLE INC"] == "AAPL" and m["ALLY FINL INC"] == "ALLY"
    assert m["ALPHABET INC CAP STK CL C"] == "GOOG" and "NONEXISTENT CORP" not in m


def _holdings():
    rows = []
    for acc, report, filed, positions in (("Q1", "2026-03-31", "2026-05-15", {"AAPL": 10, "KO": 5}),
                                          ("Q2", "2026-06-30", "2026-08-14", {"AAPL": 20, "MSFT": 7})):
        for t, sh in positions.items():
            rows.append({"fund_cik": 1, "fund_name": "Fondo", "accession": acc, "report_date": pd.Timestamp(report),
                         "filed": pd.Timestamp(filed), "issuer": t, "cusip": t, "title": "COM", "value": sh * 100.0,
                         "shares": float(sh), "ticker": t})
    return pd.DataFrame(rows)


def test_fund_positions_and_metrics_point_in_time():
    pos = sup.fund_positions(_holdings()).set_index("ticker")
    assert pos.at["AAPL", "change"] == "aumentata" and pos.at["MSFT", "change"] == "nuova"
    assert pos.at["KO", "change"] == "uscita" and pos.at["AAPL", "weight"] == pytest.approx(20 / 27)
    before = sup.fund_positions(_holdings(), as_of="2026-06-01").set_index("ticker")
    assert set(before.index) == {"AAPL", "KO"}                        # il 13F di agosto non era ancora pubblico

    dates = pd.bdate_range("2026-01-01", "2026-09-30")
    px = pd.concat([pd.DataFrame({"date": dates, "ticker": t, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1})
                    for t in ("AAPL", "MSFT", "KO")])
    data = MarketData.from_long(px, pd.DataFrame())
    data.holdings = _holdings()
    data.insiders = ins.parse_form4(FORM4, "AAPL", "X1", "2026-09-22")
    from modules.metrics import ownership_metrics

    m = ownership_metrics(data, ["AAPL", "MSFT", "KO"])
    assert m.at["AAPL", "funds_holding"] == 1 and m.at["KO", "funds_holding"] == 0 and m.at["MSFT", "funds_adding"] == 1
    assert m.at["AAPL", "insider_buy_value"] == pytest.approx(300500.0) and m.at["AAPL", "insider_buyers"] == 1
    early = ownership_metrics(data.as_of("2026-07-01"), ["AAPL", "MSFT"])
    assert early.at["MSFT", "funds_holding"] == 0 and early.at["AAPL", "insider_buy_value"] == 0


def test_ten_percent_owners_excluded_from_signals():
    tx = ins.parse_bulk(_bulk_zip(), {111: "XYZ"})
    big = tx.iloc[[0]].assign(accession="A9", relationship="TenPercentOwner", value=1e9, owner_cik="9")
    s = ins.summary(pd.concat([tx, big]), "2026-08-10").loc["XYZ"]
    assert s["buy_value"] == 5000 and s["buyers"] == 1


def test_13f_unita_sbagliate_corrette_nel_database_una_volta_sola(tmp_path):
    from data.db_manager import DBManager

    db = DBManager(tmp_path / "t.db")
    days = pd.to_datetime(["2026-09-29", "2026-09-30"])
    db.upsert_prices(pd.DataFrame([{"ticker": t, "date": d, "close": px}
                                   for t, px in (("A", 10.0), ("B", 20.0), ("C", 40.0)) for d in days]))
    rows = []
    for acc, scale in (("migliaia", 0.001), ("dollari", 1.0), ("troppo", 1000.0), ("obbligazioni", 0.01)):
        for t, px in (("A", 10.0), ("B", 20.0), ("C", 40.0)):
            rows.append({"fund_cik": 1, "fund_name": "Fondo", "accession": acc, "report_date": days[1],
                         "filed": days[1], "issuer": t, "cusip": t + acc, "title": "COM",
                         "value": 1000.0 * px * scale, "shares": 1000.0, "ticker": t})
    db.upsert_thirteenf(pd.DataFrame(rows)[sup.COLUMNS])

    dry = sup.fix_units(db, dry_run=True)
    assert set(dry["accession"]) == {"migliaia", "troppo"}
    assert db.load_thirteenf().groupby("accession")["value"].sum()["migliaia"] == pytest.approx(70)

    version = db.data_version()
    assert len(sup.fix_units(db)) == 2
    assert db.data_version() != version  # la copia veloce della webapp va rifatta
    tot = db.load_thirteenf().groupby("accession")["value"].sum()
    assert tot["migliaia"] == pytest.approx(70_000) and tot["troppo"] == pytest.approx(70_000)
    assert tot["dollari"] == pytest.approx(70_000) and tot["obbligazioni"] == pytest.approx(700)
    assert sup.fix_units(db).empty  # idempotente
    assert sup.fix_units(db, accessions=["dollari"]).empty
