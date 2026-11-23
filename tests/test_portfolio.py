"""Test del portafoglio reale (costo medio, euro, tasse stimate, confronto) e del diario delle decisioni."""
import numpy as np
import pandas as pd
import pytest

from modules import journal, portfolio

DAYS = pd.bdate_range("2026-01-01", periods=60)


def _tx(rows):
    df = pd.DataFrame(rows, columns=["ticker", "kind", "date", "quantity", "price", "currency", "fx", "fees", "note"])
    df.insert(0, "id", range(1, len(df) + 1))
    df["date"] = pd.to_datetime(df["date"])
    return df


FX = pd.DataFrame({"EURUSD=X": np.linspace(1.10, 1.20, 60)}, index=DAYS)


def test_average_cost_realized_gain_and_fx():
    tx = _tx([("AAPL", "BUY", "2026-01-01", 10, 100.0, "USD", 1.10, 1.1, None),
              ("AAPL", "BUY", "2026-01-15", 10, 120.0, "USD", np.nan, 0.0, None),     # cambio da Yahoo
              ("AAPL", "SELL", "2026-02-02", 5, 150.0, "USD", 1.20, 0.0, None),
              ("ENI.MI", "BUY", "2026-01-05", 100, 15.0, "EUR", np.nan, 2.0, None)])
    pos, realized = portfolio.positions(tx, FX, {"AAPL": 130.0, "ENI.MI": 16.0}, {"USD": 1.20, "EUR": 1.0})
    a = pos.set_index("ticker").loc["AAPL"]
    rate15 = float(FX.loc[:"2026-01-15", "EURUSD=X"].iloc[-1])
    cost = (1000 + 1.1) / 1.10 + 1200 / rate15                    # in euro, commissioni comprese
    assert a["quantity"] == 15 and a["cost_eur"] == pytest.approx(cost * 15 / 20)
    assert a["avg_cost"] == pytest.approx((1000 + 1.1 + 1200) / 20)  # prezzo medio nella valuta del titolo
    assert a["value_eur"] == pytest.approx(15 * 130 / 1.20)
    r = realized.iloc[0]
    assert r["proceeds_eur"] == pytest.approx(5 * 150 / 1.20) and r["gain_eur"] == pytest.approx(5 * 150 / 1.20 - cost / 4)
    e = pos.set_index("ticker").loc["ENI.MI"]
    assert e["cost_eur"] == pytest.approx(1502.0) and e["gain_eur"] == pytest.approx(1600 - 1502)
    assert pos["weight"].sum() == pytest.approx(1.0)


def test_taxes_dividends_and_symbols():
    realized = pd.DataFrame({"ticker": ["A", "B"], "date": pd.to_datetime(["2026-03-01", "2026-05-01"]),
                             "quantity": [1, 1], "proceeds_eur": [0, 0], "cost_eur": [0, 0], "gain_eur": [1000.0, -400.0]})
    pos = pd.DataFrame({"ticker": ["C", "D"], "gain_eur": [500.0, -100.0]})
    t = portfolio.tax_estimate(realized, pos, today="2026-11-16")
    # vendendo oggi: +500 e -100 si compensano (azioni), il realizzato dell'anno (+600) è già tassato a parte
    assert t["by_year"][2026]["tax"] == pytest.approx(600 * 0.26) and t["tax_if_sold"] == pytest.approx(400 * 0.26)
    # un ETF in guadagno non si compensa con le perdite delle azioni (redditi di capitale)
    t = portfolio.tax_estimate(realized, pos, today="2026-11-16", funds={"C"})
    assert t["tax_if_sold"] == pytest.approx(500 * 0.26 - 100 * 0.26)  # -100 riduce l'imposta sul realizzato
    # anno in perdita: le perdite già realizzate assorbono i guadagni di oggi
    loss = realized.assign(gain_eur=[-1000.0, 0.0])
    assert portfolio.tax_estimate(loss, pos, today="2026-11-16")["tax_if_sold"] == pytest.approx(0.0)
    assert portfolio.tax_estimate(loss, pd.DataFrame({"ticker": ["C"], "gain_eur": [np.nan]}),
                                  today="2026-11-16")["incomplete"]
    tx = _tx([("AAPL", "DIV", "2026-02-02", 10, 0.25, "USD", 1.2, 0, None),
              ("ENI.MI", "DIV", "2026-02-02", 100, 0.5, "EUR", np.nan, 0, None)])
    div = portfolio.dividends(tx, FX, {"AAPL"}).set_index("ticker")
    assert div.at["AAPL", "us_tax"] == pytest.approx(2.5 / 1.2 * 0.15)
    assert div.at["AAPL", "net_eur"] == pytest.approx(2.5 / 1.2 * 0.85 * 0.74)
    assert div.at["ENI.MI", "us_tax"] == 0 and div.at["ENI.MI", "net_eur"] == pytest.approx(50 * 0.74)
    assert portfolio.needed_symbols(tx) == ["AAPL", "ENI.MI", "EURUSD=X"]


def test_value_history_vs_benchmark_in_euro():
    close = pd.DataFrame({"AAPL": np.linspace(100, 130, 60), "SPY": np.linspace(500, 550, 60)}, index=DAYS)
    tx = _tx([("AAPL", "BUY", "2026-01-01", 10, 100.0, "USD", 1.10, 0.0, None)])
    h = portfolio.value_history(tx, close, FX, benchmark="SPY")
    assert h["invested"].iloc[-1] == pytest.approx(1000 / 1.10)
    assert h["value"].iloc[-1] == pytest.approx(10 * 130 / 1.20)
    # stessi euro nello SPY al cambio del giorno: 909,09 € -> 1000 $ -> 2 quote -> 1100 $ -> 916,67 €
    assert h["benchmark"].iloc[-1] == pytest.approx(1000 / 500 * 550 / 1.20)


def test_journal_outcomes_and_hit_rate():
    close = pd.DataFrame({"AAA": np.linspace(10, 20, 200), "BBB": np.linspace(10, 8, 200),
                          "SPY": np.linspace(100, 110, 200)}, index=pd.bdate_range("2025-06-02", periods=200))
    j = pd.DataFrame({"id": [1, 2, 3], "ticker": ["AAA", "BBB", "AAA"], "action": ["BUY", "PASS", "SELL"],
                      "date": pd.to_datetime(["2025-06-02", "2025-06-02", "2026-02-20"]), "price": [10.0, 10.0, np.nan],
                      "review_date": pd.to_datetime(["2025-12-01", None, None]), "reviewed_at": [None, None, None]})
    out = journal.outcomes(j, close, "SPY").set_index("id")
    assert out.at[1, "right"] is True and out.at[2, "right"] is True                # BUY salito, PASS sceso
    assert out.at[3, "right"] is None                                              # meno di 3 mesi: non si giudica
    assert out.at[1, "review_due"] and not out.at[2, "review_due"]
    h = journal.hit_rate(out.reset_index())
    assert h["judged"] == 2 and h["rate"] == 1.0
