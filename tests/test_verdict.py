"""Test del giudizio su un'azienda (modules/verdict.py): casi sintetici senza rete né database reale, più un giro
su tutto l'universo demo."""
import numpy as np
import pandas as pd
import pytest

from modules import verdict as vd
from modules.verdict import (AVOID, CANDIDATE, FLAGS, INSIDERS, NEUTRAL, NO_DATA, PRICE, QUALITY, SOLIDITY,
                             VALUE_TRAP, WAIT, VerdictInput, judge, profile_of, settings)

AS_OF = pd.Timestamp("2026-11-12")
RNG = np.random.default_rng(0)
PEERS = {  # 20 aziende simili, valori fissi
    "operating_margin": np.linspace(0.02, 0.40, 20), "pe": np.linspace(10, 40, 20),
    "ev_ebit": np.linspace(8, 35, 20), "p_fcf": np.linspace(10, 45, 20), "ev_sales": np.linspace(1, 10, 20),
    "ptbv": np.linspace(0.8, 3.0, 20), "p_ffo": np.linspace(10, 25, 20), "pb": np.linspace(0.8, 3.0, 20),
}

GOOD_BUSINESS = dict(
    roic=0.25, roic_avg_5y=0.24, roe_min_5y=0.20, operating_margin=0.30, net_margin=0.20, cash_conversion=1.2,
    revenue_cagr_5y=0.08, eps_cagr_5y=0.10, margin_trend=0.01, profitable_years=10, dividend_years=10,
    net_debt_ebitda=0.5, interest_coverage=20.0, altman_z=5.0, piotroski=8, current_ratio=1.8, debt_to_equity=0.4,
    beneish_m=-2.6, accruals=-0.03, share_change=-0.01, sbc_to_revenue=0.01, fcf_margin=0.20,
    insider_buyers=0, insider_net_pct=0.0, funds_holding=4, funds_adding=1, roe=0.3, roa=0.12,
)
POOR_BUSINESS = dict(GOOD_BUSINESS, roic=0.03, roic_avg_5y=0.03, roe_min_5y=0.01, operating_margin=0.03,
                     cash_conversion=0.6, revenue_cagr_5y=-0.03, eps_cagr_5y=-0.05, margin_trend=-0.04,
                     profitable_years=2, piotroski=4, altman_z=2.5, interest_coverage=4.0, net_debt_ebitda=2.5)
CHEAP = dict(price=100.0, market_cap=1e10, enterprise_value=1.05e10, fcf_yield=0.09, pe=11.0, pe_vs_history=0.7,
             ev_ebit=9.0, p_fcf=11.0, ev_sales=1.5)
EXPENSIVE = dict(price=100.0, market_cap=1e10, enterprise_value=1.05e10, fcf_yield=0.02, pe=45.0, pe_vs_history=1.5,
                 ev_ebit=38.0, p_fcf=50.0, ev_sales=11.0)


def make(metrics: dict, implied: float | None = None, **kw) -> VerdictInput:
    """Un'azienda sintetica: `implied` = crescita che il prezzo sconta sulla curva del DCF."""
    m = dict(metrics)
    base = dict(ticker="TEST", metrics=m, sector="Industrials", sub_industry="Machinery", as_of=AS_OF,
                price_date=AS_OF, statement_date=AS_OF - pd.Timedelta(days=60), wacc=0.08, cost_of_equity=0.09,
                sector_peers=PEERS, sector_group="del settore di prova", fcf_lag_days=0.0, quality_issues=[])
    if implied is not None:
        g = np.linspace(-0.10, 0.60, 141)
        base.update(dcf_growths=g, dcf_values=m["price"] * (1 + (g - implied) * 4))
    base.update(kw)
    return VerdictInput(**base)


def test_candidate():
    v = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03))
    assert v.category == CANDIDATE, v.summary()
    assert v.label == "Candidata all'acquisto" and v.rule and v.coverage > 0.9
    assert v.score(QUALITY) > 0.8 and v.score(SOLIDITY) > 0.8 and v.score(PRICE) >= 0.6
    # cosa farebbe cambiare: il prezzo oltre il quale non basta più
    assert any("Sopra circa" in c for c in v.changes)
    favorable = [e for e in v.pillars[QUALITY].evidence if e.outcome == "favorevole"]
    assert favorable and all(e.text and e.reference for e in favorable)


def test_wait_good_business_expensive_with_target_price():
    v = judge(make({**GOOD_BUSINESS, **EXPENSIVE}, implied=0.20))
    assert v.category == WAIT, v.summary()
    target = [c for c in v.changes if c.startswith("Sotto circa")]
    assert target, v.changes
    # al prezzo indicato il giudizio cambia davvero (multipli scalati di conseguenza)
    k = float(target[0].split("(")[1].split("%")[0].replace("\u2212", "-")) / 100 + 1  # meno tipografico nel testo
    scaled = {**GOOD_BUSINESS, **EXPENSIVE, "price": 100 * k, "market_cap": 1e10 * k,
              "enterprise_value": 1.05e10 + 1e10 * (k - 1), "fcf_yield": 0.02 / k, "pe": 45 * k,
              "pe_vs_history": 1.5 * k, "ev_ebit": 38 * (1.05e10 + 1e10 * (k - 1)) / 1.05e10,
              "p_fcf": 50 * k, "ev_sales": 11 * (1.05e10 + 1e10 * (k - 1)) / 1.05e10}
    inp = make(scaled, implied=0.20)
    inp.dcf_values = 100 * (1 + (inp.dcf_growths - 0.20) * 4)  # stessa curva: il valore non dipende dal prezzo
    assert judge(inp).category == CANDIDATE


def test_value_trap_and_avoid():
    trap = judge(make({**POOR_BUSINESS, **CHEAP}, implied=-0.05))
    assert trap.category == VALUE_TRAP, trap.summary()
    assert any("servirebbe almeno" in c for c in trap.changes)
    avoid = judge(make({**POOR_BUSINESS, **EXPENSIVE}, implied=0.15))
    assert avoid.category == AVOID, avoid.summary()


def test_severe_alarm_means_avoid_and_beneish_only_blocks_candidate():
    distressed = {**GOOD_BUSINESS, **CHEAP, "altman_z": 1.2, "interest_coverage": 1.1}
    v = judge(make(distressed, implied=0.03))
    assert v.category == AVOID and v.alarms and "dissesto" in v.alarms[0]
    burning = {**GOOD_BUSINESS, **CHEAP, "fcf_margin": -0.1, "net_margin": -0.05}
    assert judge(make(burning, implied=0.03)).category == AVOID
    beneish = {**GOOD_BUSINESS, **CHEAP, "beneish_m": -1.0, "accruals": 0.10}
    v = judge(make(beneish, implied=0.03))
    assert v.category == NEUTRAL and v.checks and not v.alarms


def test_neutral_middle_of_the_road():
    middle = {**GOOD_BUSINESS, "roic": 0.10, "roic_avg_5y": 0.10, "roe_min_5y": 0.08, "operating_margin": 0.15,
              "cash_conversion": 0.85, "revenue_cagr_5y": 0.03, "eps_cagr_5y": 0.04, "margin_trend": -0.01,
              "profitable_years": 6, **{k: v for k, v in EXPENSIVE.items()}, "pe_vs_history": 1.0, "fcf_yield": 0.05}
    v = judge(make(middle, implied=0.05))
    assert v.category == NEUTRAL, v.summary()


def test_missing_data_is_never_positive():
    full = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03))
    # togliere prove favorevoli non alza mai il punteggio
    fewer = {**GOOD_BUSINESS, **CHEAP, "roic": np.nan, "eps_cagr_5y": np.nan, "altman_z": np.nan}
    v = judge(make(fewer, implied=0.03))
    assert v.score(QUALITY) < full.score(QUALITY) and v.score(SOLIDITY) < full.score(SOLIDITY)
    assert v.coverage < full.coverage
    # e non lo alza nemmeno per un'azienda debole: il punteggio sotto il neutro resta dov'è
    poor = judge(make({**POOR_BUSINESS, **CHEAP}, implied=-0.05))
    poor_less = judge(make({**POOR_BUSINESS, **CHEAP, "roic": np.nan, "roe_min_5y": np.nan}, implied=-0.05))
    assert poor_less.score(QUALITY) <= poor.pillars[QUALITY].mean + 1e-9
    # una sola prova eccellente non basta: pilastro senza punteggio e giudizio "Dati insufficienti"
    only_one = {"price": 100.0, "roic": 0.40, "fcf_yield": 0.10, "pe_vs_history": 0.5}
    v = judge(make(only_one))
    assert v.category == NO_DATA and v.score(QUALITY) is None
    assert any("mancano" in c for c in v.changes)


def test_low_overall_coverage_means_no_data():
    c = settings({"verdict": {"min_coverage": 0.99}})
    v = judge(make({**GOOD_BUSINESS, **CHEAP, "current_ratio": np.nan}, implied=0.03), c)
    assert v.category == NO_DATA and "prove" in v.rule


def test_data_errors_and_stale_price():
    inp = make({**GOOD_BUSINESS, **CHEAP}, implied=0.03,
               quality_issues=[("bilanci assenti", "errore", "nessun bilancio")])
    assert judge(inp).category == NO_DATA
    stale = make({**GOOD_BUSINESS, **CHEAP}, implied=0.03, price_date=AS_OF - pd.Timedelta(days=30))
    v = judge(stale)
    assert v.category == NO_DATA and v.data_issues
    no_price = judge(make({**GOOD_BUSINESS, **CHEAP, "price": np.nan}, implied=0.03))
    assert no_price.category == NO_DATA


def test_data_warning_lowers_flags():
    clean = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03))
    warned = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03,
                        quality_issues=[("debito forse incompleto", "avviso", "interessi alti")]))
    assert warned.score(FLAGS) < clean.score(FLAGS)
    unknown = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03, quality_issues=None))
    assert any(e.key == "data_warnings" for e in unknown.pillars[FLAGS].missing)


def test_user_fair_value_dominates_price():
    expensive = {**GOOD_BUSINESS, **EXPENSIVE}
    without = judge(make(expensive, implied=0.20))
    mine = judge(make(expensive, implied=0.20, user_fair_value=160.0))
    assert mine.score(PRICE) > without.score(PRICE)
    fv = mine.pillars[PRICE].evidence[0]
    assert fv.key == "fv_margin" and fv.outcome == "favorevole" and "fair value" in fv.text
    total = sum(e.weight for e in mine.pillars[PRICE].evidence)
    assert fv.weight / total == pytest.approx(vd.DEFAULTS["user_fair_value_share"])


def test_bank_uses_sector_metrics():
    bank = dict(price=100.0, market_cap=3e11, rotce=0.18, roe_min_5y=0.11, efficiency_ratio=0.55,
                provision_ratio=0.06, eps_cagr_5y=0.08, profitable_years=10, roa=0.012, roe=0.15, ptbv=1.6,
                pe=11.0, pe_vs_history=0.9, share_change=-0.02,
                net_debt_ebitda=9.0, altman_z=0.3, roic=0.01)  # numeri generali senza senso per una banca: ignorati
    inp = make(bank, sector="Financials", sub_industry="Diversified Banks", profile=None)
    assert profile_of("Financials", "Diversified Banks", bank) == "bank"
    v = judge(inp)
    keys = {e.key for p in v.pillars.values() for e in p.evidence}
    assert {"rotce", "efficiency_ratio", "tce_assets", "ptbv_justified"} <= keys
    assert not keys & {"roic", "net_debt_ebitda", "altman_z", "implied_growth_gap"}
    assert v.profile == "bank" and v.category != NO_DATA and not v.alarms, v.summary()
    ptbv = next(e for e in v.pillars[PRICE].evidence if e.key == "ptbv_justified")
    assert ptbv.outcome == "favorevole"  # 1,6x contro (18%-2,5%)/(9%-2,5%) = 2,4x giustificato


def test_profiles():
    assert profile_of("Financials", "Property & Casualty Insurance", {}) == "insurer"
    assert profile_of("Financials", "Insurance Brokers", {}) == "financial"
    assert profile_of("Financials", "Transaction & Payment Processing Services", {}) == "financial"
    assert profile_of("Banks", None, {}) == "bank"            # settori ICB delle europee
    assert profile_of("Insurance", None, {}) == "insurer"
    assert profile_of("Real Estate", "Retail REITs", {}) == "reit"
    assert profile_of("Real Estate", "Real Estate Services", {}) == "general"
    assert profile_of("Information Technology", "Semiconductors", {}) == "general"


def test_reit():
    reit = dict(price=50.0, market_cap=4e10, revenue_cagr_5y=0.06, operating_margin=0.30, cash_conversion=2.0,
                profitable_years=10, dividend_years=10, margin_trend=0.0, net_debt_ebitda=5.0, interest_coverage=4.5,
                debt_to_equity=0.8, piotroski=6, p_ffo=12.0, pb=1.2, sbc_to_revenue=0.005, share_change=0.08)
    v = judge(make(reit, sector="Real Estate", sub_industry="Retail REITs"))
    assert v.profile == "reit"
    keys = {e.key for p in v.pillars.values() for e in p.evidence}
    assert "p_ffo" in keys and "share_change" not in keys  # i REIT crescono emettendo azioni: non è un allarme
    assert v.category == CANDIDATE, v.summary()


def test_european_with_few_data_degrades_gracefully():
    eu = dict(price=15.0, market_cap=5e10, roic=0.06, roic_avg_5y=0.08, cash_conversion=2.0, revenue_cagr_5y=0.05,
              net_debt_ebitda=1.4, piotroski=6, fcf_yield=0.05, pe_vs_history=1.1)
    v = judge(make(eu, ticker="ENI.MI", sector="Energy", sub_industry=None, sec_filer=False, currency="EUR",
                   sector_peers={}, statement_date=AS_OF - pd.Timedelta(days=316), fcf_lag_days=None,
                   quality_issues=None, wacc=None))
    assert v.score(INSIDERS) is None  # niente Form 4 né 13F per chi non deposita alla SEC
    assert v.category in vd.LABELS
    nothing = judge(make({"price": 200.0}, ticker="SAP.DE", sector="Information Technology", sec_filer=False,
                         sector_peers={}, statement_date=None, fcf_lag_days=None, quality_issues=None))
    assert nothing.category == NO_DATA and nothing.overall is None


def test_insider_signals_are_context_only():
    base = judge(make({**GOOD_BUSINESS, **EXPENSIVE}, implied=0.20))
    buying = judge(make({**GOOD_BUSINESS, **EXPENSIVE, "insider_buyers": 3, "insider_net_pct": 0.002},
                        implied=0.20))
    assert buying.score(INSIDERS) > base.score(INSIDERS) and buying.category == base.category
    none = [e for e in base.pillars[INSIDERS].evidence if e.key == "insider_buyers"][0]
    assert none.outcome == "neutro"  # nessun acquisto non è un segnale contrario


def test_deterministic_and_config_merge():
    inp = make({**GOOD_BUSINESS, **CHEAP}, implied=0.03)
    a, b = judge(inp).to_dict(), judge(inp).to_dict()
    assert repr(a) == repr(b)
    strict = settings({"verdict": {"thresholds": {"roic": [0.50, 0.30]}, "quality_good": 0.95}})
    assert strict["thresholds"]["roic"] == (0.50, 0.30) and strict["thresholds"]["altman_z"] == (3.0, 1.8)
    assert judge(inp, {"verdict": {"quality_good": 1.01}}).category == VALUE_TRAP
    assert vd.DEFAULTS["quality_good"] == 0.60  # i default non vengono toccati


def test_assess_many_on_demo_universe(cfg, demo_db, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    tickers = list(d.tickers)
    table, verdicts = vd.assess_many(d, tickers, return_verdicts=True)
    assert sorted(table.index) == sorted(tickers) and set(verdicts) == set(tickers)
    assert table["verdict"].isin(vd.LABELS.values()).all()
    assert table["coverage"].between(0, 1).all()
    for col in (QUALITY, SOLIDITY, PRICE, FLAGS):
        s = table[col].dropna()
        assert s.between(0, 1).all()
    one = vd.assess_ticker(d, tickers[0])
    assert one.category == verdicts[tickers[0]].category
    assert one.summary().startswith(tickers[0])


def test_texts_in_italian_and_no_global_config():
    """Le frasi escono già in italiano (0,93 ≥ 0,60; 1.234,5) senza cambiare regole e punteggi; la configurazione
    passa come parametro (niente stato globale: due configurazioni alternate danno ciascuna il proprio risultato)."""
    import re

    assert not hasattr(vd, "_CFG_CACHE")
    english = re.compile(r"\d\.\d+(?![,.\d])|>=|<=")   # "0.93 >= 0.60"; "1.234,5" è italiano
    cases = [make({**GOOD_BUSINESS, **CHEAP}, implied=0.03),
             make({**GOOD_BUSINESS, **EXPENSIVE}, implied=0.25),
             make({**POOR_BUSINESS, **CHEAP}, implied=0.03, user_fair_value=1234.5),
             make({**GOOD_BUSINESS, **CHEAP, "altman_z": 1.2, "interest_coverage": 1.1})]
    for inp in cases:
        v = judge(inp)
        texts = [v.rule, *v.alarms, *v.checks, *v.changes, *v.data_issues, v.summary()]
        texts += [t for p in v.pillars.values() for e in p.evidence + p.missing for t in (e.shown, e.reference, e.text)]
        bad = [t for t in texts if english.search(t)]
        assert not bad, bad
    cand = judge(cases[0])
    assert cand.category == CANDIDATE and "≥" in cand.rule and re.search(r"\d,\d\d ≥ \d,\d\d", cand.rule)
    fv = next(e for e in judge(cases[2]).pillars[PRICE].evidence if e.key == "fv_margin")
    assert "1.234,50" in fv.text
    # stessa azienda, due configurazioni alternate: il numero minimo di aziende simili cambia l'esito della prova
    strict = settings({"verdict": {"min_sector_peers": 50}})
    a1, b1, a2 = judge(cases[0]), judge(cases[0], strict), judge(cases[0])
    ev = lambda v: next(e for e in v.pillars[QUALITY].evidence + v.pillars[QUALITY].missing  # noqa: E731
                        if e.key == "margin_sector")
    assert ev(a1).outcome != "mancante" and ev(b1).outcome == "mancante" and ev(a2).outcome == ev(a1).outcome
    assert a1.overall == a2.overall


def _pillars(**scores):
    from modules.verdict import Pillar
    return {k: Pillar(k, k, s, s, 1.0, "", [], []) for k, s in scores.items()}


def test_prezzo_solo_freno_per_candidata():
    """Dal 2026-11-23 il Prezzo frena e non promuove: basta che non sia alto (>= price_brake)."""
    from modules.verdict import _decide
    brake, gate = settings(None), settings({"verdict": {"price_mode": "gate"}})
    p = _pillars(**{QUALITY: 0.8, SOLIDITY: 0.8, PRICE: 0.5, FLAGS: 0.8, INSIDERS: None})
    assert _decide(p, 1.0, [], [], brake, [])[0] == CANDIDATE
    assert _decide(p, 1.0, [], [], gate, [])[0] == WAIT  # regola di prima: serviva un prezzo interessante
    p[PRICE].score = 0.3
    cat, rule = _decide(p, 1.0, [], [], brake, [])
    assert cat == WAIT and "alto" in rule
    # il prezzo basso non salva un'azienda debole: resta "trappola"
    weak = _pillars(**{QUALITY: 0.3, SOLIDITY: 0.8, PRICE: 0.8, FLAGS: 0.8, INSIDERS: None})
    assert _decide(weak, 1.0, [], [], brake, [])[0] == VALUE_TRAP


def test_nota_se_l_ultimo_bilancio_viene_dalla_riserva():
    from types import SimpleNamespace

    from modules.market_data import MarketData
    from modules.verdict import _Context
    rows = [("AAA", "2024-12-31", m, 10.0, "2025-02-01", "sec") for m in ("revenue", "net_income", "equity")]
    rows += [("AAA", "2025-12-31", m, 11.0, "2026-11-22", "yahoo_fill") for m in ("revenue", "net_income", "equity")]
    rows += [("BBB", "2025-12-31", "revenue", 5.0, "2026-02-01", "sec"),
             ("BBB", "2025-12-31", "equity", 5.0, "2026-11-22", "yahoo_fill")]
    fin = pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
    fin[["period_end", "filed"]] = fin[["period_end", "filed"]].apply(pd.to_datetime)
    close = pd.DataFrame({"AAA": [1.0], "BBB": [1.0]}, index=pd.to_datetime(["2026-11-20"]))
    md = MarketData(close=close, high=close, low=close, volume=close, financials=fin)
    ctx = _Context.__new__(_Context)
    ctx.d, ctx.fallback = SimpleNamespace(data=md), {"AAA", "BBB"}
    note = ctx.source_notes("AAA")
    assert len(note) == 1 and "preso interamente da Yahoo" in note[0] and "31/12/2025" in note[0]
    part = ctx.source_notes("BBB")
    assert part and "completato da Yahoo per: patrimonio netto" in part[0] and "ricavi" not in part[0]
    assert md.statement_sources("BBB").loc[pd.Timestamp("2025-12-31"), "revenue"] == "sec"
    v = judge(make({**GOOD_BUSINESS, **CHEAP}, implied=0.03, data_notes=note))
    assert v.data_notes == note and v.category == CANDIDATE  # la cautela non cambia il giudizio
