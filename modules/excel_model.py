"""
Il modello di valutazione di un'azienda in Excel, con le formule vere (non solo i numeri).

Foglio "DCF": le leve sono celle blu modificabili; la tabella dei 10 anni, il valore finale e il valore per azione
sono formule che rifanno riga per riga `modules.valuation_model.value` (crescita alta per 5 anni e poi in calo fino a
quella finale, margine che va verso l'obiettivo, tasse solo sugli utili, reinvestimento = aumento dei ricavi / ricavi
per unità di capitale, costo del capitale che converge, fase stabile con reinvestimento = crescita / ROIC, probabilità
di fallimento). Ogni formula porta anche il valore calcolato dall'app, così il file si legge anche senza ricalcolo e
il test controlla che i due coincidano.
Altri fogli (valori calcolati dall'app, non formule): scenari, sensibilità al costo del capitale e alla crescita,
costo del capitale passo per passo (con formule), bilanci storici con la fonte di ogni esercizio, note e fonti.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from modules import lbo as lbo_mod
from modules.valuation_model import HIGH_GROWTH_YEARS, YEARS, Base, Levers, value
from modules.xlsx import Sheet, workbook_bytes

FADE = YEARS - HIGH_GROWTH_YEARS
# riga delle leve nel foglio DCF (colonna B)
INPUT_ROWS = {
    "revenue": 5, "margin": 6, "invested_capital": 7, "cash": 8, "debt": 9, "shares": 10, "price": 11,
    "growth": 14, "target_margin": 15, "margin_years": 16, "sales_to_capital": 17, "wacc": 18, "terminal_wacc": 19,
    "terminal_growth": 20, "terminal_roic": 21, "tax_rate": 22, "failure_prob": 23, "distress_proceeds": 24,
}
TABLE_HEAD = 27
FIRST = TABLE_HEAD + 1           # anno 1
LAST = FIRST + YEARS - 1          # anno 10
COLS = ["Anno", "Crescita", "Ricavi", "Margine", "EBIT", "NOPAT", "Reinvestimento", "FCFF", "Costo del capitale",
        "Fattore di sconto", "Valore attuale"]


def _b(key: str) -> str:
    return f"$B${INPUT_ROWS[key]}"


def dcf_sheet(base: Base, lv: Levers, title: str, currency: str) -> tuple[Sheet, dict]:
    """Foglio DCF con formule. Restituisce anche le celle dei risultati (per i test)."""
    res = value(base, lv, with_table=True)
    tb = res.table
    s = Sheet("DCF", widths={1: 44, **{c: 15 for c in range(2, 12)}}, freeze=None)
    s.set(1, 1, title, "title")
    s.set(2, 1, f"Importi in milioni di {currency} (le celle contengono le unità); in blu le ipotesi da cambiare, "
                "il resto si ricalcola.", "note")
    s.set(4, 1, "Punto di partenza (ultimi 12 mesi)", "bold")
    start = [("revenue", "Ricavi", base.revenue, "in_mln"), ("margin", "Margine operativo di oggi", base.margin, "in_pct"),
             ("invested_capital", "Capitale investito", base.invested_capital, "in_mln"),
             ("cash", "Cassa", base.cash, "in_mln"), ("debt", "Debito (leasing compresi)", base.debt, "in_mln"),
             ("shares", "Azioni (milioni)", base.shares, "in_mln"), ("price", f"Prezzo ({currency})", base.price, "in_num")]
    for key, label, v, style in start:
        s.set(INPUT_ROWS[key], 1, label)
        s.set(INPUT_ROWS[key], 2, float(v), style)
    s.set(13, 1, "Ipotesi (leve)", "bold")
    levers = [("growth", "Crescita dei ricavi (anni 1-5)", "in_pct"), ("target_margin", "Margine operativo obiettivo", "in_pct"),
              ("margin_years", "Anni per arrivarci", "in_int"),
              ("sales_to_capital", "Ricavi per unità di capitale investito", "in_num"),
              ("wacc", "Costo del capitale (anni 1-5)", "in_pct"), ("terminal_wacc", "Costo del capitale finale", "in_pct"),
              ("terminal_growth", "Crescita per sempre (dall'anno 11)", "in_pct"),
              ("terminal_roic", "ROIC nella fase stabile", "in_pct"), ("tax_rate", "Aliquota fiscale", "in_pct"),
              ("failure_prob", "Probabilità di fallimento", "in_pct"),
              ("distress_proceeds", "Recupero in caso di fallimento (x capitale investito)", "in_num")]
    for key, label, style in levers:
        s.set(INPUT_ROWS[key], 1, label)
        s.set(INPUT_ROWS[key], 2, float(getattr(lv, key)), style)

    s.row(TABLE_HEAD, 1, COLS, "bold")
    for i in range(YEARS):
        r, prev = FIRST + i, FIRST + i - 1
        t = i + 1
        row = tb.iloc[i]
        fade = f"MAX(0,MIN(1,(A{r}-{HIGH_GROWTH_YEARS})/{FADE}))"
        s.set(r, 1, t, "int")
        s.set(r, 2, float(row["crescita"]), "pct", f"{_b('growth')}+({_b('terminal_growth')}-{_b('growth')})*{fade}")
        s.set(r, 3, float(row["ricavi"]), "mln", f"{_b('revenue')}*(1+B{r})" if t == 1 else f"C{prev}*(1+B{r})")
        s.set(r, 4, float(row["margine"]), "pct",
              f"{_b('margin')}+({_b('target_margin')}-{_b('margin')})*MAX(0,MIN(1,A{r}/MAX({_b('margin_years')},1)))")
        s.set(r, 5, float(row["ebit"]), "mln", f"C{r}*D{r}")
        s.set(r, 6, float(row["nopat"]), "mln", f"IF(E{r}>0,E{r}*(1-{_b('tax_rate')}),E{r})")
        s.set(r, 7, float(row["reinvestimento"]), "mln",
              f"(C{r}-{_b('revenue')})/{_b('sales_to_capital')}" if t == 1 else f"(C{r}-C{prev})/{_b('sales_to_capital')}")
        s.set(r, 8, float(row["fcff"]), "mln", f"F{r}-G{r}")
        s.set(r, 9, float(row["costo_capitale"]), "pct", f"{_b('wacc')}+({_b('terminal_wacc')}-{_b('wacc')})*{fade}")
        disc = float(np.prod(1 + tb["costo_capitale"].iloc[:t]))
        s.set(r, 10, disc, "num", f"1+I{r}" if t == 1 else f"J{prev}*(1+I{r})")
        s.set(r, 11, float(row["valore_attuale"]), "mln", f"H{r}/J{r}")

    # fase stabile e valore (stessi passaggi di value())
    g_t, w_t = float(lv.terminal_growth), float(lv.terminal_wacc)
    rev11 = float(tb["ricavi"].iloc[-1]) * (1 + g_t)
    nopat11 = rev11 * float(tb["margine"].iloc[-1]) * (1 - float(lv.tax_rate))
    rr = g_t / float(lv.terminal_roic) if float(lv.terminal_roic) > 0 else 1.0
    fcff11 = nopat11 * (1 - rr)
    tv = fcff11 / (w_t - g_t) if w_t - g_t > 0 else np.nan
    pv_tv = tv / float(np.prod(1 + tb["costo_capitale"]))
    pv_sum = float(tb["valore_attuale"].sum())
    r0 = LAST + 2
    out = {}
    rows = [
        ("Fase stabile e valore", None, None, "bold"),
        ("Ricavi anno 11", rev11, f"C{LAST}*(1+{_b('terminal_growth')})", "mln"),
        ("NOPAT anno 11", nopat11, f"B{r0 + 1}*D{LAST}*(1-{_b('tax_rate')})", "mln"),
        ("Reinvestimento in fase stabile (crescita / ROIC)", rr,
         f"IF({_b('terminal_roic')}>0,{_b('terminal_growth')}/{_b('terminal_roic')},1)", "pct"),
        ("FCFF anno 11", fcff11, f"B{r0 + 2}*(1-B{r0 + 3})", "mln"),
        ("Valore finale (anno 10)", tv, f"IF({_b('terminal_wacc')}-{_b('terminal_growth')}>0,"
                                        f"B{r0 + 4}/({_b('terminal_wacc')}-{_b('terminal_growth')}),NA())", "mln"),
        ("Valore attuale del valore finale", pv_tv, f"B{r0 + 5}/J{LAST}", "mln"),
        ("Valore attuale dei flussi anni 1-10", pv_sum, f"SUM(K{FIRST}:K{LAST})", "mln"),
        ("Valore operativo (con la probabilità di fallimento)", float(res.operating_value),
         f"(B{r0 + 7}+B{r0 + 6})*(1-{_b('failure_prob')})+{_b('failure_prob')}*{_b('distress_proceeds')}*"
         f"{_b('invested_capital')}", "out_mln"),
        ("Valore dell'equity (+ cassa - debito)", float(res.equity_value),
         f"B{r0 + 8}+{_b('cash')}-{_b('debt')}", "out_mln"),
        (f"Valore per azione ({currency})", float(res.per_share), f"B{r0 + 9}/{_b('shares')}", "out_num"),
        ("Margine rispetto al prezzo", float(res.per_share) / base.price - 1 if base.price else np.nan,
         f"B{r0 + 10}/{_b('price')}-1", "out_pct"),
        ("Quota del valore che viene dal valore finale", float(res.tv_share), f"B{r0 + 6}/(B{r0 + 7}+B{r0 + 6})", "pct"),
    ]
    for i, (label, v, f, style) in enumerate(rows):
        r = r0 + i
        s.set(r, 1, label, "bold" if f is None else "text")
        if f is not None:
            s.set(r, 2, v, style, f)
            out[label] = (r, v)
    s.set(r0 + len(rows) + 1, 1, "Formule dell'app (modules/valuation_model.py): il valore per azione qui sopra deve "
                                 "coincidere con quello della scheda a parità di ipotesi.", "note")
    return s, {"per_share": (r0 + 10, float(res.per_share)), "rows": out}


def scenarios_sheet(base: Base, scenarios: dict[str, Levers], probabilities: dict[str, float], currency: str) -> Sheet:
    s = Sheet("Scenari", widths={1: 44, 2: 16, 3: 16, 4: 16})
    s.set(1, 1, "Scenari (valori calcolati dall'app)", "title")
    names = list(scenarios)
    s.row(3, 1, ["Ipotesi"] + names, "bold")
    keys = ["growth", "target_margin", "margin_years", "sales_to_capital", "wacc", "terminal_wacc", "terminal_growth",
            "terminal_roic", "tax_rate", "failure_prob"]
    from modules.valuation_model import LEVER_LABELS, PCT_LEVERS
    for i, k in enumerate(keys):
        s.set(4 + i, 1, LEVER_LABELS.get(k, k))
        for j, n in enumerate(names):
            s.set(4 + i, 2 + j, float(getattr(scenarios[n], k)), "pct" if k in PCT_LEVERS else "num")
    r = 4 + len(keys) + 1
    s.set(r, 1, f"Valore per azione ({currency})", "bold")
    s.set(r + 1, 1, "Probabilità", "bold")
    vals = {}
    for j, n in enumerate(names):
        vals[n] = float(value(base, scenarios[n]).per_share)
        s.set(r, 2 + j, vals[n], "out_num")
        s.set(r + 1, 2 + j, float(probabilities.get(n, 0.0)), "in_pct")
    first, last_col = 2, 1 + len(names)
    from modules.xlsx import col_letter
    a, b = col_letter(first), col_letter(last_col)
    weighted = sum(vals[n] * float(probabilities.get(n, 0.0)) for n in names)
    s.set(r + 3, 1, "Valore pesato per le probabilità", "bold")
    s.set(r + 3, 2, weighted, "out_num", f"SUMPRODUCT({a}{r}:{b}{r},{a}{r + 1}:{b}{r + 1})")
    s.set(r + 4, 1, "Prezzo di oggi")
    s.set(r + 4, 2, float(base.price), "num")
    s.set(r + 5, 1, "Margine rispetto al prezzo")
    s.set(r + 5, 2, weighted / base.price - 1 if base.price else np.nan, "out_pct", f"B{r + 3}/B{r + 4}-1")
    return s


def sensitivity_sheet(base: Base, lv: Levers, currency: str) -> Sheet:
    """Valore per azione al variare del costo del capitale (iniziale e finale insieme) e della crescita."""
    s = Sheet("Sensibilità", widths={1: 30, **{c: 12 for c in range(2, 9)}})
    s.set(1, 1, f"Valore per azione ({currency}) al variare delle ipotesi (calcolato dall'app)", "title")
    s.set(2, 1, "Righe: crescita dei ricavi anni 1-5. Colonne: costo del capitale (iniziale e finale spostati insieme).",
          "note")
    dw = [-0.015, -0.01, -0.005, 0.0, 0.005, 0.01, 0.015]
    dg = [-0.04, -0.02, 0.0, 0.02, 0.04]
    s.set(4, 1, "Crescita \\ costo del capitale", "bold")
    for j, w in enumerate(dw):
        s.set(4, 2 + j, float(lv.wacc) + w, "pct")
    for i, g in enumerate(dg):
        s.set(5 + i, 1, float(lv.growth) + g, "pct")
        for j, w in enumerate(dw):
            alt = replace(lv, growth=float(lv.growth) + g, wacc=float(lv.wacc) + w,
                          terminal_wacc=float(lv.terminal_wacc) + w)
            s.set(5 + i, 2 + j, float(value(base, alt).per_share), "num")
    s.set(11, 1, f"Prezzo di oggi: {base.price:,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", "."),
          "note")
    return s


def cost_of_capital_sheet(coc: dict) -> Sheet:
    s = Sheet("Costo del capitale", widths={1: 52, 2: 14})
    s.set(1, 1, "Costo del capitale (Damodaran) passo per passo", "title")
    rows = [
        (3, "Tasso privo di rischio", coc.get("risk_free"), "in_pct", None),
        (4, "Premio per il rischio azionario", coc.get("erp"), "in_pct", None),
        (5, f"Beta senza debito del gruppo «{coc.get('group') or 'tutte'}» ({coc.get('peers')} aziende)",
         coc.get("beta_unlevered"), "in_num", None),
        (6, "Debito (leasing compresi)", coc.get("debt"), "in_mln", None),
        (7, "Valore di mercato dell'equity", coc.get("equity"), "in_mln", None),
        (8, "Aliquota marginale", coc.get("marginal_tax"), "in_pct", None),
        (9, "Beta riadattato al debito = beta senza debito x (1 + (1 - t) D/E)", coc.get("beta"), "num",
         "B5*(1+(1-B8)*MAX(B6,0)/B7)"),
        (10, "Costo dell'equity = privo di rischio + beta x premio", coc.get("cost_of_equity"), "out_pct", "B3+B9*B4"),
        (11, f"Spread del rating sintetico ({coc.get('rating')})", coc.get("spread"), "in_pct", None),
        (12, "Costo del debito dopo le tasse", coc.get("cost_of_debt_after_tax"), "pct", "(B3+B11)*(1-B8)"),
        (13, "Peso dell'equity", coc.get("weight_equity"), "pct", "B7/(B7+MAX(B6,0))"),
        (14, "Costo del capitale (WACC)", coc.get("wacc"), "out_pct", "B13*B10+(1-B13)*B12"),
        (16, "Beta di regressione del titolo (solo confronto)", coc.get("beta_regression"), "num", None),
    ]
    for r, label, v, style, f in rows:
        s.set(r, 1, label)
        s.set(r, 2, None if v is None else float(v), style, f)
    s.set(18, 1, "Il DCF usa il costo del capitale delle ipotesi (foglio DCF), che parte da questo.", "note")
    return s


HISTORY_ROWS = [("revenue", "Ricavi", "mln"), ("gross_profit", "Utile lordo", "mln"),
                ("operating_income", "Utile operativo", "mln"), ("net_income", "Utile netto", "mln"),
                ("operating_cash_flow", "Flusso di cassa operativo", "mln"), ("capex", "Investimenti (capex)", "mln"),
                ("free_cash_flow", "Free cash flow", "mln"), ("total_debt", "Debito", "mln"), ("cash", "Cassa", "mln"),
                ("equity", "Patrimonio netto", "mln"), ("shares", "Azioni (milioni)", "mln"),
                ("dividends_paid", "Dividendi pagati", "mln"), ("buybacks", "Riacquisti di azioni", "mln")]


def history_sheet(annual: pd.DataFrame, sources: pd.DataFrame, currency: str) -> Sheet:
    s = Sheet("Bilanci", widths={1: 30, **{c: 12 for c in range(2, 14)}}, freeze=(3, 1))
    s.set(1, 1, f"Bilanci annuali (milioni di {currency}): ultima versione depositata", "title")
    annual = annual.tail(10)
    s.set(3, 1, "Voce", "bold")
    for j, day in enumerate(annual.index):
        s.set(3, 2 + j, pd.Timestamp(day).strftime("%Y-%m"), "bold")
    r = 4
    for key, label, style in HISTORY_ROWS:
        if key not in annual:
            continue
        s.set(r, 1, label)
        for j, v in enumerate(annual[key]):
            s.set(r, 2 + j, None if pd.isna(v) else float(v), style)
        r += 1
    if {"revenue", "operating_income"} <= set(annual.columns):
        s.set(r, 1, "Margine operativo")
        for j in range(len(annual)):
            from modules.xlsx import col_letter
            c = col_letter(2 + j)
            rev = annual["revenue"].iloc[j]
            v = annual["operating_income"].iloc[j] / rev if pd.notna(rev) and rev else None
            rr = [k for k, _, _ in HISTORY_ROWS if k in annual].index
            s.set(r, 2 + j, v, "pct", f"IFERROR({c}{4 + rr('operating_income')}/{c}{4 + rr('revenue')},\"\")")
        r += 1
    r += 1
    s.set(r, 1, "Fonte dell'esercizio", "bold")
    for j, day in enumerate(annual.index):
        if not sources.empty and day in sources.index:
            row = sources.loc[day].dropna().astype(str)
            kinds = sorted({_source_label(x) for x in row})
            s.set(r, 2 + j, " + ".join(kinds), "note")
    return s


def _source_label(src: str) -> str:
    if src.startswith("yahoo_fill"):
        return "Yahoo (riserva)"
    if src.startswith("manual"):
        return "a mano"
    if src.startswith("esef"):
        return "ESEF"
    if src.startswith("sec"):
        return "SEC"
    return src


LBO_ROWS = {"ebitda": 5, "ufcf": 6, "market_cap": 7, "net_debt": 8, "shares": 9, "tax": 10, "rate": 11,
            "premium": 14, "max_leverage": 15, "min_coverage": 16, "min_equity_share": 17, "exit_multiple_max": 18,
            "target_irr": 19}
LBO_TABLE = 34


def lbo_sheet(r, p: dict, currency: str) -> tuple[Sheet, dict]:
    """Foglio "LBO" con formule (stessi passaggi di modules.lbo._run): fonti e impieghi, debito anno per anno con
    tutto il flusso a ripagarlo, uscita al multiplo di mercato di oggi, rendimento del fondo. `r` = LBOResult."""
    s = Sheet("LBO", widths={1: 52, 2: 15, 3: 15, 4: 15, 5: 15})
    s.set(1, 1, "Test LBO: un fondo di private equity potrebbe comprarla a questo prezzo?", "title")
    s.set(2, 1, f"Importi in {currency} (le celle contengono le unità); in blu le ipotesi. Ipotesi prudenti: EBITDA e "
                "flusso di cassa fermi, uscita al multiplo di mercato di oggi, nessuna commissione.", "note")
    s.set(4, 1, "Punto di partenza (ultimi 12 mesi)", "bold")
    b = lambda k: f"$B${LBO_ROWS[k]}"  # noqa: E731
    tax, shares = r.tax, r.shares
    inputs = [("ebitda", "EBITDA", r.ebitda, "in_mln"),
              ("ufcf", "Flusso di cassa senza debito (FCF + interessi dopo le tasse)", r.ufcf, "in_mln"),
              ("market_cap", "Capitalizzazione di oggi", r.price * shares, "in_mln"),
              ("net_debt", "Debito esistente - cassa (da rifinanziare)", r.refinanced_debt, "in_mln"),
              ("shares", "Azioni", shares, "in_mln"), ("tax", "Aliquota fiscale", tax, "in_pct"),
              ("rate", "Costo del nuovo debito (Treasury 10 anni + spread)", r.rate, "in_pct")]
    s.set(13, 1, "Ipotesi del fondo", "bold")
    inputs += [("premium", "Premio sul prezzo di borsa", p["premium"], "in_pct"),
               ("max_leverage", "Debito massimo / EBITDA", p["max_leverage"], "in_num"),
               ("min_coverage", "Copertura minima degli interessi (EBITDA / interessi)", p["min_coverage"], "in_num"),
               ("min_equity_share", "Quota minima di capitale del fondo", p["min_equity_share"], "in_pct"),
               ("exit_multiple_max", "Multiplo EV/EBITDA di uscita massimo", p["exit_multiple_max"], "in_num"),
               ("target_irr", "Rendimento richiesto dal fondo", p["target_irr"], "in_pct")]
    for key, label, v, style in inputs:
        s.set(LBO_ROWS[key], 1, label)
        s.set(LBO_ROWS[key], 2, float(v), style)
    n = len(r.years)
    first, last = LBO_TABLE + 1, LBO_TABLE + n
    calc = [
        (21, "Fonti e impieghi", None, None, "bold"),
        (22, f"Prezzo di oggi per azione ({currency})", r.price, f"{b('market_cap')}/{b('shares')}", "num"),
        (23, f"Prezzo d'offerta per azione ({currency})", r.price * (1 + p["premium"]), f"B22*(1+{b('premium')})", "num"),
        (24, "Equity da comprare", r.purchase_equity, f"B23*{b('shares')}", "mln"),
        (25, "Totale impieghi (equity + debito rifinanziato)", r.uses, f"B24+{b('net_debt')}", "mln"),
        (26, "Nuovo debito (il più basso dei tre limiti)", r.new_debt,
         f"MAX(0,MIN({b('max_leverage')}*{b('ebitda')},{b('ebitda')}/({b('min_coverage')}*{b('rate')}),"
         f"(1-{b('min_equity_share')})*B25))", "mln"),
        (27, "Capitale messo dal fondo", r.sponsor_equity, "B25-B26", "out_mln"),
        (28, "Multiplo EV/EBITDA di mercato di oggi", r.entry_multiple, f"({b('market_cap')}+{b('net_debt')})/{b('ebitda')}", "num"),
        (29, "Multiplo di uscita", r.exit_multiple, f"MIN(B28,{b('exit_multiple_max')})", "num"),
    ]
    for row, label, v, f, style in calc:
        s.set(row, 1, label, "bold" if f is None else "text")
        if f is not None:
            s.set(row, 2, float(v), style, f)
    s.row(LBO_TABLE, 1, ["Anno", "Debito a inizio anno", "Interessi", "Flusso dopo gli interessi", "Debito a fine anno"],
          "bold")
    for i, y in enumerate(r.years):
        row, prev = first + i, first + i - 1
        s.set(row, 1, int(y["anno"]), "int")
        s.set(row, 2, float(y["debito_inizio"]), "mln", "B26" if i == 0 else f"E{prev}")
        s.set(row, 3, float(y["interessi"]), "mln", f"{b('rate')}*MAX(B{row},0)")
        s.set(row, 4, float(y["flusso_levered"]), "mln", f"{b('ufcf')}-C{row}*(1-{b('tax')})")
        s.set(row, 5, float(y["debito_fine"]), "mln", f"B{row}-D{row}")
    r0 = last + 2
    out_rows = [
        (r0, "Uscita", None, None, "bold"),
        (r0 + 1, "Enterprise value all'uscita", r.exit_ev, f"B29*{b('ebitda')}", "mln"),
        (r0 + 2, "Debito all'uscita (negativo = cassa)", r.exit_debt, f"E{last}", "mln"),
        (r0 + 3, "Equity del fondo all'uscita", r.exit_equity, f"B{r0 + 1}-B{r0 + 2}", "mln"),
        (r0 + 4, "Rendimento annuo del fondo (IRR)", r.irr,
         f"IF(B27<=0,NA(),IF(B{r0 + 3}<=0,-1,(B{r0 + 3}/B27)^(1/{n})-1))", "out_pct"),
        (r0 + 5, f"Prezzo massimo per azione al rendimento richiesto ({currency})", r.max_price, None, "out_num"),
    ]
    for row, label, v, f, style in out_rows:
        s.set(row, 1, label, "bold" if v is None else "text")
        if v is not None:
            s.set(row, 2, float(v) if np.isfinite(v) else None, style, f)
    s.set(r0 + 7, 1, "Il prezzo massimo è calcolato dall'app (ricerca per bisezione). In Excel: Dati > Analisi di "
                     "simulazione > Ricerca obiettivo, cella del rendimento = rendimento richiesto, cambiando il "
                     "prezzo d'offerta (B23, scritto come numero).", "note")
    s.set(r0 + 8, 1, "Formule dell'app: modules/lbo.py. Fonte del metodo: corsi di private equity (LBO) e M&A "
                     "(debito massimo dal vincolo di copertura).", "note")
    return s, {"irr": (r0 + 4, float(r.irr))}


def notes_sheet(lines: list[str]) -> Sheet:
    s = Sheet("Note e fonti", widths={1: 120})
    for i, line in enumerate(lines, 1):
        s.set(i, 1, line, "bold" if i == 1 else "text")
    return s


def company_model(d, ticker: str, scenario: str = "Base") -> bytes | None:
    """Il file Excel del modello di `ticker` (d = DashboardData) con le ipotesi salvate nel diario, se ci sono,
    altrimenti quelle proposte dal sistema. None se la valutazione non si può impostare (mancano ricavi o azioni)."""
    setup = d.valuation_setup(ticker)
    if setup is None:
        return None
    info, cur = d.info(ticker), d.currency(ticker)
    base, scenarios = setup["base"], setup["scenarios"]
    name = scenario if scenario in scenarios else next(iter(scenarios))
    lv = scenarios[name]
    as_of = pd.Timestamp(d.data.last_date) if d.data.last_date is not None else pd.Timestamp.now()
    title = (f"{info.get('name') or ticker} ({ticker}) - valutazione, scenario {name} - dati al "
             f"{as_of:%d/%m/%Y}")
    dcf, _ = dcf_sheet(base, lv, title, cur)
    lines = [f"{info.get('name') or ticker} ({ticker}): modello creato da QUANT_ENGINE il {pd.Timestamp.now():%d/%m/%Y}",
             f"Ipotesi: {'ultima valutazione salvata nel diario' if setup['saved'] else 'proposte dal sistema'}; "
             f"scenario nel foglio DCF: {name}.",
             "Bilanci: SEC (10-K/10-Q, XBRL companyfacts) per le aziende USA, ESEF (filings.xbrl.org) per le europee, "
             "Yahoo solo come riserva dove mancano (segnato nel foglio Bilanci). Prezzi: Yahoo.",
             "Ultimi 12 mesi = esercizio più recente o somma degli ultimi quattro trimestri (10-Q).",
             *[f"Correzione: {a}" for a in setup.get("adjustments", [])],
             *[f"Nota: {n}" for n in setup.get("notes", [])],
             "Non è una raccomandazione: è un modello con ipotesi esplicite da verificare e cambiare."]
    sheets = [dcf, scenarios_sheet(base, scenarios, setup["probabilities"], cur), sensitivity_sheet(base, lv, cur),
              cost_of_capital_sheet(setup["cost_of_capital"]),
              history_sheet(d.data.statements(ticker), d.data.statement_sources(ticker), cur)]
    buyout = d.lbo(ticker)
    if buyout is not None and buyout.ok:
        sheets.append(lbo_sheet(buyout, lbo_mod.settings(d.cfg), cur)[0])
    sheets.append(notes_sheet(lines))
    return workbook_bytes(sheets)
