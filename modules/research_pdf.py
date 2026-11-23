"""
La scheda di ricerca di un'azienda in PDF: tesi, numeri principali col settore, il tuo giudizio, l'ultima
valutazione a scenari e il diario. Datata e con la data dei dati, da conservare o condividere.
Usa fpdf2; il font Arial del Mac per le lettere accentate, altrimenti Helvetica.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

from modules.metrics import METRIC_BY_KEY, format_value

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
KEY_METRICS = ["pe", "ev_ebit", "fcf_yield", "roic", "operating_margin", "revenue_growth", "net_debt_ebitda",
               "dividend_yield", "piotroski", "altman_z"]
LEVER_ROWS = ["growth", "target_margin", "sales_to_capital", "wacc", "terminal_growth", "terminal_roic"]


def _pdf():
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    regular, bold = FONT_DIR / "Arial.ttf", FONT_DIR / "Arial Bold.ttf"
    if regular.exists() and bold.exists():
        pdf.add_font("Main", "", str(regular))
        pdf.add_font("Main", "B", str(bold))
        pdf.font_name = "Main"
    else:  # senza TTF: Helvetica (solo latin-1)
        pdf.font_name = "Helvetica"
    pdf.add_page()
    return pdf


def _text(pdf, s: str) -> str:
    s = str(s or "")
    return s if pdf.font_name == "Main" else s.encode("latin-1", "replace").decode("latin-1")


def _heading(pdf, title: str):
    pdf.ln(3)
    pdf.set_font(pdf.font_name, "B", 13)
    pdf.set_text_color(31, 79, 153)
    pdf.cell(0, 8, _text(pdf, title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(pdf.font_name, "", 10)


def _para(pdf, label: str, body: str):
    if not body:
        return
    pdf.set_font(pdf.font_name, "B", 10)
    pdf.cell(0, 6, _text(pdf, label), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.font_name, "", 10)
    pdf.multi_cell(0, 5, _text(pdf, body), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _table(pdf, header: list[str], rows: list[list[str]], widths: list[float]):
    pdf.set_font(pdf.font_name, "B", 9)
    pdf.set_fill_color(240, 239, 236)
    for h, w in zip(header, widths):
        pdf.cell(w, 6, _text(pdf, h), border=0, fill=True)
    pdf.ln()
    pdf.set_font(pdf.font_name, "", 9)
    for row in rows:
        for v, w in zip(row, widths):
            pdf.cell(w, 5.5, _text(pdf, v))
        pdf.ln()
    pdf.ln(1)


def _price_chart(d, ticker: str) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    px = d.data.prices(ticker)
    px = px[px.index >= px.index[-1] - pd.DateOffset(years=5)] if not px.empty else px
    if px.empty:
        return None
    diary = d.valuation_diary(ticker)
    fig, ax = plt.subplots(figsize=(7.5, 2.6))
    ax.plot(px.index, px.values, color="#2a78d6", linewidth=1.4, label="Prezzo")
    if not diary.empty:
        ax.scatter(diary["created_at"], diary["expected_value"], color="#eb6834", s=30, zorder=3, label="Il tuo valore")
        ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def _verdict_section(pdf, d, ticker: str) -> None:
    """Il giudizio in poche righe: etichetta, regola, punteggi dei pilastri, cautele sui dati."""
    from modules import verdict as vd

    try:
        v = vd.assess_ticker(d, ticker)
    except Exception:  # noqa: BLE001 - senza giudizio il PDF resta utile
        return
    _heading(pdf, f"Giudizio: {v.label}")
    pdf.set_font(pdf.font_name, "", 9)
    pdf.multi_cell(0, 4.5, _text(pdf, v.rule), new_x="LMARGIN", new_y="NEXT")
    rows = [[p.label, "–" if p.score is None else f"{p.score:.2f}".replace(".", ","), p.grade]
            for p in v.pillars.values()]
    _table(pdf, ["Pilastro", "Punteggio (0-1)", "Lettura"], rows, [70, 35, 75])
    for line in [*v.alarms, *v.checks, *v.data_notes, *v.changes[:2]]:
        pdf.multi_cell(0, 4.5, _text(pdf, "· " + line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.font_name, "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, _text(pdf, "Il giudizio applica soglie fisse ai dati: nella prova sul passato le «Candidate» "
                                    "non hanno battuto l'indice. È una checklist, non un segnale."),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def _peers_table(pdf, d, ticker: str) -> None:
    """Multipli e redditività contro i concorrenti più grandi dello stesso gruppo."""
    try:
        peers, group, _ = d.peers(ticker, n=8)
    except Exception:  # noqa: BLE001
        return
    if peers is None or peers.empty:
        return
    m = d.metrics()
    names = [t for t in peers["ticker"] if t in m.index][:8]
    if ticker in m.index and ticker not in names:
        names = [ticker] + names[:7]
    cols = [("pe", "P/E"), ("ev_ebit", "EV/EBIT"), ("fcf_yield", "Rend. FCF"), ("operating_margin", "Marg. op."),
            ("roic", "ROIC"), ("revenue_cagr_3y", "Crescita 3a")]
    cols = [(k, lbl) for k, lbl in cols if k in m.columns]
    _heading(pdf, f"Concorrenti ({group})")
    rows = [[("> " if t == ticker else "") + t] + [format_value(k, m.at[t, k]) for k, _ in cols] for t in names]
    _table(pdf, ["Ticker"] + [lbl for _, lbl in cols], rows, [30] + [25] * len(cols))


def _system_valuation(pdf, d, ticker: str, currency: str) -> None:
    """Senza una valutazione nel diario: gli scenari proposti dal sistema (punto di partenza, non un parere)."""
    from modules.valuation_model import value

    try:
        setup = d.valuation_setup(ticker)
    except Exception:  # noqa: BLE001
        return
    if setup is None:
        return
    base, sc, prob = setup["base"], setup["scenarios"], setup["probabilities"]
    vals = {n: float(value(base, lv).per_share) for n, lv in sc.items()}
    _heading(pdf, "Valutazione di partenza del sistema (DCF a leve)")
    rows = [[n, f"{lv.growth:.1%}", f"{lv.target_margin:.1%}", f"{lv.wacc:.1%}", f"{vals[n]:,.2f}",
             f"{prob.get(n, 0):.0%}"] for n, lv in sc.items()]
    _table(pdf, ["Scenario", "Crescita 1-5", "Margine obiettivo", "Costo capitale", f"Valore ({currency})",
                 "Probabilità"], rows, [30, 28, 34, 30, 32, 26])
    pdf.set_font(pdf.font_name, "", 8)
    pdf.multi_cell(0, 4, _text(pdf, "Ipotesi proposte dal sistema dai bilanci e dal settore: vanno riviste. Il modello "
                                    "completo con le formule è nell'export Excel."), new_x="LMARGIN", new_y="NEXT")


def company_pdf(d, ticker: str) -> bytes:
    """PDF della scheda di ricerca di `ticker` (d = DashboardData)."""
    info = d.info(ticker)
    research = d.research(ticker)
    px = d.data.prices(ticker)
    price = float(px.iloc[-1]) if not px.empty else np.nan
    currency = d.currency(ticker)
    pdf = _pdf()

    pdf.set_font(pdf.font_name, "B", 18)
    pdf.cell(0, 10, _text(pdf, f"{info.get('name') or ticker} ({ticker})"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.font_name, "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 5, _text(pdf, f"{info.get('sector') or ''} · prezzo {price:,.2f} {currency} al "
                              f"{pd.Timestamp(d.data.last_date):%d/%m/%Y} · scheda del {pd.Timestamp.now():%d/%m/%Y}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    _verdict_section(pdf, d, ticker)
    _heading(pdf, "La tesi")
    if research:
        _para(pdf, "In una frase", research.get("one_liner"))
        _para(pdf, "La storia", research.get("story"))
        _para(pdf, "Cosa deve succedere perché io abbia ragione", research.get("catalysts"))
        _para(pdf, "Rischi", research.get("risks"))
        _para(pdf, "Cosa mi farebbe cambiare idea", research.get("change_mind"))
        if research.get("review_date"):
            _para(pdf, "Prossima revisione", f"{pd.Timestamp(research['review_date']):%d/%m/%Y}")
    else:
        pdf.multi_cell(0, 5, _text(pdf, "Scheda di ricerca non ancora compilata."), new_x="LMARGIN", new_y="NEXT")

    chart = _price_chart(d, ticker)
    if chart:
        _heading(pdf, "Prezzo negli ultimi 5 anni")
        pdf.image(io.BytesIO(chart), w=180)

    _heading(pdf, "Numeri principali (ultimi 12 mesi)")
    cm = d.company_metrics(ticker).set_index("key")
    keys = KEY_METRICS + [k for k in cm.index if k.startswith("my_") and k != "my_checklist"]
    rows = [[METRIC_BY_KEY[k].label, format_value(k, cm.at[k, "value"]), format_value(k, cm.at[k, "sector_median"]),
             f"{cm.at[k, 'rank_sector']:.0%}" if np.isfinite(cm.at[k, "rank_sector"]) else "–"]
            for k in keys if k in cm.index]
    _table(pdf, ["Metrica", "Valore", "Mediana settore", "Nel settore"], rows, [80, 35, 40, 25])

    items = d.checklist_items()
    scores = d.checklist(ticker)
    if scores:
        _heading(pdf, "Il mio giudizio")
        rows = [[q, f"{scores[int(i)]:.0f} / 5" if int(i) in scores else "–"]
                for i, q in zip(items["id"], items["question"])]
        _table(pdf, ["Domanda", "Voto"], rows, [150, 30])
        from modules.method import checklist_score

        total = checklist_score(scores, items)
        if total is not None:
            _para(pdf, "Voto complessivo", f"{total:.1f} / 5")

    _peers_table(pdf, d, ticker)
    diary = d.valuation_diary(ticker)
    if diary.empty:
        _system_valuation(pdf, d, ticker, currency)
    if not diary.empty:
        last = diary.iloc[-1]
        _heading(pdf, f"Ultima valutazione ({last['created_at']:%d/%m/%Y}, prezzo allora {last['price']:,.2f})")
        names = list(last["scenarios"])
        from modules.valuation_model import LEVER_LABELS, PCT_LEVERS

        rows = []
        for k in LEVER_ROWS:
            vals = [last["scenarios"][n].get(k) for n in names]
            rows.append([LEVER_LABELS[k]] + [f"{v:.1%}" if k in PCT_LEVERS else f"{v:.2f}" for v in vals])
        rows.append(["Probabilità"] + [f"{last['probabilities'].get(n, 0):.0%}" for n in names])
        rows.append(["Valore per azione"] + [f"{last['per_scenario'].get(n, np.nan):,.2f}" for n in names])
        _table(pdf, ["Leva"] + names, rows, [72] + [36] * len(names))
        sim = last["simulation"] or {}
        text = f"Valore atteso {last['expected_value']:,.2f} (margine allora {last['margin_then']:+.0%})."
        if sim:
            text += (f" Simulazione: 10°-90° percentile {sim.get('p10', np.nan):,.0f} - {sim.get('p90', np.nan):,.0f}, "
                     f"valore sopra il prezzo nel {sim.get('prob_above_price', np.nan):.0%} dei casi.")
        pdf.multi_cell(0, 5, _text(pdf, text), new_x="LMARGIN", new_y="NEXT")
        if len(diary) > 1:
            _heading(pdf, "Diario delle valutazioni")
            rows = [[f"{r.created_at:%d/%m/%Y}", f"{r.price:,.2f}", f"{r.expected_value:,.2f}", f"{r.margin_then:+.0%}",
                     f"{r.return_since:+.0%}"] for r in diary.itertuples()]
            _table(pdf, ["Data", "Prezzo allora", "Tuo valore", "Margine allora", "Prezzo da allora"], rows,
                   [30, 35, 35, 40, 40])

    pdf.ln(4)
    pdf.set_font(pdf.font_name, "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, _text(pdf, "Generato da QUANT_ENGINE con dati SEC (point-in-time) e prezzi Yahoo. "
                                    "Valutazioni e giudizi sono dell'autore. Non è una raccomandazione di investimento."),
                   new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
