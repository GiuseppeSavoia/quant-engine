"""Test dei dati inseriti a mano e delle aziende aggiunte dall'utente."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import manual_data
from modules.market_data import MarketData


def test_build_rows_units_signs_and_derived():
    rows = manual_data.build_rows("ENI.MI", "2025-12-31", {"operating_cash_flow": 12.0, "capex": 8.0,
                                                           "dividends_paid": -3.0, "shares": 3.1},
                                  kind="annual", filed="2026-03-15", amount_unit=1e9, share_unit=1e9)
    v = rows.set_index("metric")["value"]
    assert v["capex"] == -8e9 and v["dividends_paid"] == 3e9                    # segni come nei dati SEC
    assert v["free_cash_flow"] == pytest.approx(4e9)                            # ricavato: 12 - 8
    assert v["shares"] == pytest.approx(3.1e9)
    assert set(rows["source"]) == {"manual"} and (rows["filed"] == pd.Timestamp("2026-03-15")).all()
    ttm = manual_data.build_rows("X", "2025-06-30", {"revenue": 5}, kind="ttm")
    assert set(ttm["source"]) == {"manual_ttm"} and ttm["filed"].iloc[0] == pd.Timestamp.now().normalize()
    with pytest.raises(ValueError):
        manual_data.build_rows("X", pd.Timestamp.now() + pd.Timedelta(days=30), {"revenue": 1})
    with pytest.raises(ValueError):
        manual_data.build_rows("X", "2025-12-31", {"revenue": 1}, filed="2025-06-30")   # noto prima della chiusura
    with pytest.raises(ValueError):
        manual_data.build_rows("X", "2025-12-31", {"boh": 1})


def test_changed_cells():
    before = pd.DataFrame({"2024-12-31": [100.0, np.nan], "2025-12-31": [110.0, 5.0]}, index=["revenue", "capex"])
    after = before.copy()
    after.loc["capex", "2024-12-31"] = 4.0                                       # aggiunto
    after.loc["revenue", "2025-12-31"] = 111.0                                   # corretto
    after["2026-03-31"] = [120.0, np.nan]                                        # periodo nuovo
    assert manual_data.changed_cells(before, after) == {"2024-12-31": {"capex": 4.0},
                                                        "2025-12-31": {"revenue": 111.0},
                                                        "2026-03-31": {"revenue": 120.0}}


def _data(fin_rows) -> MarketData:
    dates = pd.bdate_range("2025-01-01", "2026-10-30")
    px = pd.DataFrame({"date": dates, "ticker": "X", "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1})
    fin = pd.DataFrame(fin_rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
    return MarketData.from_long(px, fin)


def test_manual_values_win_over_sec_from_their_date():
    sec = ("X", "2025-12-31", "net_income", 100.0, "2026-02-15", "sec")
    sec_restated = ("X", "2025-12-31", "net_income", 90.0, "2026-09-01", "sec")
    fix = ("X", "2025-12-31", "net_income", 120.0, "2026-03-01", "manual")
    data = _data([sec, fix, sec_restated])
    assert data.statements("X")["net_income"].iloc[-1] == 120.0                # vince anche su una versione SEC successiva
    assert data.as_of("2026-02-20").statements("X")["net_income"].iloc[-1] == 100.0   # prima della correzione
    assert data.as_of("2026-03-02").statements("X")["net_income"].iloc[-1] == 120.0
    assert _data([sec, sec_restated]).statements("X")["net_income"].iloc[-1] == 90.0  # senza correzioni: come prima
    ttm = _data([sec, ("X", "2026-06-30", "net_income", 130.0, "2026-08-01", "manual_ttm")])
    assert ttm.ttm_statements("X")["net_income"].iloc[-1] == 130.0 and ttm.statements("X")["net_income"].iloc[-1] == 100.0


def test_manual_entry_in_dashboard_and_custom_companies(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    version = demo_db.user_data_version()
    table = d.manual_table("AAPL", "annual", 1e6, 1e6)
    assert "revenue" in table.index and len(table.columns) > 0
    period = table.columns[-1]
    old_ni = table.at["net_income", period]
    saved = d.save_manual("AAPL", {period: {"net_income": old_ni * 2, "operating_cash_flow": 500.0, "capex": -100.0}},
                          "annual", 1e6, 1e6, days_after_close=60)
    assert saved == 4                                                           # 3 voci + free cash flow ricavato
    assert demo_db.user_data_version() != version                               # la webapp ricarica i dati
    d2 = DashboardData(cfg, demo_db, tmp_path)
    st = d2.data.statements("AAPL")
    assert st["net_income"].iloc[-1] == pytest.approx(old_ni * 2 * 1e6)
    assert st["free_cash_flow"].iloc[-1] == pytest.approx(400e6)
    manual = demo_db.load_manual_financials("AAPL")
    assert len(manual) == 4 and (manual["filed"] == pd.Timestamp(period) + pd.Timedelta(days=60)).all()
    demo_db.delete_manual_financials([tuple(r) for r in manual[["ticker", "period_end", "metric", "filed"]].values])
    assert demo_db.load_manual_financials("AAPL").empty

    demo_db.add_custom_company("ENI.MI", "Eni", "Energy", "EUR")
    d3 = DashboardData(cfg, demo_db, tmp_path)
    assert "ENI.MI" in d3.tickers and "ENI.MI" not in d3.index_tickers          # mai nei backtest
    assert d3.currency("ENI.MI") == "EUR" and d3.info("ENI.MI")["name"] == "Eni"
    assert "ENI.MI" in universe.data_tickers()                                  # main.py ne scarica i prezzi
    demo_db.remove_custom_company("ENI.MI")
    assert "ENI.MI" not in universe.data_tickers()
