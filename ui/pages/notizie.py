"""Pagina Notizie: flusso in tempo (quasi) reale dai feed RSS pubblici (modules/news.py) e, in una seconda scheda, gli
8-K della SEC (documenti ufficiali).

Per altre pagine (pannello "Ultime notizie" nel Monitor o nella scheda azienda):
    from ui.pages.notizie import render_latest_news, latest_news
    render_latest_news(d, tickers=None, limit=8)          # tabella fitta pronta (None = tutte le notizie)
    render_latest_news(d, tickers=["AAPL"], limit=10)     # solo le notizie collegate ad AAPL
    df = latest_news(d, tickers=["AAPL"], limit=10)       # solo i dati (colonne di modules.news.COLUMNS)
Il thread che legge i feed parte alla prima chiamata ed è uno solo per tutto il server.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st

from modules import news as news_mod
from modules.config import PROJECT_ROOT
from ui import nav
from ui.common import data
from ui.components import notice

ALL_GROUPS = news_mod.GROUPS


# ---------------------------------------------------------------- 8-K (documenti ufficiali)
def _news_table(news: pd.DataFrame, height: int = 520):
    if news.empty:
        st.caption("Nessun 8-K nel periodo (si scaricano con \"Aggiorna dati\" e con l'aggiornamento automatico).")
        return
    news = news.assign(when=pd.to_datetime(news["when"]))
    shown = news.assign(what=np.where(news["important"], "⚠ " + news["what"], news["what"]))
    st.dataframe(shown[["when", "ticker", "name", "what", "url"]], hide_index=True, width="stretch", height=height,
                 column_config={"when": st.column_config.DatetimeColumn("Quando", format="DD/MM HH:mm"),
                                "ticker": "Ticker", "name": "Azienda", "what": "Cosa",
                                "url": st.column_config.LinkColumn("SEC", display_text="apri")})


def _filings_section(d):
    from modules.sec_filings import ITEM_LABELS

    st.caption("Le notizie ufficiali: gli 8-K che le aziende devono depositare alla SEC entro 4 giorni da ogni evento "
               "rilevante (risultati, acquisizioni, dirigenti, debito, revisore...). L'ora è quella di accettazione "
               "della SEC (New York). ⚠ = da guardare subito.")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
    days = c1.selectbox("Periodo", [1, 3, 7, 14, 30], index=2, format_func=lambda n: f"ultimi {n} giorni")
    mine = c2.toggle("Solo le mie aziende", key="news_mine")
    only_imp = c3.toggle("Solo ⚠", key="news_imp")
    kinds = c4.multiselect("Tipo di evento", list(ITEM_LABELS), format_func=lambda k: f"{k} {ITEM_LABELS[k]}",
                           placeholder="Tutti")
    page = d.filings_page(days, mine, only_imp, kinds)
    news = pd.DataFrame(page["items"], columns=["when", "ticker", "name", "form", "what", "important", "url", "codes"])
    st.caption(f"{len(news)} depositi di {page['companies']} aziende.")
    _news_table(news, height=640)


# ---------------------------------------------------------------- flusso dai feed RSS
@st.cache_resource(show_spinner=False)
def _feed(news_cfg_json: str):
    """Un solo NewsFeed (con il suo thread) per configurazione, condiviso da tutte le sessioni."""
    return news_mod.build_feed({"news": json.loads(news_cfg_json)}, PROJECT_ROOT)


def _followed(d) -> list[str]:
    """Le mie aziende: seguite, posizioni aperte, con una soglia di prezzo (come in Oggi e nella nuova interfaccia)."""
    from modules.alerts import followed_tickers

    return sorted(followed_tickers(d.db))


def _name(d, ticker: str) -> str:
    try:
        return str(d.info(ticker).get("name") or "")
    except Exception:  # noqa: BLE001
        return ""


def news_feed(d):
    """Il NewsFeed del server, con l'universo per collegare i titoli e i feed delle aziende seguite aggiornati;
    None se le notizie sono spente (config.yaml, news.enabled)."""
    cfg = d.cfg.get("news") or {}
    if not cfg.get("enabled", True):
        return None
    feed = _feed(json.dumps(cfg, sort_keys=True, default=str))
    comp = d.companies
    names = ({t: str(n) for t, n in comp["name"].dropna().items() if t in set(d.tickers)}
             if not comp.empty and "name" in comp else {})
    feed.set_universe(names, cfg.get("aliases") or {})
    followed = {t: _name(d, t) for t in _followed(d)}
    feed.add_feeds(news_mod.company_feeds(cfg, followed), "aziende")
    return feed


def latest_news(d, tickers=None, limit: int = 10) -> pd.DataFrame:
    """Ultime notizie (tutte o di alcuni ticker): published/first_seen in UTC, source, feed, group, title, link,
    tickers (separati da spazio), lang."""
    feed = news_feed(d)
    if feed is None:
        return pd.DataFrame(columns=news_mod.COLUMNS)
    table, _, _ = feed.snapshot()
    return news_mod.latest(table, tickers, limit)


def _when(ts: pd.Timestamp, today) -> str:
    local = ts.to_pydatetime().astimezone()  # ora locale del Mac (con l'ora legale giusta per quel giorno)
    return f"{local:%H:%M}" if local.date() == today else f"{local:%d/%m %H:%M}"


def _ticker_links(tickers: str) -> str:
    return " ".join(f'<a href="notizie?apri={html.escape(t)}" target="_self" title="Apri la scheda di {html.escape(t)}">'
                    f"{html.escape(t)}</a>" for t in str(tickers or "").split())


def news_html(df: pd.DataFrame, new_since=None, max_rows: int = 200, show_source: bool = True) -> str:
    """Elenco fitto: ora locale, fonte, ticker (link alla scheda), titolo (link all'articolo, nuova scheda).
    Le righe con `first_seen` dopo `new_since` sono evidenziate (●)."""
    today = datetime.now().astimezone().date()
    head = '<tr><th></th><th>Ora</th>' + ('<th>Fonte</th>' if show_source else "") + '<th>Ticker</th><th>Titolo</th></tr>'
    rows = []
    for r in df.head(max_rows).itertuples(index=False):
        is_new = new_since is not None and r.first_seen > new_since
        title = html.escape(r.title)
        link = html.escape(r.link, quote=True)
        style = ' style="color:var(--accent)"' if is_new else ""
        rows.append(
            f'<tr><td{style}>{"●" if is_new else ""}</td><td>{_when(r.published, today)}</td>'
            + (f'<td title="{html.escape(r.feed)}">{html.escape(str(r.source))[:22]}</td>' if show_source else "")
            + f'<td class="tk">{_ticker_links(r.tickers)}</td>'
            f'<td class="fill" title="{title}"><a href="{link}" target="_blank" rel="noopener noreferrer"{style}>'
            f"{title}</a></td></tr>")
    return f'<table class="qe-dense qe-news">{head}{"".join(rows)}</table>'


NEWS_CSS = """<style>
table.qe-news a { color: inherit; text-decoration: none; }
table.qe-news a:hover { text-decoration: underline; }
table.qe-news td.tk a { color: var(--info); }
</style>"""


def render_latest_news(d, tickers=None, limit: int = 8, empty_text: str = "Nessuna notizia per ora (i feed si "
                       "leggono in sottofondo: le prime arrivano entro un paio di minuti)."):
    """Pannello "Ultime notizie" per Monitor e scheda azienda (va dentro un `panel(...)` o dove serve)."""
    df = latest_news(d, tickers, limit)
    if df.empty:
        st.caption(empty_text)
        return
    st.html(NEWS_CSS + news_html(df, show_source=True))


def _status_line(updated, status: pd.DataFrame) -> str:
    if status.empty:
        return "nessun feed configurato (config.yaml, sezione news)"
    ok = int(status["ok"].sum())
    err = int(status["error"].notna().sum())
    waiting = len(status) - ok - err
    when = f"aggiornato alle {updated:%H:%M:%S}" if updated else "primo giro dei feed in corso"
    return f"{when} · {ok} fonti attive, {err} in errore" + (f", {waiting} in attesa" if waiting else "")


def _live_section(d):
    every = int((d.cfg.get("news") or {}).get("ui_refresh_seconds", 30))

    @st.fragment(run_every=every)
    def live():
        feed = news_feed(d)
        if feed is None:
            st.caption("Notizie spente in config.yaml (news.enabled: false).")
            return
        table, updated, status = feed.snapshot()
        followed = _followed(d)

        c1, c2, c3, c4, c5 = st.columns([2.6, 0.9, 1.5, 0.8, 1.5], vertical_alignment="bottom")
        view = c1.segmented_control("Notizie", ["Tutte", "Le mie aziende", "Un ticker"], default="Tutte",
                                    key="nw_view", label_visibility="collapsed") or "Tutte"
        ticker = c2.text_input("Ticker", key="nw_ticker", placeholder="ticker", label_visibility="collapsed",
                               disabled=view != "Un ticker").strip().upper()
        groups = c3.multiselect("Fonte", ALL_GROUPS, key="nw_groups", placeholder="Tutte le fonti",
                                label_visibility="collapsed")
        lang = c4.selectbox("Lingua", ["tutte", "en", "it"], key="nw_lang", label_visibility="collapsed",
                            format_func=lambda x: {"tutte": "EN+IT", "en": "EN", "it": "IT"}[x])
        query = c5.text_input("Cerca", key="nw_query", placeholder="cerca nel titolo", label_visibility="collapsed")

        # evidenza: le notizie arrivate dopo l'ultimo giro visto in questa sessione (alla prima apertura nessuna)
        newest = table["first_seen"].max() if not table.empty else None
        mark, seen = st.session_state.get("nw_mark", (None, None))
        if seen is None:
            mark = newest
        elif newest is not None and newest > seen:
            mark = seen
        st.session_state["nw_mark"] = (mark, newest)

        tickers = followed if view == "Le mie aziende" else [ticker] if view == "Un ticker" and ticker else None
        view_df = news_mod.filter_table(table, tickers, groups, None if lang == "tutte" else lang, query)

        n_new = int((view_df["first_seen"] > mark).sum()) if mark is not None and not view_df.empty else 0
        st.caption(f"{_status_line(updated, status)} · {len(view_df)} notizie"
                   + (f" · ● {n_new} nuove dall'ultimo aggiornamento" if n_new else "")
                   + (" · le mie aziende: nessuna (segui un'azienda o aggiungila al portafoglio)"
                      if view == "Le mie aziende" and not followed else ""))
        if view_df.empty:
            st.caption("Nessuna notizia con questi filtri." if not table.empty else
                       "Le prime notizie arrivano entro un paio di minuti (i feed si leggono in sottofondo).")
        else:
            st.html(NEWS_CSS + news_html(view_df, new_since=mark, max_rows=250))
            if len(view_df) > 250:
                st.caption(f"Mostrate le 250 più recenti su {len(view_df)}: restringi con i filtri.")
        with st.expander("Stato delle fonti"):
            if not status.empty:
                shown = status.assign(last_ok=[_when(pd.Timestamp(x), datetime.now().date()) if pd.notna(x) else "–"
                                               for x in status["last_ok"]])
                st.dataframe(shown[["feed", "group", "tickers", "ok", "error", "failures", "items", "last_ok"]],
                             hide_index=True, width="stretch",
                             column_config={"feed": "Fonte", "group": "Gruppo", "tickers": "Ticker", "ok": "OK",
                                            "error": "Errore", "failures": "Tentativi falliti",
                                            "items": "Voci nel feed", "last_ok": "Ultima lettura"})

    live()


def _x_section(d):
    from streamlit.components.v1 import html as component_html

    from ui.common import TERMINAL

    cfg = d.cfg.get("news") or {}
    height = int(cfg.get("x_height", 600))
    st.caption("Account X scelti da te, con il riquadro ufficiale incorporabile di X (gratis, nessuna API, nessun "
               "post raccolto o salvato dall'app). Limiti: non è in tempo reale (si aggiorna ricaricando la pagina); "
               "X può chiedere di essere collegati nel browser e mostrare solo una parte dei post, o nessuno. Se il "
               "riquadro resta vuoto usa il link diretto al profilo sotto il nome.")
    accounts = d.x_accounts()["accounts"]
    with st.form("nw_x_form", clear_on_submit=True, border=False):  # Invio = Aggiungi
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        new = c1.text_input("Aggiungi un account", key="nw_x_new", placeholder="@nome, nome o link x.com/nome")
        added = c2.form_submit_button("Aggiungi", width="stretch")
    if added and new.strip():
        try:
            d.add_x_account(new)
            st.rerun()
        except ValueError as e:
            notice(str(e), "warn")
    if not accounts:
        st.caption("Nessun account: aggiungine uno qui sopra (l'elenco resta nelle preferenze dell'app, accanto ai dati).")
        return
    cols = st.columns(min(2, len(accounts)))
    for i, user in enumerate(accounts):
        with cols[i % len(cols)]:
            h1, h2 = st.columns([4, 1], vertical_alignment="center")
            h1.markdown(f"**@{user}** · [apri il profilo su X](https://x.com/{user})")
            if h2.button("Togli", key=f"nw_x_del_{user}", width="stretch"):
                d.remove_x_account(user)
                st.rerun()
            component_html(news_mod.x_timeline_html(user, dark=TERMINAL, height=height), height=height + 10,
                           scrolling=True)


def page_news():
    d = data()
    opened = st.query_params.get("apri")
    if opened:  # ticker cliccato in un elenco di notizie: apre la scheda
        del st.query_params["apri"]
        t = str(opened).upper()
        if t in set(d.tickers) or t in d.companies.index:
            st.session_state["ticker"] = t
            st.switch_page(nav.page("azienda"))
    st.title("Notizie")
    # solo la sezione aperta si disegna (i riquadri di X si caricano solo quando servono)
    section = st.segmented_control("Sezione", ["Flusso", "X", "Documenti ufficiali (8-K)"], default="Flusso",
                                   key="nw_section", label_visibility="collapsed") or "Flusso"
    if section == "Flusso":
        st.caption("Titoli dai feed RSS pubblici di agenzie, giornali, comunicati stampa e banche centrali, più "
                   "Yahoo Finance e Google News per le tue aziende. Solo titolo e link all'articolo originale. "
                   "Ritardo tipico rispetto all'evento: da 1 a 15 minuti (non è un flusso professionale). "
                   "● = arrivata dall'ultimo aggiornamento.")
        _live_section(d)
    elif section == "X":
        _x_section(d)
    else:
        _filings_section(d)
