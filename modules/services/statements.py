"""Bilanci della scheda pronti da mostrare (funzioni FA ed ERN): periodi con etichette leggibili, serie per i grafici,
tabella delle voci con il tipo di ogni riga, segni sulle celle che non vengono da un deposito ufficiale, link al 10-K /
10-Q di ogni colonna. Una sola definizione per la webapp Streamlit (ui/pages/azienda/bilanci.py la importa) e per
l'interfaccia web (api/: /companies/{t}/financials). Valori grezzi: la formattazione la fa chi mostra."""
from __future__ import annotations

import pandas as pd

from modules.formats import fmt
from modules.services.base import Service
from modules.services.company import FINANCIAL_ROWS, RATIO_ROWS

MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
PCT_ROWS = ("revenue_growth", "gross_margin", "operating_margin", "net_margin", "fcf_margin")
TABLE_PERIODS = 12
VIEWS = {"annual": "Esercizi", "quarterly_single": "Trimestri singoli", "quarterly_ttm": "Ultimi 12 mesi a ogni trimestre"}
CHART_PERIODS = {"annual": 11, "quarterly_single": 16, "quarterly_ttm": 16}

# fonti che non sono un deposito ufficiale: segnate nella tabella (simbolo, classe CSS, spiegazione)
SOURCE_MARKS = {"yahoo_fill": ("*", "src-fb", "Dato di riserva da Yahoo: manca nel deposito ufficiale (SEC/ESEF), da "
                                                "verificare sul documento"),
                "manual": ("✎", "src-man", "Inserito a mano (pagina Inserisci dati)")}

# grafici della funzione (titolo, serie): stessi della scheda Streamlit
CHARTS = [
    {"key": "results", "title": "Ricavi, utili e cassa", "type": "bars",
     "series": [("revenue", "Ricavi"), ("net_income", "Utile netto"), ("free_cash_flow", "Free cash flow")]},
    {"key": "margins", "title": "Margini (% dei ricavi)", "type": "lines",
     "series": [("gross_margin", "Lordo"), ("operating_margin", "Operativo"), ("net_margin", "Netto"),
                ("fcf_margin", "FCF")]},
    {"key": "balance", "title": "Debito, cassa e patrimonio", "type": "bars",
     "series": [("total_debt", "Debito"), ("cash", "Cassa"), ("equity", "Patrimonio netto")]},
    {"key": "returned", "title": "Soldi restituiti agli azionisti", "type": "bars",
     "series": [("dividends_paid", "Dividendi"), ("buybacks", "Riacquisti di azioni")]},
]

NOTES = {
    "quarterly_single": "Ogni colonna è un solo trimestre (dal 10-Q; il quarto = esercizio meno i primi nove mesi); stato "
                        "patrimoniale alla chiusura; crescita sullo stesso trimestre dell'anno prima; azioni = media "
                        "diluita del trimestre (manca nel quarto). Nella tabella gli ultimi 12 trimestri.",
    "quarterly_ttm": "Ogni colonna: ultimi 12 mesi alla chiusura del trimestre (dai 10-Q; gli esercizi dai 10-K); "
                     "crescita sullo stesso trimestre dell'anno prima. Nella tabella gli ultimi 12 trimestri.",
    "annual": "Esercizi dai 10-K (il mese è indicato se l'esercizio non chiude a dicembre); \"12 mesi a\": ultimi "
              "quattro trimestri dai 10-Q.",
}


def month_label(d, short_year: bool = True) -> str:
    """"giu 25" (o "giu 2025")."""
    d = pd.Timestamp(d)
    return f"{MONTHS[d.month - 1]} {d:%y}" if short_year else f"{MONTHS[d.month - 1]} {d.year}"


def period_labels(fin: pd.DataFrame, quarterly: bool) -> list[str]:
    """Etichette dei periodi: esercizio = anno (con il mese se non chiude a dicembre), trimestre = "giu 25", ultimi
    12 mesi = "12 mesi a giu 25". Etichette doppie (due chiusure nello stesso mese) ricevono la data intera."""
    ttm = fin["ttm"].astype(bool) if "ttm" in fin else pd.Series(False, index=fin.index)
    out = []
    for d, t in zip(fin.index, ttm):
        d = pd.Timestamp(d)
        if quarterly:
            out.append(month_label(d))  # la vista dice già se sono trimestri singoli o ultimi 12 mesi
        elif t:
            out.append(f"12 mesi a {month_label(d)}")
        else:
            out.append(str(d.year) if d.month == 12 else month_label(d, short_year=False))
    seen = {}
    for i, lab in enumerate(out):
        if lab in seen:
            out[i] = f"{lab} ({pd.Timestamp(fin.index[i]):%d/%m})"
        seen[lab] = i
    return out


def shares_summary(shares: pd.Series, labels: list[str]) -> str:
    """Frase sulla variazione delle azioni nel periodo: "in calo: da 1.110,0 a 1.098,9 milioni (−1,0%) tra 2015 e 2025"."""
    s = pd.Series(shares.to_numpy(dtype=float), index=labels).dropna()
    if len(s) < 2 or s.iloc[0] <= 0:
        return ""
    chg = s.iloc[-1] / s.iloc[0] - 1
    verb = "in calo" if chg < -0.005 else "in aumento" if chg > 0.005 else "quasi invariate"
    return (f"{verb}: da {fmt(s.iloc[0] / 1e6, 'num', digits=1)} a {fmt(s.iloc[-1] / 1e6, 'num', digits=1)} milioni "
            f"({fmt(chg, 'pct', signed=True)}) tra {s.index[0]} e {s.index[-1]}")


def row_kind(metric: str) -> str:
    """Come mostrare una riga della tabella: "pct", "mult" (debito/patrimonio), "shares_mln" o "money_mln"."""
    return "pct" if metric in PCT_ROWS else "mult" if metric == "debt_to_equity" else \
        "shares_mln" if metric == "shares" else "money_mln"


def row_label(metric: str) -> str:
    label = {**FINANCIAL_ROWS, **RATIO_ROWS}[metric]
    if metric == "shares":  # "Azioni (diluite)" -> "Azioni (diluite, mln)"
        return label[:-1] + ", mln)" if label.endswith(")") else f"{label} (mln)"
    return label


def table_rows(fin: pd.DataFrame) -> list[str]:
    return [r for r in list(FINANCIAL_ROWS) + list(RATIO_ROWS) if r in fin.columns]


def source_mark_keys(fin: pd.DataFrame, sources: pd.DataFrame) -> dict[str, list[str]]:
    """Per ogni riga della tabella, la chiave di SOURCE_MARKS di ogni periodo ("" = fonte ufficiale o derivato)."""
    src = sources.reindex(index=fin.index) if not sources.empty else pd.DataFrame(index=fin.index)

    def mark(r, day):
        if r not in src.columns or pd.isna(src.at[day, r]):
            return ""
        v = str(src.at[day, r])
        return next((k for k in SOURCE_MARKS if v.startswith(k)), "")

    return {r: [mark(r, day) for day in fin.index] for r in table_rows(fin)}


class StatementsService(Service):
    def financial_statements(self, ticker: str, view: str = "annual") -> dict:
        """Bilanci pronti da mostrare per `view` = "annual" (esercizi), "quarterly_ttm" (ultimi 12 mesi a ogni
        trimestre) o "quarterly_single" (trimestri singoli). {"empty": True, ...} se non ci sono dati."""
        if view not in VIEWS:
            raise ValueError(f"Vista sconosciuta: {view} (ammesse: {', '.join(VIEWS)})")
        ctx = self.ctx
        single, quarterly = view == "quarterly_single", view != "annual"
        fin = ctx.quarterly(ticker) if single else ctx.financials(ticker, quarterly=quarterly)
        out = {"ticker": ticker, "view": view, "view_label": VIEWS[view], "currency": ctx.currency(ticker),
               "views": [{"value": k, "label": v} for k, v in VIEWS.items()]}
        if fin.empty:
            why = ("I trimestri si ricavano dai 10-Q della SEC al prossimo aggiornamento dei dati. Le aziende europee "
                   "pubblicano solo gli esercizi: guarda \"Esercizi\"." if single else
                   "La SEC non ha bilanci per questo ticker (aziende europee senza deposito, o aggiunte da te): si "
                   "possono inserire a mano nella pagina Inserisci dati.")
            return {**out, "empty": True, "why": why}
        fin = fin.tail(CHART_PERIODS[view])
        labels = period_labels(fin, quarterly)
        by_day = {} if single else ctx.financial_links(ticker, quarterly=quarterly)
        periods = []
        for lab, day in zip(labels, fin.index):
            link = by_day.get(day)
            periods.append({"label": lab, "end": day, "ttm": bool(fin.at[day, "ttm"]) if "ttm" in fin else False,
                            "link": {"url": link[0], "title": link[1]} if link and link[0] else None})
        charts = []
        for c in CHARTS:
            series = [{"key": k, "label": lab, "values": fin[k].tolist()} for k, lab in c["series"]
                      if k in fin and fin[k].notna().any()]
            if series:
                charts.append({"key": c["key"], "title": c["title"], "type": c["type"],
                               "kind": "pct" if c["type"] == "lines" else "money", "series": series})
        shares = None
        if "shares" in fin and fin["shares"].notna().any():
            shares = {"values": fin["shares"].tolist(), "summary": shares_summary(fin["shares"], labels)}
        marks = {} if single else source_mark_keys(fin, ctx.financial_sources(ticker, quarterly=quarterly))
        first = max(0, len(labels) - TABLE_PERIODS)  # nella tabella gli ultimi 12 periodi
        rows = [{"key": r, "label": row_label(r), "kind": row_kind(r), "values": fin[r].tolist()[first:],
                 "marks": (marks.get(r) or [""] * len(fin))[first:]} for r in table_rows(fin)]
        used = {m for r in rows for m in r["marks"] if m}
        return {**out, "empty": False, "periods": periods, "table_from": first, "charts": charts, "shares": shares,
                "rows": rows, "marks": {k: {"symbol": v[0], "why": v[2]} for k, v in SOURCE_MARKS.items() if k in used},
                "note": NOTES[view]}
