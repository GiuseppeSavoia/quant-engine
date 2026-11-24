"""Cornice di ogni pagina (docs/revisione_grafica.md §3.12): barra di ricerca e comandi, tasti funzione F1-F8 con
quello della pagina corrente pieno, "dove sono" (briciole), menu "Dati", barra di stato in fondo.

app.py chiama, in quest'ordine: `_command_bar(pagina)` (prima della pagina), `_keyboard()`, `_status_bar()`, poi
esegue la pagina e infine `finish(pagina)` (aggiorna le briciole con l'azienda e la funzione scelte nella pagina).
La barra e i tasti stanno in un contenitore fisso in alto (`qe_shell`, CSS in ui/common.SHELL_CSS).
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from modules import code_version, commands, pipeline, scheduler, updater
from modules.config import PROJECT_ROOT, load_config
from ui import nav
from ui.common import (APPEARANCES, TERMINAL, _args, _on_appearance, appearance, data, main_config_args, md,
                       scheme_script)
from ui.components import breadcrumb_html, empty_state, notice
from ui.components import fmt as _fmt

PLACEHOLDER = "Cerca azienda o comando…   ( / )"


# ---------------------------------------------------------------- barra dei comandi
def _run_command():
    text = st.session_state.get("qe_cmd", "")
    d = data()
    cmd = commands.parse(text, d.tickers, {t: d.info(t).get("name") or "" for t in d.tickers})
    if cmd.help:
        st.session_state.update(qe_cmd="", qe_help=True)
        return
    if cmd.error:
        st.session_state["qe_error"] = cmd.error  # il testo resta nella barra per correggerlo
        return
    st.session_state["qe_cmd"] = ""
    if cmd.follow is not None:  # SEGUI / TOGLI: si resta sulla pagina, conferma con un toast
        d.follow(cmd.tickers, on=cmd.follow)
        names = ", ".join(cmd.tickers)
        st.session_state["qe_flash"] = (f"{names} " + ("aggiunt" if cmd.follow else "tolt")
                                        + ("e" if len(cmd.tickers) > 1 else "a")
                                        + (" alle tue aziende." if cmd.follow else " dalle tue aziende."))
        return
    if cmd.page == "azienda" and cmd.tickers:
        st.session_state["ticker"] = cmd.tickers[0]
    if cmd.section:
        st.session_state["co_fn"] = cmd.section
    if cmd.page == "confronta":
        st.session_state["cmp"] = cmd.tickers
    st.session_state["qe_goto"] = cmd.page


@st.dialog("Come si usa", width="large")
def _command_help():
    st.markdown(commands.HELP_TEXT)


def _open_from_link():
    """I ticker delle tabelle sono link `./azienda?t=AAPL&fn=DCF` (ui/components.ticker_link): si apre la scheda di
    quell'azienda e si toglie il parametro dall'indirizzo (sennò rimetterebbe sempre la stessa azienda)."""
    qp = st.query_params
    t = qp.get("t")
    if not t:
        return
    d = data()
    known = {x.upper(): x for x in d.tickers}
    if str(t).upper() in known:
        st.session_state["ticker"] = known[str(t).upper()]
        fn = str(qp.get("fn") or "").upper()
        if fn in {c for c, _ in commands.FUNCTIONS}:
            st.session_state["co_fn"] = fn
    for k in ("t", "fn"):
        if k in qp:
            del qp[k]


def _unseen_alerts() -> int:
    """Avvisi attivi non ancora visti (numero sul tasto F1); 0 se la tabella non c'è o non risponde."""
    try:
        return int(len(data().db.load_alerts(unseen=True, active=True)))
    except Exception:  # noqa: BLE001 - un contatore non deve mai rompere la cornice
        return 0


def _crumbs(current) -> list[str]:
    path = getattr(current, "url_path", None)
    if path is None or path not in nav.SPECS:
        return []
    parts = [nav.section_of(path), nav.SPECS[path][2]]
    if path == "azienda":
        t = st.session_state.get("ticker")
        fn = st.session_state.get("co_fn")
        names = dict(commands.FUNCTIONS)
        parts += [t or "", f"{fn} · {names[fn]}" if fn in names else ""]
    return parts


def _command_bar(current=None):
    """Cornice fissa in alto: ricerca/comandi (Invio per eseguire), tasti F1-F8 (quello della pagina corrente pieno),
    menu "Dati"; sotto, "dove sono". `current`: la pagina restituita da `nav.navigation()`. Restituisce il
    segnaposto delle briciole da passare a `finish`."""
    _open_from_link()
    here = getattr(current, "url_path", None)
    unseen = _unseen_alerts()
    with st.container(key="qe_shell"):
        bar, keys, menu = st.columns([3.0, 7.0, 0.8], vertical_alignment="center", gap="small")
        bar.text_input("Comando", key="qe_cmd", on_change=_run_command, label_visibility="collapsed",
                       placeholder=PLACEHOLDER)
        with keys.container(horizontal=True, gap=None, wrap=False, key="qe_fkeys"):
            for key, path, label in nav.FKEYS:
                text = label + (f" · {unseen}" if path == "" and unseen else "")
                tip = f"{nav.SPECS[path][2]} (tasto {key} o Alt+{key[1:]})"
                if path == here:
                    with st.container(key="qe_fkey_on", width="content"):
                        st.page_link(nav.page(path), label=text, help=f"Sei qui: {tip}")
                else:
                    st.page_link(nav.page(path), label=text, help=tip)
        with menu.popover("Dati" + _menu_mark(), width="stretch",
                          help="Aggiorna i dati, esito dell'ultimo aggiornamento, aggiornamento automatico, aspetto"):
            _update_panel()
        crumbs = st.empty()
        crumbs.html(breadcrumb_html(_crumbs(current)))
    _restart_notice()
    if err := st.session_state.pop("qe_error", None):
        st.toast(err, icon=":material/error:", duration="long")
    if msg := st.session_state.pop("qe_flash", None):
        st.toast(msg, icon=":material/check:")
    if st.session_state.pop("qe_help", False):
        _command_help()
    goto = st.session_state.pop("qe_goto", None)
    if goto is not None and goto in nav.SPECS and goto != here:
        st.switch_page(nav.page(goto))
    return crumbs


def _restart_notice():
    """Codice cambiato dopo l'avvio del server: le pagine userebbero un misto di vecchio e nuovo (Streamlit non
    ricarica i moduli importati), quindi si chiede di riavviare. Inoltre: versione di Streamlit non collaudata."""
    import streamlit

    from ui import streamlit_contract

    if not streamlit_contract.version_supported():
        notice(f"Streamlit {streamlit.__version__} non è la versione collaudata ({streamlit_contract.TESTED_SERIES}.x): "
               "l'app funziona, ma qualche rifinitura grafica può mancare. Reinstalla le dipendenze di "
               "requirements.txt.", "info")
    files = code_version.changed()
    if files:
        shown = ", ".join(files[:3]) + (f" e altri {len(files) - 3}" if len(files) > 3 else "")
        notice(f"Il codice dell'app è cambiato dopo l'avvio ({shown}): chiudi e riapri QuantEngine (o riavvia il "
               "server) per usare la versione nuova.", "warn")


def finish(current=None, crumbs=None):
    """Dopo la pagina: le briciole (segnaposto restituito da `_command_bar`) con l'azienda e la funzione scelte
    nella pagina stessa (la scheda cambia il ticker mentre si disegna)."""
    if crumbs is not None:
        crumbs.html(breadcrumb_html(_crumbs(current)))


def _keyboard():
    """F1-F8 (o Alt+1...8) aprono le pagine cliccando i tasti della cornice (niente ricaricamento), "/" porta alla
    barra di ricerca. Inoltre la lingua del browser per i numeri delle tabelle (`st.dataframe` con
    `components.num_column`) è l'italiano. JavaScript nella pagina stessa (st.html): si registra una volta sola."""
    paths = {k: path for k, path, _ in nav.FKEYS}
    st.html(r"""<script>
(() => {
  if (window.__qeKeys) return; window.__qeKeys = true;
  try { Object.defineProperty(Navigator.prototype, 'languages', {get() { return ['it-IT', 'it']; }, configurable: true});
        Object.defineProperty(Navigator.prototype, 'language', {get() { return 'it-IT'; }, configurable: true}); }
  catch (err) {}
  // l'italiano (CLDR) non separa le migliaia sotto 10.000 ("1950,73"): sempre il punto, come fmt() ("1.950,73")
  try {
    const NF = Intl.NumberFormat;
    if (!NF.__qeGrouping) {
      const grouped = (args) => {
        const loc = [].concat(args[0] || [])[0] || '';
        const opts = Object.assign({}, args[1]);
        if (opts.useGrouping === undefined && /^it(-|$)/i.test(String(loc))) opts.useGrouping = 'always';
        return [args[0], opts];
      };
      Intl.NumberFormat = new Proxy(NF, {
        construct(target, args) { return new target(...grouped(args)); },
        apply(target, self, args) { return target(...grouped(args)); },
        get(target, prop) { return prop === '__qeGrouping' ? true : Reflect.get(target, prop); },
      });
    }
  } catch (err) {}
  const paths = %s;
  const open = (path) => {
    const want = ('/' + path).replace(/\/$/, '');
    const link = [...document.querySelectorAll('a[href]')].find(a => {
      const p = new URL(a.href, location.href).pathname.replace(/\/$/, '');
      return path === '' ? p === '' : p.endsWith(want);
    });
    if (link) { link.click(); return true; }
    return false;
  };
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement && document.activeElement.tagName);
    let key = e.key;
    if (e.altKey && !e.metaKey && !e.ctrlKey && /^Digit[1-8]$/.test(e.code)) key = 'F' + e.code.slice(5);
    else if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (paths[key] !== undefined) {
      if (open(paths[key])) e.preventDefault();
    } else if (key === '/' && !typing) {
      const box = document.querySelector('input[aria-label="Comando"]');
      if (box) { e.preventDefault(); box.focus(); box.select(); }
    }
  }, true);
})();
</script>""" % json.dumps(paths), unsafe_allow_javascript=True)
    if appearance() == "sistema":  # "Segui il sistema": il browser dice al server se il Mac è scuro o chiaro
        st.html(scheme_script("sistema", TERMINAL), unsafe_allow_javascript=True)


def _exchange_codes(d) -> dict:
    from modules import exchanges

    conf = d.cfg["monitor"].get("exchanges") or {}
    return conf or {c: [None, ""] for c in exchanges.DEFAULT}


def _status_bar():
    """Riga fissa in fondo, 12 px. In ordine di priorità (se manca spazio si tagliano prima le ultime): dati simulati,
    data dei prezzi (mai tagliata), bilanci arrivati, aggiornamento automatico, borse aperte (●) e chiuse (○)."""
    d = data()
    from modules import exchanges

    board = exchanges.board(list(_exchange_codes(d)))
    filings = d.recent_filings(14)
    loaded = int((filings["status"] == "caricato").sum()) if not filings.empty else 0
    pending = int((filings["status"] == "da caricare").sum()) if not filings.empty else 0
    last = d.data.last_date
    parts = []
    if _args().demo or getattr(d, "is_demo", False):
        parts.append(('keep', '<span class="demo">DEMO</span> dati simulati'))
    parts.append(("keep", f"prezzi al <b>{pd.Timestamp(last):%d/%m/%y}</b>" if last is not None
                  else "nessun prezzo"))
    parts.append(("low", f"10-Q/10-K 14 gg: <b>{loaded}</b> caricati"
                  + (f", {pending} in attesa" if pending else "")))
    upd = _last_update_part()
    if upd:
        parts.append(upd)
    parts.append(("low", "aggiornamento automatico " + ("<b>attivo</b>" if scheduler.installed() else "spento")))
    marks = " ".join(f'<span class="o" title="aperta">●{b["code"]}</span>' if b["open"] else
                     f'<span class="p" title="pausa">◐{b["code"]}</span>' if b["state"] == "PAUSA" else
                     f'<span class="c" title="chiusa">○{b["code"]}</span>' for b in board)
    parts.append(("lowest", f"borse {marks}"))
    parts.append(("low", 'analisi a criteri fissi, non consulenza · <a href="./limiti" target="_self">metodo e limiti</a>'))
    st.html('<div class="qe-status">' + "".join(f'<span class="{c}">{h}</span>' for c, h in parts) + "</div>")


# ---------------------------------------------------------------- esito dell'ultimo aggiornamento (modules/pipeline.py)
_TONE = {pipeline.OK: "pos", pipeline.WARN: "warn", pipeline.FAILED: "neg", pipeline.SKIPPED: "off"}


def _last_summary() -> dict:
    """Riepilogo dell'ultimo aggiornamento con download (se non c'è, dell'ultima esecuzione di main.py), letto dal
    file accanto al database aperto: vale anche per gli aggiornamenti lanciati da Terminale. {} se mai fatto."""
    try:
        d = data()
        path = pipeline.summary_path(d.db.db_path, demo=bool(_args().demo or getattr(d, "is_demo", False)))
        s = pipeline.read_summary(path)
    except Exception:  # noqa: BLE001 - il riepilogo è un di più: la cornice non deve mai rompersi
        return {}
    return s.get("last_update") or s.get("last") or {}


def _duration(seconds) -> str:
    try:
        sec = float(seconds)
    except (TypeError, ValueError):
        return "–"
    if sec < 1:
        return "< 1 s"
    minutes, rest = divmod(int(round(sec)), 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"


def _when(s: dict) -> str:
    try:
        return f"{pd.Timestamp(s.get('finished') or s.get('started')):%d/%m/%Y %H:%M}"
    except (TypeError, ValueError):
        return "–"


def _menu_mark() -> str:
    """Segno accanto a "Dati" quando l'ultimo aggiornamento ha avvisi (⚠) o passi falliti (✗)."""
    status = _last_summary().get("status")
    return f" {pipeline.SYMBOL[status]}" if status in (pipeline.WARN, pipeline.FAILED) else ""


def _last_update_part():
    """Voce della barra di stato: "agg. 17/11 22:55 ✓"; con avvisi o passi falliti non si taglia mai."""
    s = _last_summary()
    status = s.get("status")
    if status not in _TONE:
        return None
    try:
        when = f"{pd.Timestamp(s.get('finished')):%d/%m %H:%M}"
    except (TypeError, ValueError):
        when = "–"
    tip = html.escape(s.get("headline") or "", quote=True)
    if status == pipeline.OK:
        return "low", f'<span title="{tip}">agg. {when} <span class="upd-ok">✓</span></span>'
    if status == pipeline.WARN:
        n = int(s.get("warnings") or 0)
        what = f"{n} " + ("avviso" if n == 1 else "avvisi")
        return "keep", f'<span title="{tip}">agg. {when} <span class="upd-warn">⚠ {what}</span></span>'
    n = int(s.get("failures") or 0)
    what = ("non riuscito" if s.get("fatal") or not n else
            f"{n} " + ("passo fallito" if n == 1 else "passi falliti"))
    return "keep", f'<span title="{tip}">agg. {when} <span class="upd-fail">✗ {what}</span></span>'


def _step_note(step: dict) -> str:
    if step.get("error"):
        return step["error"]
    if step.get("reason"):
        return step["reason"]
    if step.get("warnings"):
        return "; ".join(step["warnings"])
    counts = step.get("counts") or {}
    return ", ".join(f"{k} {_fmt(v, 'int') if isinstance(v, int) and not isinstance(v, bool) else v}"
                     for k, v in counts.items())


def _steps_html(steps: list[dict]) -> str:
    """Passi dell'aggiornamento: esito (simbolo, con la parola nel suggerimento), passo con sotto la nota (errore,
    motivo del salto, avvisi o conteggi) e le righe di avviso del log in un riquadro richiudibile, durata."""
    rows = []
    for st_ in steps:
        status = st_.get("status", "")
        tone = _TONE.get(status, "neutral")
        note = _step_note(st_)
        log = st_.get("log") or []
        extra = (f'<div class="note">{html.escape(note)}</div>' if note else "") + (
            f'<details><summary>{len(log)} righe di avviso nel log</summary><pre>{html.escape(chr(10).join(log))}'
            "</pre></details>" if log else "")
        rows.append(f'<tr><td class="t"><span class="{tone}" title="{html.escape(status)}">'
                    f'{pipeline.SYMBOL.get(status, "?")}</span></td>'
                    f'<td class="t lab">{html.escape(st_.get("label", st_.get("name", "")))}{extra}</td>'
                    f'<td class="n">{"–" if status == pipeline.SKIPPED else _duration(st_.get("seconds"))}</td></tr>')
    return ('<div class="qe-tablewrap"><table class="qe-dense qe-steps"><thead><tr><th></th><th>Passo</th>'
            f'<th class="n">Durata</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _history_html(history: list[dict]) -> str:
    rows = "".join(
        f'<tr><td class="t"><span class="{_TONE.get(h.get("status"), "neutral")}" title="{html.escape(h.get("status") or "")}">'
        f'{pipeline.SYMBOL.get(h.get("status"), "?")}</span></td>'
        f'<td class="t lab">{_when(h)}<div class="note">{html.escape(h.get("headline") or "")}</div></td>'
        f'<td class="n">{_duration(h.get("seconds"))}</td></tr>' for h in history)
    return ('<details class="qe-hist"><summary>Esecuzioni precedenti</summary><div class="qe-tablewrap">'
            f'<table class="qe-dense qe-steps"><tbody>{rows}</tbody></table></div></details>')


def _last_update_section(running: bool) -> None:
    """In cima al menu "Dati": esito dell'ultimo aggiornamento (riga di sintesi; passi con durata, note e righe di
    avviso del log; esecuzioni precedenti)."""
    s = _last_summary()
    if not s:
        st.caption("Ultimo aggiornamento: nessun riepilogo ancora (compare dopo il prossimo aggiornamento).")
        return
    st.caption(f"Ultimo aggiornamento: {_when(s)} · {_duration(s.get('seconds'))}"
               + (" · ne è in corso un altro" if running else ""))
    notice(s.get("headline") or "", _TONE.get(s.get("status"), "info"))
    with st.expander("Passi", expanded=s.get("status") != pipeline.OK):
        st.html(_steps_html(s.get("steps", [])))
        try:
            d = data()
            hist = pipeline.read_summary(pipeline.summary_path(
                d.db.db_path, demo=bool(_args().demo or getattr(d, "is_demo", False)))).get("history", [])
        except Exception:  # noqa: BLE001
            hist = []
        if len(hist) > 1:
            st.html(_history_html(hist[:10]))
        st.caption("Riepilogo: data/logs/aggiornamento_riepilogo.json · log completo: data/logs/aggiornamento.log")


def _toggle_schedule():
    try:
        if st.session_state["auto_update"]:
            scheduler.install(load_config(_args().config))
        else:
            scheduler.uninstall()
    except (RuntimeError, OSError) as exc:  # es. server avviato in un ambiente che non può scrivere fuori dal progetto
        print(f"Aggiornamento automatico non modificato: {exc}", file=sys.stderr, flush=True)
        st.session_state["auto_update_error"] = str(exc)


def _auto_update_toggle():
    """Aggiornamento automatico (launchd): ogni sera dopo la chiusura di Wall Street."""
    cfg = load_config(_args().config)
    close = updater.close_time(pd.Timestamp.now(tz=updater.NY).date(), int(cfg["schedule"]["after_close_minutes"]))
    local = close.tz_convert(datetime.now().astimezone().tzinfo)
    st.session_state["auto_update"] = scheduler.installed()
    st.toggle("Aggiornamento automatico", key="auto_update", on_change=_toggle_schedule,
              help=md(f"Ogni giorno feriale dopo la chiusura di Wall Street (alle {local:%H:%M} ora locale): "
                      "prezzi di chiusura, 10-Q e 10-K appena depositati alla SEC, insider e 13F, analisi. Se il "
                      "Mac è spento o in stop recupera appena si riaccende. Ti avvisa con una notifica."))
    if err := st.session_state.pop("auto_update_error", None):
        empty_state("Aggiornamento automatico non modificato", f"Non riuscito: {err}.",
                    details="Da Terminale, nella cartella del progetto: .venv/bin/python tools/schedule.py attiva")


def _update_panel():
    """Menu "Dati" (in alto a destra): data dei dati e pulsante per aggiornarli (main.py in un processo separato)."""
    demo = _args().demo
    # una copia dei dati simulati aperta con --config (collaudi): niente aggiornamento automatico (installerebbe quello
    # vero dell'utente) e niente "Aggiorna dati" (main.py scaricherebbe dati veri dentro la copia della demo)
    demo_copy = not demo and getattr(data(), "is_demo", False)
    status = updater.read_status(PROJECT_ROOT)
    running = status.get("running", False)

    def panel():
        s = updater.read_status(PROJECT_ROOT)
        if s.get("running"):
            elapsed = pd.Timestamp.now() - pd.Timestamp(s["started"])
            minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
            notice(f"Aggiornamento in corso da {minutes} min {seconds:02d} s. Puoi continuare a usare l'app: "
                   "i dati nuovi compaiono alla fine.", "info")
            st.caption(updater.progress_line(PROJECT_ROOT))
            return
        if running:  # era in corso al giro precedente: è appena finito, si ricarica tutto con i dati nuovi
            st.session_state["update_just_finished"] = True
            st.rerun(scope="app")

    _last_update_section(running)
    st.session_state["ui_appearance"] = appearance()
    st.segmented_control("Aspetto", list(APPEARANCES), format_func=APPEARANCES.get, key="ui_appearance",
                         required=True, on_change=_on_appearance, width="stretch",
                         help="Segui il sistema: chiaro o scuro come il Mac (cambia da solo con l'aspetto del Mac). "
                              "Chiaro: fondo bianco, accento blu. Scuro: fondo nero.")
    if not demo and not demo_copy:
        _auto_update_toggle()
    if demo_copy:
        st.caption("Copia dei dati simulati: aggiornamento dei dati disattivato.")
        return
    if running:
        st.fragment(panel, run_every="3s")()
        return
    if st.session_state.pop("update_just_finished", False) and status.get("returncode") == 0:
        st.success("Dati aggiornati.")
    if status.get("returncode") not in (None, 0) or status.get("interrupted"):
        what = ("interrotto" if status.get("interrupted")
                else f"non riuscito (codice {status['returncode']})")
        st.error(f"Ultimo aggiornamento {what}.")
        with st.expander("Ultime righe del log"):
            st.code("\n".join(updater.log_tail(PROJECT_ROOT, 15)) or "(vuoto)", language=None)
            st.caption("Log completo: data/logs/aggiornamento.log")
    backtest = st.checkbox("Anche il backtest", help="Circa 3 minuti in più: rifà il backtest 2012-oggi.")
    insiders = st.checkbox("Anche insider e grandi investitori",
                           help="Serve solo la prima volta (5-10 minuti: 12 trimestri di Form 4 e i 13F dei fondi). "
                                "Dopo si aggiornano da soli a ogni aggiornamento.")
    if st.button("Aggiorna dati", type="primary", width="stretch",
                 help="Esegue main.py: prezzi nuovi, bilanci attesi, qualità dei dati, giudizi e avvisi. "
                      "Di solito 2-5 minuti."):
        args = main_config_args() + (["--backtest"] if backtest else []) + \
            (["--insiders"] if insiders and not demo else [])
        try:
            updater.start(args, PROJECT_ROOT)
        except RuntimeError as exc:
            notice(str(exc), "warn")
        st.rerun()
