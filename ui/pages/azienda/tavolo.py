"""Funzione TAV della scheda: il tavolo dei grandi investitori (modules/investor_panel.py tramite il servizio). In cima
la sintesi, sotto una riga per investitore con l'esito e il dettaglio richiudibile delle regole. Solo informazione."""
from __future__ import annotations

import html

import streamlit as st

from ui.components import badge, empty_state, fmt, fmt_html, notice, panel

SYMBOL = {"compra": "▲", "piace": "●", "quasi": "◐", "no": "✗", "fuori": "○", "dati": "?"}
STATUS = {"passed": ("passata", "pos", "✓"), "failed": ("mancata", "neg", "✗"), "missing": ("dato mancante", "off", "?")}


def _need(r: dict, cur: str) -> str:
    n = r.get("need")
    if not n:
        return ""
    if n["kind"] == "price":
        return f"passerebbe a {fmt(n['price'], 'price', currency=cur)} ({fmt(n['change'], 'pct', signed=True)})"
    if n["kind"] == "rank":
        return f"servirebbe essere nel {fmt(1 - n['rank'], 'pct', digits=0)} migliore (oggi " \
               f"{fmt(1 - (r['rank'] or 0), 'pct', digits=0)} dalla cima)"
    return n.get("text", "")


def _rules_html(m: dict, cur: str) -> str:
    rows = []
    for r in m["rules"]:
        text, tone, sym = STATUS[r["status"]]
        rows.append(f"<tr><td>{badge(text, tone, sym)}</td><td>{html.escape(r['text'])}</td>"
                    f"<td class='n'>{html.escape(r['value_text'])}</td><td>{html.escape(_need(r, cur))}</td></tr>")
    return ("<table class='qe-dense'><thead><tr><th>Regola</th><th>Condizione</th><th class='n'>Oggi</th>"
            "<th>Cosa dovrebbe cambiare</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def render_panel(d, ticker: str):
    with st.spinner("Il tavolo legge i numeri…"):
        p = d.investor_panel(ticker)
    cur = p.get("currency") or ""
    notice(p["note"], "info", title="Solo informazione:")
    if not p["members"]:
        empty_state("Nessun investitore nel catalogo", "Le strategie dei grandi investitori sono in modules/investors.py.")
        return
    s = p["summary"]
    with panel("Il tavolo in sintesi", right=f"dati al {fmt(p['data_as_of'], 'date')}"
               + (" · fuori dall'S&P 500: posizione ipotetica" if p["outside_index"] else "")):
        st.html("<div class='qe-tav-sum'>" + "".join(f"<p>{html.escape(x)}</p>" for x in s["lines"]) + "</div>")
    outcomes = p["outcomes"]
    for m in p["members"]:
        o = outcomes[m["outcome"]]
        label = f"{SYMBOL[m['outcome']]} {m['short']} — {o['label']}"
        with st.expander(label):
            st.html(f"<p>{html.escape(m['reason'][0].upper() + m['reason'][1:])}.</p>")
            if m["rules"]:
                st.html(_rules_html(m, cur))
            rk = m.get("ranking")
            if rk and rk.get("position"):
                st.caption(f"Classifica ({', '.join(rk['metrics']) or 'capitalizzazione'}): {rk['position']}ª su "
                           f"{rk['passing']} che passano le regole; ne tiene {rk['positions']}.")
            h = m.get("history")
            if h:
                st.caption(f"La strategia dal {h['since']}: {fmt(h['cagr'], 'pct', signed=True)} l'anno (1ª metà "
                           f"{fmt(h['first'], 'pct', signed=True)}, 2ª {fmt(h['second'], 'pct', signed=True)}; indice a "
                           f"pesi uguali nella 2ª {fmt(h['baseline_second'], 'pct', signed=True)}), Sharpe "
                           f"{fmt(h['sharpe'], 'num')}." + (" Backtest su dati di un giorno precedente." if h["stale"] else ""))
            st.html(f"<p class='qe-na'><b>Chi</b>: {html.escape(m['who'])}<br><b>Il metodo</b>: "
                    f"{html.escape(m['original'])}<br><b>Cosa manca nelle regole</b>: "
                    f"{html.escape(m['missing_in_rules'])}</p>")
    st.caption(p["history_note"])
