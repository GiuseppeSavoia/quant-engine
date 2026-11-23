"""Bilanci di riserva da Yahoo: solo dove mancano le fonti primarie, che vincono sempre (nessuna rete)."""
import numpy as np
import pandas as pd

from modules import yahoo_fundamentals as yfm
from modules.market_data import MarketData

TODAY = pd.Timestamp("2026-11-22")


def yahoo(periods=("2025-12-31", "2024-12-31"), scale=1.0, current=True, ttm=None):
    """Prospetti finti nel formato di yfinance (righe = voci, colonne = chiusure)."""
    cols = pd.to_datetime(list(periods))
    k = np.arange(len(cols), 0, -1, dtype=float)  # più recente = più grande
    inc = pd.DataFrame({c: {"Total Revenue": 1000 * v * scale, "Operating Income": 150 * v * scale,
                            "Net Income Common Stockholders": 100 * v * scale, "Pretax Income": 130 * v * scale,
                            "Interest Expense": 10 * v * scale, "Diluted Average Shares": 50.0,
                            "Net Interest Income": -5.0}
                        for c, v in zip(cols, k)})
    bs_rows = {"Total Assets": 5000.0 * scale, "Stockholders Equity": 2000.0 * scale, "Total Debt": 900.0 * scale,
               "Capital Lease Obligations": 100.0 * scale}
    if current:
        bs_rows |= {"Current Assets": 1200.0 * scale, "Current Liabilities": 800.0 * scale}
    bs = pd.DataFrame({c: bs_rows for c in cols})
    cf = pd.DataFrame({c: {"Operating Cash Flow": 200.0 * scale, "Capital Expenditure": -50.0 * scale,
                           "Free Cash Flow": 150.0 * scale, "Cash Dividends Paid": -30.0 * scale} for c in cols})
    out = {"inc": inc, "bs": bs, "cf": cf, "qbs": pd.DataFrame(), "ttm_inc": pd.DataFrame(),
           "ttm_cf": pd.DataFrame(), "currency": "EUR"}
    if ttm is not None:
        c = pd.Timestamp(ttm)
        out["ttm_inc"] = pd.DataFrame({c: {"Total Revenue": 2500.0, "Operating Income": 400.0,
                                           "Interest Expense": 22.0}})
        out["qbs"] = pd.DataFrame({c: {"Current Assets": 1300.0, "Current Liabilities": 900.0,
                                       "Total Assets": 5100.0}})
    return out


def primary(rows, source="esef", filed="2026-03-01"):
    return pd.DataFrame([(t, pd.Timestamp(p), m, v, pd.Timestamp(filed), source) for t, p, m, v in rows],
                        columns=["ticker", "period_end", "metric", "value", "filed", "source"])


def empty():
    return pd.DataFrame(columns=["ticker", "period_end", "metric", "value", "filed", "source"])


def test_senza_bilanci_primari_si_scrive_l_esercizio_intero():
    rows, note = yfm.plan("SAP.DE", empty(), yahoo())
    assert set(rows["source"]) == {yfm.SOURCE}
    last = rows[rows["period_end"] == pd.Timestamp("2025-12-31")].set_index("metric")["value"]
    assert last["revenue"] == 2000 and last["operating_income"] == 300 and last["net_income"] == 200
    assert last["total_debt"] == 800  # leasing tolti come nel confronto con Yahoo
    assert last["capex"] == -50 and last["dividends_paid"] == 30  # segni come la SEC
    assert last["interest_expense"] == 20
    assert {"current_assets", "current_liabilities"} <= set(last.index)


def test_esercizio_primario_si_completano_solo_le_voci_vuote_alla_sua_data():
    prim = primary([("GE", "2025-12-28", "revenue", 1000.0), ("GE", "2025-12-28", "net_income", 99.0),
                    ("GE", "2025-12-28", "total_assets", 5000.0), ("GE", "2025-12-28", "current_assets", 1100.0)],
                   source="sec")
    rows, _ = yfm.plan("GE", prim, yahoo(periods=("2025-12-31",)))
    added = rows.set_index("metric")
    assert set(added["period_end"]) == {pd.Timestamp("2025-12-28")}  # data del bilancio primario
    assert "operating_income" in added.index and "revenue" not in added.index and "net_income" not in added.index
    # attività correnti già primarie: le passività correnti di Yahoo non si mescolano
    assert "current_liabilities" not in added.index


def test_coppia_corrente_incompleta_non_si_scrive():
    y = yahoo(current=False)
    y["bs"].loc["Current Assets"] = 1200.0
    rows, _ = yfm.plan("X.DE", empty(), y)
    assert not {"current_assets", "current_liabilities"} & set(rows["metric"])


def test_azienda_con_dati_incoerenti_si_salta():
    prim = primary([("X.MI", "2024-12-31", m, v) for m, v in
                    (("revenue", 1000.0), ("net_income", 100.0), ("total_assets", 5000.0), ("equity", 2000.0))])
    rows, note = yfm.plan("X.MI", prim, yahoo(scale=1000))  # unità diverse
    assert rows.empty and note.startswith("saltata")


def test_valuta_convertita_tranne_le_azioni():
    rows, _ = yfm.plan("PRX.AS", empty(), yahoo(periods=("2025-12-31",)), fx=lambda d: 0.5)
    v = rows.set_index("metric")["value"]
    assert v["revenue"] == 500 and v["shares"] == 50


def test_azioni_escluse_per_i_titoli_con_regole_proprie():
    rows, _ = yfm.plan("V", empty(), yahoo(), skip_metrics={"shares"})
    assert "shares" not in set(rows["metric"])


def test_ultimi_12_mesi_a_meta_si_completano():
    prim = primary([("CLX", "2025-06-30", "revenue", 1000.0), ("CLX", "2025-06-30", "net_income", 100.0),
                    ("CLX", "2025-06-30", "total_assets", 5000.0),
                    ("CLX", "2026-09-30", "revenue", 2400.0)], source="sec")
    prim.loc[prim["period_end"] == pd.Timestamp("2026-09-30"), "source"] = "sec_ttm"
    rows, _ = yfm.plan("CLX", prim, yahoo(periods=("2025-06-30",), ttm="2026-09-30"))
    t = rows[rows["source"] == yfm.SOURCE_TTM].set_index("metric")["value"]
    assert t["operating_income"] == 400 and t["current_assets"] == 1300 and "revenue" not in t.index


def test_filed_resta_quello_del_primo_scarico_se_il_valore_non_cambia():
    rows, _ = yfm.plan("SAP.DE", empty(), yahoo())
    old = yfm.with_filed(rows, None, pd.Timestamp("2026-11-01"))
    rows.loc[rows["metric"] == "revenue", "value"] *= 1.05
    new = yfm.with_filed(rows, old, TODAY)
    f = new.set_index(["period_end", "metric"])["filed"]
    assert f[(pd.Timestamp("2025-12-31"), "net_income")] == pd.Timestamp("2026-11-01")
    assert f[(pd.Timestamp("2025-12-31"), "revenue")] == TODAY


def test_marketdata_la_fonte_primaria_vince_anche_se_depositata_prima():
    fin = pd.concat([
        primary([("AAA", "2025-12-31", "revenue", 1000.0)], source="sec", filed="2026-02-01"),
        primary([("AAA", "2025-12-31", "revenue", 999.0), ("AAA", "2025-12-31", "operating_income", 150.0)],
                source=yfm.SOURCE, filed="2026-11-22"),
    ], ignore_index=True)
    close = pd.DataFrame({"AAA": [10.0, 11.0]}, index=pd.to_datetime(["2026-11-20", "2026-11-21"]))
    md = MarketData(close=close, high=close, low=close, volume=close, financials=fin)
    for view in (md, md.as_of(TODAY)):  # anche nelle viste alla data
        st = view.statements("AAA")
        assert st["revenue"].iloc[-1] == 1000 and st["operating_income"].iloc[-1] == 150


def test_filler_scrive_e_rilancio_idempotente(cfg, demo_db):
    t = demo_db.load_financials()["ticker"].iloc[0]
    calls = []

    def fetcher(tk):
        calls.append(tk)
        return yahoo(periods=("2026-06-30",))
    f = yfm.YahooFiller(demo_db, cfg, fetcher=fetcher, fx_rate=lambda s, d, day: 1.0)
    f.update([t, "NEW.DE"], today=TODAY)
    got = demo_db.load_financials(["NEW.DE"])
    assert len(got) and set(got["source"]) == {yfm.SOURCE}
    assert "net_interest_income" not in set(got["metric"])  # solo per banche e finanziarie
    f.update([t, "NEW.DE"], today=TODAY + pd.Timedelta(days=3))
    again = demo_db.load_financials(["NEW.DE"])
    assert len(again) == len(got) and set(pd.to_datetime(again["filed"])) == {TODAY}


def test_copia_locale_si_rilegge_senza_rete(tmp_path):
    y = yahoo()
    raw = {"fetched": TODAY.isoformat(), "currency": "EUR",
           "frames": {k: (None if v.empty else v.to_json(orient="split", date_format="iso"))
                      for k, v in y.items() if isinstance(v, pd.DataFrame)}}
    (tmp_path / "SAP.DE.json").write_text(__import__("json").dumps(raw))
    got = yfm.fetch("SAP.DE", tmp_path, max_age_days=7, now=TODAY + pd.Timedelta(days=1))
    assert got["currency"] == "EUR"
    pd.testing.assert_frame_equal(yfm.annual_values(got), yfm.annual_values(y), check_freq=False)


def test_in_un_esercizio_esef_solo_voci_che_concordano_con_esef():
    prim = primary([("AIR.PA", "2025-12-31", "revenue", 1000.0), ("AIR.PA", "2025-12-31", "net_income", 100.0),
                    ("AIR.PA", "2025-12-31", "total_assets", 5000.0)], source="esef")
    rows, _ = yfm.plan("AIR.PA", prim, yahoo(periods=("2025-12-31",)))
    added = set(rows["metric"])
    assert "pretax_income" in added and {"current_assets", "current_liabilities"} <= added
    assert not {"operating_income", "interest_expense", "total_debt"} & added


def test_risposta_vuota_di_yahoo_non_cancella_i_valori_salvati(cfg, demo_db):
    ok = yfm.YahooFiller(demo_db, cfg, fetcher=lambda t: yahoo(periods=("2026-06-30",)),
                         fx_rate=lambda s, d, day: 1.0)
    ok.update(["NEW.DE"], today=TODAY)
    n = len(demo_db.load_financials(["NEW.DE"]))

    def empty_fetch(t):
        raise ValueError("Yahoo non ha restituito prospetti")
    res = yfm.YahooFiller(demo_db, cfg, fetcher=empty_fetch).update(["NEW.DE"], today=TODAY)
    assert res["errori"] and len(demo_db.load_financials(["NEW.DE"])) == n


def test_chi_ha_valori_di_riserva_resta_candidato():
    fin = pd.concat([primary([("AAA", "2026-06-30", m, 1.0) for m in ("revenue", "net_income")], source="sec"),
                     primary([("AAA", "2026-06-30", "operating_income", 1.0)], source=yfm.SOURCE)])
    assert yfm.candidates(fin, ["AAA"], set(), ["revenue", "net_income"], 450, TODAY) == ["AAA"]
    assert yfm.candidates(fin[fin["source"] == "sec"], ["AAA"], set(), ["revenue", "net_income"], 450, TODAY) == []


def test_avviso_ultimo_periodo_a_meta_anche_se_completato_da_yahoo():
    from modules.config import DEFAULTS
    from modules.data_quality import DataQualityChecker

    base = ["revenue", "net_income", "operating_income", "total_assets", "equity", "cash", "total_debt",
            "free_cash_flow", "shares", "capex"]
    rows = [("CLX", "2025-06-30", m, 1.0) for m in base]
    fin = pd.concat([primary(rows, source="sec"),
                     primary([("CLX", "2026-09-30", m, 1.0) for m in base[:3]], source="sec_ttm"),
                     primary([("CLX", "2026-09-30", m, 1.0) for m in base[3:]], source=yfm.SOURCE_TTM,
                             filed="2026-11-22")], ignore_index=True)
    close = pd.DataFrame({"CLX": [10.0, 11.0]}, index=pd.to_datetime(["2026-11-20", "2026-11-21"]))
    md = MarketData(close=close, high=close, low=close, volume=close, financials=fin)
    issues = DataQualityChecker(DEFAULTS["quality"], max_stale_days=5)._sparse_period("CLX", md)
    assert len(issues) == 1 and issues[0][1] == "ultimo periodo a metà"
    assert "3 voci dai depositi ufficiali contro 10" in issues[0][3] and "7 completate" in issues[0][3]
    full = primary([("CLX", "2026-09-30", m, 1.0) for m in base], source="sec_ttm")
    md2 = MarketData(close=close, high=close, low=close, volume=close,
                     financials=pd.concat([primary(rows, source="sec"), full], ignore_index=True))
    assert DataQualityChecker(DEFAULTS["quality"], max_stale_days=5)._sparse_period("CLX", md2) == []
