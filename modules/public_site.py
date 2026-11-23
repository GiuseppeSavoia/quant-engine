"""
Sito pubblico in sola lettura: le scelte dell'utente (portafoglio, decisioni, aziende seguite, fondi 13F, avvisi)
in pagine HTML statiche, generate dal Mac dopo l'aggiornamento serale. Nessun server: la cartella si pubblica su
un servizio statico qualunque.

Due passi:
1. `collect` legge il database in SOLA LETTURA (usa solo metodi pubblici di DashboardData e i caricamenti di
   DBManager) e produce un contesto serializzabile (JSON): solo campi scelti uno per uno (mai dizionari interi, che
   potrebbero contenere percorsi o configurazione).
2. `render` riempie i modelli Jinja2 di `site_templates/`, copia `site_static/` e scrive i feed RSS.
`build` fa i due passi in una cartella temporanea, controlla che nell'output non ci siano dati privati (`leaks`) e
solo allora sostituisce la cartella pubblicata.

Cosa NON si pubblica: la serie dei prezzi giornalieri (Yahoo): il grafico del portafoglio è settimanale e in euro,
i prezzi compaiono solo come numero singolo (carico, ultimo prezzo); `sec.user_agent` (contiene un'email), percorsi
locali, chiavi, configurazione. Bilanci SEC, 13F e insider sono dati pubblici.
"""
from __future__ import annotations

import html
import json
import logging
import math
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data.db_manager import DBManager
from modules import formats as F

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "site_templates"
STATIC = ROOT / "site_static"
MARKER = ".sito-generato"
PRESERVE = (".git", "CNAME")  # nella cartella di uscita sopravvivono a ogni nuova generazione  # file scritto nella cartella di uscita: solo una cartella con questo segno si sostituisce
TZ = ZoneInfo("Europe/Rome")

DEFAULTS = {
    "enabled": False,
    "title": "Registro Aperto",
    "author": "",
    "tagline": "Le scelte di un investitore privato, con i numeri e gli errori in chiaro.",
    "intro": ("Qui pubblico quello che compro, vendo e guardo, con il perché e con il risultato misurato contro "
              "l'indice, anche quando ho sbagliato. I giudizi sulle aziende vengono da regole scritte in anticipo "
              "e applicate ai bilanci depositati alla SEC.\n"
              "Non è una raccomandazione d'investimento: è il diario pubblico di una persona."),
    "base_url": "",            # es. https://nome.github.io/registro/ (serve ai link assoluti dei feed RSS)
    "out_dir": "build/sito",
    "funds": [],               # nomi o CIK di superinvestors.funds da mostrare; vuoto = tutti
    "feed_items": 50,
    "alerts_days": 400,        # avvisi più vecchi di così non compaiono
    "fund_positions_shown": 40,
    "insider_min_value": 0,    # acquisti degli insider sotto questo valore ($) non fanno avviso
}

KIND_LABELS = {"decision": "Decisione", "trade": "Operazione", "dividend": "Dividendo", "verdict": "Giudizio",
               "13f": "Nuovo 13F", "insider": "Insider"}
TX_LABELS = {"BUY": "Acquisto", "SELL": "Vendita", "DIV": "Dividendo"}
OUTCOME_SYMBOL = {"favorevole": "✓", "neutro": "●", "contrario": "✗", "mancante": "–"}
# giudizio -> (tono, simbolo): mai solo colore
CATEGORY_STYLE = {"candidata": ("pos", "✓"), "aspettare": ("warn", "◷"), "neutra": ("neutral", "●"),
                  "qualita_dubbia": ("warn", "!"), "evitare": ("neg", "✗"), "dati_insufficienti": ("off", "?")}
# prova storica del giudizio (tools/verdict_backtest.py, CLAUDE.md): numeri fissati qui, si aggiornano a mano
HISTORICAL_TEST = {"period": "2013-2026", "dates": 55, "excess_12m": -0.013, "t_stat": -1.4}  # reports/verdetto_storico_20261116.md


def settings(cfg: dict | None) -> dict:
    """Sezione `public_site:` di config.yaml con i default di questo modulo come riserva."""
    return {**DEFAULTS, **(((cfg or {}).get("public_site")) or {})}


class ReadOnlyDB(DBManager):
    """DBManager che apre il file in sola lettura (SQLite mode=ro) e non tocca lo schema."""

    def __init__(self, db_path):  # noqa: D107 - niente _init_db: nessuna scrittura
        self.db_path = str(db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    def load_checklist_items(self, defaults=None):  # noqa: ARG002 - la prima lettura inserirebbe le domande
        return super().load_checklist_items(None)


# ================================================================== valori serializzabili
def clean(v):
    """Numero, testo, data o None, ricorsivo: NaN/inf -> None, Timestamp -> "AAAA-MM-GG" (o con l'ora)."""
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, (pd.Timestamp, datetime)):
        ts = pd.Timestamp(v)
        return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(v, str):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def _txt(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _paragraphs(text) -> list[str]:
    return [p.strip() for p in str(text or "").split("\n") if p.strip()]


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9.\-]+", "-", str(text)).strip("-")
    return s or "x"


def fund_slug(name: str, cik) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:40]
    return f"{base or 'fondo'}-{int(cik)}"


_EN_NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+")


def it_text(text: str) -> str:
    """Numeri all'inglese nei messaggi degli avvisi ("1,234.5") -> italiano ("1.234,5")."""
    if not text:
        return ""
    return _EN_NUMBER.sub(lambda m: F.it_number(m.group(0)), str(text))


# ================================================================== grafici in SVG
def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if not np.isfinite(lo) or not np.isfinite(hi):
        return []
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    return [start + i * step for i in range(int(math.ceil((hi - start) / step)) + 1)]


def line_chart(series: list[dict], dates: list[str], *, title: str, kind: str = "money", currency: str = "EUR",
               height_px: int = 240) -> dict:
    """Grafico a linee: SVG con griglia e linee (coordinate 0-1000, si stira sull'area del grafico) + etichette in
    HTML posizionate in percentuale (restano leggibili a ogni larghezza) + punti per il livello interattivo.
    `series`: [{key, label, values, cls, dashed}] (cls = classe CSS del colore). Un solo asse; etichette dirette
    alla fine delle linee e legenda."""
    vals = [v for s in series for v in s["values"] if v is not None]
    if len(dates) < 2 or not vals:
        return {}
    ticks = _nice_ticks(min(vals), max(vals))
    lo, hi = ticks[0], ticks[-1]
    x = lambda i: 1000 * i / (len(dates) - 1)  # noqa: E731
    y = lambda v: 1000 * (1 - (v - lo) / (hi - lo))  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 1000 1000" preserveAspectRatio="none" role="img" '
           f'aria-label="{html.escape(title)}">']
    labels = []
    for t in ticks:
        out.append(f'<line class="grid" x1="0" x2="1000" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        labels.append({"cls": "yl", "top": y(t) / 10,
                       "text": F.fmt(t, kind, currency=currency, digits=0 if kind == "money" and abs(t) < 1e6 else None)})
    idx = sorted({0, len(dates) - 1, *[round(i * (len(dates) - 1) / 4) for i in range(1, 4)]})
    for i in idx:
        labels.append({"cls": "xl" + (" first" if i == 0 else " last" if i == len(dates) - 1 else " mid"),
                       "left": x(i) / 10, "text": F.fmt(dates[i], "date_short")})
    ends = []
    for s in series:
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None]
        if len(pts) < 2:
            continue
        d = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        out.append(f'<path class="line {s["cls"]}{" dashed" if s.get("dashed") else ""}" d="{d}"/>')
        ends.append([pts[-1][1] / 10, s["label"]])
    ends.sort(key=lambda t: t[0])  # etichette dirette senza sovrapposizioni: almeno 18 px tra due
    gap = 18 / height_px * 100
    for i in range(1, len(ends)):
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + gap)
    labels += [{"cls": "dl", "top": top, "text": text} for top, text in ends]
    out.append('<line class="cross" x1="0" x2="0" y1="0" y2="1000" visibility="hidden"/>')
    out.append("</svg>")
    points = {"x0": 0, "w": 1000, "dates": [F.fmt(dd, "date") for dd in dates],
              "series": [{"label": s["label"], "cls": s["cls"],
                          "values": [F.fmt(v, kind, currency=currency) for v in s["values"]]} for s in series]}
    return {"svg": "".join(out), "labels": labels, "points": points, "title": title}


def verdict_strip(segments: list[dict], width: int = 720, height: int = 28) -> str:
    """Striscia del giudizio nel tempo: un rettangolo per periodo, colore della categoria (con testo a parte)."""
    if not segments:
        return ""
    start = pd.Timestamp(segments[0]["start"])
    end = pd.Timestamp(segments[-1]["end"]) + pd.Timedelta(days=1)
    span = max((end - start).days, 1)
    out = [f'<svg class="strip" viewBox="0 0 {width} {height}" role="img" aria-hidden="true" preserveAspectRatio="none">']
    for s in segments:
        a = (pd.Timestamp(s["start"]) - start).days / span * width
        b = ((pd.Timestamp(s["end"]) + pd.Timedelta(days=1)) - start).days / span * width
        out.append(f'<rect class="cat-{s["category"]}" x="{a:.1f}" y="0" width="{max(b - a - 2, 1):.1f}" '
                   f'height="{height}" rx="2"/>')
    out.append("</svg>")
    return "".join(out)


# ================================================================== raccolta dei dati
def _followed(db, positions: pd.DataFrame, journal: pd.DataFrame) -> list[str]:
    tickers = list(db.load_watchlist())
    if positions is not None and not positions.empty:
        tickers += list(positions["ticker"])
    if journal is not None and not journal.empty:
        tickers += list(journal["ticker"])
    return list(dict.fromkeys(t for t in tickers if isinstance(t, str) and t))


def _verdict_dict(v) -> dict:
    """Il Verdict (modules/verdict.py) con i soli campi da pubblicare."""
    from modules import verdict as vd

    tone, symbol = CATEGORY_STYLE.get(v.category, ("neutral", "●"))
    pillars = []
    for p in v.pillars.values():
        pillars.append({
            "key": p.key, "label": p.label, "score": p.score, "grade": p.grade, "coverage": p.coverage,
            "evidence": [{"label": e.label, "shown": e.shown, "reference": e.reference, "outcome": e.outcome,
                          "symbol": OUTCOME_SYMBOL.get(e.outcome, "●"), "text": e.text}
                         for e in sorted(p.evidence, key=lambda e: e.outcome == "mancante")],
        })
    return {"category": v.category, "label": v.label, "short": vd.LABELS.get(v.category, v.label), "tone": tone,
            "symbol": symbol, "rule": v.rule, "profile": v.profile_label, "coverage": v.coverage,
            "overall": v.overall, "alarms": list(v.alarms), "checks": list(v.checks), "changes": list(v.changes),
            "pillars": pillars, "scores": {p["key"]: p["score"] for p in pillars}, "as_of": v.as_of}


def _verdict_timeline(rows: pd.DataFrame) -> dict:
    """Periodi con lo stesso giudizio dallo storico (senza il prezzo: sarebbe una serie di prezzi)."""
    from modules import verdict as vd

    if rows is None or rows.empty:
        return {"segments": [], "first": None, "dates": 0}
    rows = rows.sort_values("as_of")
    segs: list[dict] = []
    for r in rows.itertuples(index=False):
        day = pd.Timestamp(r.as_of)
        if segs and segs[-1]["category"] == r.category:
            segs[-1]["end"] = day
            continue
        tone, symbol = CATEGORY_STYLE.get(r.category, ("neutral", "●"))
        segs.append({"category": r.category, "label": vd.LABELS.get(r.category, r.category), "tone": tone,
                     "symbol": symbol, "start": day, "end": day})
    return {"segments": segs, "first": rows["as_of"].iloc[0], "dates": int(rows["as_of"].nunique()),
            "strip": verdict_strip(clean(segs)) if len(segs) > 1 or rows["as_of"].nunique() > 1 else ""}


def _with_current(history: pd.DataFrame, t: str, verdict, as_of) -> pd.DataFrame:
    """Storico di `t` che finisce con il giudizio mostrato in pagina: la riga della data dei dati, se c'è, è
    sostituita da quella calcolata ora (dopo un cambio del fair value o delle regole sarebbe diversa)."""
    rows = history[history["ticker"] == t][["as_of", "category"]] if history is not None and not history.empty \
        else pd.DataFrame(columns=["as_of", "category"])
    if verdict is None or as_of is None:
        return rows
    rows = rows[rows["as_of"] != pd.Timestamp(as_of)]
    now = pd.DataFrame({"as_of": [pd.Timestamp(as_of)], "category": [verdict.category]})
    return pd.concat([rows, now], ignore_index=True) if not rows.empty else now


def twr(values: pd.Series, invested: pd.Series) -> float | None:
    """Rendimento ponderato nel tempo: i versamenti e i prelievi (variazioni di `invested`) non contano come
    guadagno. Rendimento di ogni giorno = valore / (valore del giorno prima + versamento del giorno) - 1, i versamenti
    a inizio giornata; i giorni senza base (portafoglio vuoto) si saltano. Con le vendite il "guadagno / investito
    netto" gonfia il risultato (la base si restringe): questo no."""
    v = pd.to_numeric(values, errors="coerce")
    flows = pd.to_numeric(invested, errors="coerce").diff().fillna(pd.to_numeric(invested, errors="coerce"))
    base = v.shift(1).fillna(0.0) + flows
    r = v / base.where(base > 0) - 1
    r = r[np.isfinite(r)]
    return float((1 + r).prod() - 1) if not r.empty else None


def _portfolio(d, followed_verdicts: dict) -> tuple[dict, pd.DataFrame]:
    from modules import portfolio as pf

    view = d.portfolio_view()
    tx = view.get("tx", pd.DataFrame())
    if tx is None or tx.empty:
        return {"status": "empty"}, pd.DataFrame()
    pos = view.get("positions", pd.DataFrame())
    hist = view.get("history", pd.DataFrame())
    out = {"status": "ok", "benchmark": view.get("benchmark"), "missing_prices": list(view.get("missing_prices", [])),
           "chart": {}, "chart_table": [], "look_through": None}
    positions = []
    for r in pos.itertuples(index=False):
        v = followed_verdicts.get(r.ticker)
        positions.append({"ticker": r.ticker, "name": r.name, "sector": r.sector, "quantity": r.quantity,
                          "currency": r.currency, "avg_cost": r.avg_cost, "price": r.price, "cost_eur": r.cost_eur,
                          "value_eur": r.value_eur, "gain_eur": r.gain_eur, "gain_pct": r.gain_pct,
                          "weight": getattr(r, "weight", None), "since": r.since,
                          "verdict": {k: v[k] for k in ("label", "tone", "symbol", "category")} if v else None})
    positions.sort(key=lambda p: -(p["value_eur"] if p["value_eur"] is not None and np.isfinite(p["value_eur"]) else -1))
    out["positions"] = positions

    close = d.data.close
    fx = close[[c for c in close.columns if str(c).startswith("EUR") and str(c).endswith("=X")]]
    t = pf.with_eur(tx, fx)
    out["trades"] = [{"id": r.id, "date": r.date, "kind": r.kind, "kind_label": TX_LABELS.get(r.kind, r.kind),
                      "ticker": r.ticker, "quantity": r.quantity, "price": r.price, "currency": r.currency,
                      "amount_eur": getattr(r, "gross_eur", None), "fees_eur": getattr(r, "fees_eur", None),
                      "note": _txt(r.note)}
                     for r in t.sort_values(["date", "id"], ascending=False).itertuples(index=False)]
    div = view.get("dividends", pd.DataFrame())
    out["dividends"] = [{"date": r.date, "ticker": r.ticker, "gross_eur": r.gross_eur, "net_eur": r.net_eur}
                        for r in div.sort_values("date", ascending=False).itertuples(index=False)] if not div.empty else []
    out["dividends_net"] = float(div["net_eur"].sum()) if not div.empty else 0.0
    realized = view.get("realized", pd.DataFrame())
    out["realized"] = [{"date": r.date, "ticker": r.ticker, "quantity": r.quantity, "proceeds_eur": r.proceeds_eur,
                        "gain_eur": r.gain_eur} for r in realized.itertuples(index=False)] if not realized.empty else []

    kpi = {"value": None, "invested": None, "gain": None, "twr": None, "bench_value": None, "bench_gain": None,
           "bench_twr": None, "vs_bench": None, "since": tx["date"].min(), "positions": len(positions)}
    if hist is not None and not hist.empty:
        last = hist.dropna(subset=["value"]).iloc[-1] if hist["value"].notna().any() else None
        if last is not None:
            kpi.update(value=last["value"], invested=last["invested"], gain=last["value"] - last["invested"],
                       as_of=last.name)
            kpi["twr"] = twr(hist["value"], hist["invested"])
            b = last.get("benchmark", np.nan)
            if pd.notna(b):
                kpi.update(bench_value=b, bench_gain=b - last["invested"], vs_bench=last["value"] - b,
                           bench_twr=twr(hist["benchmark"], hist["invested"]))
        # grafico SETTIMANALE (mai la serie giornaliera: con una sola posizione ridarebbe i prezzi)
        wk = hist.resample("W-FRI").last().dropna(how="all")
        dates = [clean(i) for i in wk.index]
        series = [{"key": "value", "label": "Portafoglio", "cls": "s1",
                   "values": [clean(v) for v in wk["value"]]}]
        if "benchmark" in wk and wk["benchmark"].notna().any():
            series.append({"key": "benchmark", "label": f"Indice ({out['benchmark']})", "cls": "s2",
                           "values": [clean(v) for v in wk["benchmark"]]})
        series.append({"key": "invested", "label": "Investito", "cls": "s0", "dashed": True,
                       "values": [clean(v) for v in wk["invested"]]})
        out["chart"] = line_chart(series, dates, title="Valore del portafoglio e degli stessi euro nell'indice")
        mo = hist.resample("ME").last().dropna(how="all")
        out["chart_table"] = [{"date": i, "value": r.get("value"), "benchmark": r.get("benchmark"),
                               "invested": r.get("invested")} for i, r in mo.iterrows()]
    out["kpi"] = kpi

    try:  # esposizione vera negli ETF: solo dalla copia salvata, nessuno scarico (sorgente "spenta")
        look = d.portfolio_look_through(positions=pos, source=_Offline())
        if look.get("status") == "ok":
            summary = look["look"]["summary"]
            by_company = look["look"]["by_company"].head(15)
            out["look_through"] = {
                "summary": {k: summary.get(k) for k in ("total_eur", "detailed_eur", "undetailed_eur")},
                "top": [{"name": r.get("name") or r.get("ticker") or "", "ticker": r.get("ticker"),
                         "value_eur": r.get("total_eur"), "weight": r.get("weight")}
                        for _, r in by_company.reset_index().iterrows()],
                "etfs": list(look.get("etfs", [])), "fetched": dict(look.get("fetched", {}))}
    except Exception as exc:  # noqa: BLE001 - l'esposizione vera è un di più
        logger.warning("Sito: esposizione negli ETF non calcolata (%s)", exc)
    return out, pos


class _Offline:
    """Sorgente delle composizioni degli ETF che non scarica nulla: si usa solo la copia salvata."""

    def fetch(self, symbol):  # noqa: ARG002
        return None


def _decisions(d) -> dict:
    from modules import journal

    j = d.journal_view()
    if j is None or j.empty:
        return {"entries": [], "hit": {"judged": 0, "right": 0, "rate": None, "by_action": []}, "pending": 0,
                "min_days": journal.MIN_DAYS_FOR_VERDICT}
    items = []
    for r in j.itertuples(index=False):
        right = r.right if isinstance(r.right, bool) else None
        items.append({"id": r.id, "date": r.date, "ticker": r.ticker, "name": getattr(r, "name", ""),
                      "action": r.action, "action_label": journal.ACTIONS.get(r.action, r.action), "price": r.price,
                      "reason": _txt(r.reason), "change_mind": _txt(r.change_mind), "review_date": r.review_date,
                      "review_note": _txt(getattr(r, "review_note", None)), "ret": r.ret, "bench_ret": r.bench_ret,
                      "excess": r.excess, "right": right, "days": r.days,
                      "days_left": max(journal.MIN_DAYS_FOR_VERDICT - int(r.days), 0) if pd.notna(r.days) else None,
                      "currency": d.currency(r.ticker) if hasattr(d, "currency") else "USD"})
    hit = journal.hit_rate(j)
    return {"entries": items, "pending": sum(1 for i in items if i["right"] is None),
            "hit": {"judged": hit["judged"], "right": hit["right"], "rate": hit["rate"],
                    "by_action": [{"action": a, "label": journal.ACTIONS.get(a, a), "n": n, "rate": rate}
                                  for a, (n, rate) in hit["by_action"].items()]},
            "min_days": journal.MIN_DAYS_FOR_VERDICT}


def _company(d, t: str, verdict, history: pd.DataFrame, positions: pd.DataFrame, decisions: list[dict],
             trades: list[dict], fund_pos: pd.DataFrame, insiders: pd.DataFrame) -> dict:
    from modules import changes

    info = d.info(t)
    currency = d.currency(t) if hasattr(d, "currency") else "USD"
    held = positions[positions["ticker"] == t] if positions is not None and not positions.empty else pd.DataFrame()
    owned = None
    if not held.empty:
        h = held.iloc[0]
        owned = {"quantity": h["quantity"], "avg_cost": h["avg_cost"], "since": h["since"], "weight": h.get("weight"),
                 "gain_pct": h["gain_pct"]}
    px = d.data.prices(t) if t in d.data.close else pd.Series(dtype=float)
    px = px.dropna() if px is not None else pd.Series(dtype=float)
    note, research = d.note(t) or {}, d.research(t) or {}
    vals = d.valuation_diary(t)
    valuation = None
    if vals is not None and not vals.empty:
        v = vals.sort_values("created_at").iloc[-1]
        valuation = {"created_at": v.get("created_at"), "price": v.get("price"), "expected_value": v.get("expected_value"),
                     "margin_then": v.get("margin_then"), "return_since": v.get("return_since"),
                     "story": _txt(v.get("story")), "note": _txt(v.get("note"))}
    wc = {"form": None, "period_end": None, "filed": None, "items": []}
    try:
        n0, val0 = changes.thesis_inputs(d.db, t)
        res = changes.what_changed(d.data, t, cfg=d.cfg, sector=info.get("sector"), currency=currency, note=n0,
                                   valuation=val0)
        if res:
            lt = res["latest"]
            wc = {"form": lt.get("form"), "period_end": lt.get("period_end"), "filed": lt.get("filed"),
                  "items": [{"text": s["text"], "favorable": s["favorable"]} for s in res.get("summary", [])[:8]]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sito: cosa è cambiato per %s non calcolato (%s)", t, exc)
    holders = []
    if fund_pos is not None and not fund_pos.empty:
        fh = fund_pos[(fund_pos["ticker"] == t) & (fund_pos["change"] != "uscita")].sort_values("weight", ascending=False)
        holders = [{"fund": r.fund_name, "slug": fund_slug(r.fund_name, r.fund_cik), "weight": r.weight,
                    "value": r.value, "change": r.change, "report_date": r.report_date} for r in fh.itertuples()]
    ins = insiders[insiders["ticker"] == t] if insiders is not None and not insiders.empty else pd.DataFrame()
    return {
        "ticker": t, "slug": slug(t), "name": info.get("name") or "", "sector": info.get("sector") or "",
        "sub_industry": info.get("sub_industry") or "", "currency": currency,
        "last_price": float(px.iloc[-1]) if not px.empty else None, "price_date": px.index[-1] if not px.empty else None,
        "owned": owned, "verdict": _verdict_dict(verdict) if verdict is not None else None,
        "timeline": _verdict_timeline(_with_current(history, t, verdict, d.data.last_date)),
        "thesis": {"text": _paragraphs(note.get("thesis")), "fair_value": note.get("fair_value"),
                   "updated_at": note.get("updated_at"), "one_liner": _txt(research.get("one_liner")),
                   "story": _paragraphs(research.get("story")), "catalysts": _paragraphs(research.get("catalysts")),
                   "risks": _paragraphs(research.get("risks")), "change_mind": _paragraphs(research.get("change_mind")),
                   "review_date": research.get("review_date")},
        "valuation": valuation, "changes": wc,
        "decisions": [x for x in decisions if x["ticker"] == t],
        "trades": [x for x in trades if x["ticker"] == t],
        "holders": holders,
        "insider_buys": [{"date": r.filed, "owner": r.owner, "title": r.title or r.relationship, "shares": r.shares,
                          "value": r.value} for r in ins.itertuples()][:20],
    }


def _selected_funds(cfg: dict, holdings: pd.DataFrame) -> list[dict]:
    funds = (cfg.get("superinvestors") or {}).get("funds") or []
    wanted = settings(cfg).get("funds") or []
    if wanted:
        keys = {str(w).strip().lower() for w in wanted}
        funds = [f for f in funds if str(f.get("cik")).lower() in keys or str(f.get("name", "")).lower() in keys]
    if not funds and holdings is not None and not holdings.empty:  # nessuna lista in config: i fondi nei dati
        funds = [{"cik": c, "name": n} for c, n in holdings.drop_duplicates("fund_cik")[["fund_cik", "fund_name"]]
                 .itertuples(index=False)]
    return [{"cik": int(f["cik"]), "name": str(f["name"])} for f in funds]


def _top(names: pd.DataFrame, n: int = 3) -> list[str]:
    return [(r.ticker if isinstance(r.ticker, str) and r.ticker else str(r.issuer).title()) for r in names.head(n).itertuples()]


def fix_thousands(holdings: pd.DataFrame, close: pd.DataFrame | None) -> tuple[pd.DataFrame, set]:
    """Unità sbagliate nei 13F (regola in `superinvestors.unit_factors`, che le corregge già nel database a ogni
    ingestione): qui di nuovo, per non pubblicare importi falsi da un database non ancora corretto. I pesi non
    cambiano. Restituisce (tabella corretta, depositi corretti)."""
    from modules.superinvestors import unit_factors

    factor = unit_factors(holdings, close)
    if factor:
        holdings = holdings.copy()
        holdings["value"] = holdings["value"] * holdings["accession"].map(factor).fillna(1.0)
    return holdings, set(factor)


def _funds(cfg: dict, db, since: pd.Timestamp, close: pd.DataFrame | None = None
           ) -> tuple[list[dict], list[dict], pd.DataFrame]:
    """(fondi con posizioni e cambi, avvisi "nuovo 13F", posizioni dell'ultimo 13F di tutti i fondi scelti)."""
    from modules.superinvestors import fund_positions

    s = settings(cfg)
    holdings, fixed = fix_thousands(db.load_thirteenf(), close)
    selected = _selected_funds(cfg, holdings)
    out, alerts, latest_all = [], [], []
    for f in selected:
        sub = holdings[holdings["fund_cik"] == f["cik"]] if not holdings.empty else holdings
        entry = {"cik": f["cik"], "name": f["name"], "slug": fund_slug(f["name"], f["cik"]),
                 "sec_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={f['cik']}&type=13F-HR",
                 "status": "empty", "positions": [], "changes": {}}
        if sub.empty:
            out.append(entry)
            continue
        pos = fund_positions(sub)
        latest_all.append(pos)
        held = pos[pos["change"] != "uscita"].sort_values("value", ascending=False)
        entry.update(status="ok", report_date=pos["report_date"].max(), filed=pos["filed"].max(),
                     thousands_fixed=bool(set(sub["accession"]) & fixed),
                     total_value=float(held["value"].sum()), n_positions=int(len(held)),
                     quarters=int(sub["report_date"].nunique()))
        entry["positions"] = [{"ticker": r.ticker if isinstance(r.ticker, str) else None, "issuer": r.issuer,
                               "value": r.value, "weight": r.weight, "shares_change": r.shares_change,
                               "change": r.change, "rel": (r.value / held["value"].max()) if held["value"].max() else None}
                              for r in held.head(int(s["fund_positions_shown"])).itertuples()]
        entry["top10_weight"] = float(held["weight"].head(10).sum()) if not held.empty else None
        for kind in ("nuova", "aumentata", "ridotta", "uscita"):
            g = pos[pos["change"] == kind]
            entry["changes"][kind] = len(g)
        entry["new"] = [{"ticker": r.ticker if isinstance(r.ticker, str) else None, "issuer": r.issuer,
                         "value": r.value, "weight": r.weight} for r in
                        pos[pos["change"] == "nuova"].sort_values("value", ascending=False).head(10).itertuples()]
        out.append(entry)
        # un avviso per ogni 13F depositato nella finestra, con le novità principali
        filings = sub.drop_duplicates("accession")[["accession", "report_date", "filed"]].sort_values("filed")
        for r in filings[filings["filed"] >= since].itertuples(index=False):
            p = fund_positions(sub, as_of=r.filed)
            if p.empty:
                continue
            new = p[p["change"] == "nuova"].sort_values("value", ascending=False)
            up = p[p["change"] == "aumentata"].sort_values("value", ascending=False)
            gone = p[p["change"] == "uscita"]
            parts = []
            if not new.empty:
                parts.append(f"nuove posizioni: {', '.join(_top(new))}" + (f" (+{len(new) - 3})" if len(new) > 3 else ""))
            if not up.empty:
                parts.append(f"aumentate: {', '.join(_top(up))}" + (f" (+{len(up) - 3})" if len(up) > 3 else ""))
            if not gone.empty:
                parts.append(f"uscite: {', '.join(_top(gone))}" + (f" (+{len(gone) - 3})" if len(gone) > 3 else ""))
            alerts.append({"guid": f"13f-{r.accession}", "when": r.filed, "kind": "13f", "ticker": None,
                           "title": f"Nuovo 13F depositato da {f['name']}",
                           "text": (f"Portafoglio al {F.fmt(r.report_date, 'date')}: "
                                    + ("; ".join(parts) if parts else "nessun cambio rilevante") + "."),
                           "href": f"fondi/{entry['slug']}.html"})
    latest = pd.concat(latest_all, ignore_index=True) if latest_all else pd.DataFrame()
    return out, alerts, latest


def _alerts(cfg: dict, db, decisions: list[dict], trades: list[dict], followed: list[str], since: pd.Timestamp,
            insiders: pd.DataFrame) -> list[dict]:
    out = []
    for x in decisions:
        cur = x.get("currency") or "USD"
        price = f" a {F.fmt(x['price'], 'price', currency=cur)}" if x.get("price") is not None else ""
        out.append({"guid": f"dec-{x['id']}", "when": x["date"], "kind": "decision", "ticker": x["ticker"],
                    "title": f"{x['action_label']}: {x['ticker']}{price}",
                    "text": x["reason"] or "Nessun motivo scritto.", "href": "decisioni.html#d" + str(x["id"])})
    for x in trades:
        if x["kind"] == "DIV":
            out.append({"guid": f"tx-{x['id']}", "when": x["date"], "kind": "dividend", "ticker": x["ticker"],
                        "title": f"Dividendo da {x['ticker']}: {F.fmt(x['amount_eur'], 'money', currency='EUR')}",
                        "text": "Dividendo lordo incassato.", "href": "portafoglio.html#operazioni"})
            continue
        out.append({"guid": f"tx-{x['id']}", "when": x["date"], "kind": "trade", "ticker": x["ticker"],
                    "title": (f"{x['kind_label']} di {F.fmt(x['quantity'], 'num', digits=0 if float(x['quantity'] or 0).is_integer() else 2)} "
                              f"{x['ticker']} a {F.fmt(x['price'], 'price', currency=x['currency'])}"),
                    "text": (f"Importo {F.fmt(x['amount_eur'], 'money', currency='EUR')}."
                             + (f" {x['note']}" if x.get("note") else "")),
                    "href": "portafoglio.html#operazioni"})
    try:
        vrd = db.load_alerts()
        vrd = vrd[(vrd["code"] == "VRD") & vrd["ticker"].isin(followed)] if not vrd.empty else vrd
        for r in vrd.itertuples(index=False):
            out.append({"guid": f"vrd-{r.id}", "when": pd.Timestamp(r.created_at), "kind": "verdict",
                        "ticker": r.ticker, "title": f"Giudizio cambiato: {r.ticker}",
                        "text": _sentence(it_text(r.message)), "href": f"aziende/{slug(r.ticker)}.html"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sito: avvisi VRD non letti (%s)", exc)
    if insiders is not None and not insiders.empty:
        for (acc, t), g in insiders.groupby(["accession", "ticker"], sort=False):
            r = g.iloc[0]
            value = float(g["value"].sum())
            if value < float(settings(cfg)["insider_min_value"]):
                continue
            who = f"{r['owner']}" + (f" ({r['title']})" if isinstance(r["title"], str) and r["title"] else "")
            out.append({"guid": f"ins-{acc}", "when": r["filed"], "kind": "insider", "ticker": t,
                        "title": f"Acquisto di un insider: {t}",
                        "text": (f"{who.title() if who.isupper() else who} ha comprato "
                                 f"{F.fmt(g['shares'].sum(), 'shares')} azioni per {F.fmt(value, 'money', currency='USD')} "
                                 f"(Form 4 depositato il {F.fmt(r['filed'], 'date')})."),
                        "href": f"aziende/{slug(t)}.html"})
    out = [a for a in out if pd.notna(a["when"]) and pd.Timestamp(a["when"]) >= since]
    out.sort(key=lambda a: (pd.Timestamp(a["when"]), a["guid"]), reverse=True)
    for a in out:
        a["kind_label"] = KIND_LABELS.get(a["kind"], a["kind"])
    return out


def collect(cfg: dict, db, d=None, data=None, reports_dir=None, now: datetime | None = None) -> dict:
    """Tutto quello che va sul sito, già ridotto ai campi pubblicabili (valori semplici, JSON)."""
    from modules import verdict as vd
    from modules import verdict_history as vh

    s = settings(cfg)
    now = now or datetime.now(TZ)
    if d is None:
        d = vh.app_data(cfg, db, Path(reports_dir) if reports_dir else ROOT / "reports", data)
    as_of = d.data.last_date
    since = pd.Timestamp(as_of or now.date()) - pd.Timedelta(days=int(s["alerts_days"]))

    journal_view = d.journal_view()
    pos_now = d.portfolio_view().get("positions", pd.DataFrame())
    followed = _followed(d.db, pos_now, journal_view)
    verdicts = {}
    if followed:
        try:
            _, objs = vd.assess_many(d, followed, return_verdicts=True)
            verdicts = dict(objs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sito: giudizi non calcolati (%s)", exc, exc_info=True)
    vdicts = {t: _verdict_dict(v) for t, v in verdicts.items()}

    portfolio, positions = _portfolio(d, vdicts)
    decisions = _decisions(d)
    history = d.db.load_verdict_history(tickers=followed, with_rule=False) if followed else pd.DataFrame()
    funds, fund_alerts, fund_pos = _funds(cfg, d.db, since, d.data.close)
    insiders = pd.DataFrame()
    if followed:
        ins = d.db.load_insiders(followed, codes=["P"])
        if not ins.empty:
            ins = ins[~ins["relationship"].fillna("").str.fullmatch("TenPercentOwner")]
            insiders = ins[ins["filed"] >= since].sort_values("filed", ascending=False)
    companies = [_company(d, t, verdicts.get(t), history, positions, decisions["entries"], portfolio.get("trades", []),
                          fund_pos, insiders) for t in followed]
    companies.sort(key=lambda c: ([*vd.ORDER, None].index(c["verdict"]["category"] if c["verdict"] else None),
                                  c["ticker"]))
    alerts = _alerts(cfg, d.db, decisions["entries"], portfolio.get("trades", []), followed, since, insiders)
    alerts = sorted(alerts + fund_alerts, key=lambda a: (pd.Timestamp(a["when"]), a["guid"]), reverse=True)
    for a in alerts:
        a["kind_label"] = KIND_LABELS.get(a["kind"], a["kind"])

    site = {"title": s["title"], "author": s["author"], "tagline": s["tagline"], "intro": _paragraphs(s["intro"]),
            "base_url": (s["base_url"].rstrip("/") + "/") if s["base_url"] else "",
            "is_demo": bool(getattr(d, "is_demo", False)), "generated_at": now, "as_of": as_of,
            "feed_items": int(s["feed_items"])}
    counts = pd.Series([c["verdict"]["category"] for c in companies if c["verdict"]]).value_counts().to_dict()
    ctx = {
        "site": site, "portfolio": portfolio, "decisions": decisions, "companies": companies, "funds": funds,
        "alerts": alerts,
        "method": {"pillars": [{"key": k, "label": lab} for k, lab in vd.PILLAR_LABELS.items()],
                   "categories": [{"key": c, "label": vd.LABELS[c], "tone": CATEGORY_STYLE[c][0],
                                   "symbol": CATEGORY_STYLE[c][1], "n": int(counts.get(c, 0))} for c in vd.ORDER],
                   "test": HISTORICAL_TEST, "disclaimer": vd.DISCLAIMER,
                   "history_start": (history["as_of"].min() if history is not None and not history.empty else None)},
    }
    return clean(ctx)


# ================================================================== scrittura
def _env(strict: bool = False):
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined, select_autoescape

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True, undefined=StrictUndefined if strict else Undefined)
    env.filters["fmt"] = lambda v, kind="num", **kw: F.fmt(v, kind, **kw)
    env.filters["tone"] = F.tone_of
    env.filters["slug"] = slug
    env.filters["tojson_attr"] = lambda v: json.dumps(v, ensure_ascii=False)
    return env


def _rfc822(when) -> str:
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize(TZ)
    return format_datetime(ts.to_pydatetime())


def _feed(env, ctx: dict, items: list[dict], title: str, description: str, self_path: str) -> str:
    base = ctx["site"]["base_url"]
    rows = [{**a, "pub": _rfc822(a["when"]), "link": (base + a["href"]) if base and a.get("href") else ""}
            for a in items[: ctx["site"]["feed_items"]]]
    return env.get_template("feed.xml").render(
        site=ctx["site"], items=rows, title=title, description=description,
        link=base or "", self_url=(base + self_path) if base else "", built=_rfc822(ctx["site"]["generated_at"]))


def render(ctx: dict, out_dir: Path, strict: bool = False) -> list[Path]:
    """Scrive le pagine in `out_dir` (che deve esistere ed essere vuota) e restituisce i file scritti. `strict`: un
    campo mancante nel contesto è un errore (test) invece di una cella vuota."""
    env = _env(strict)
    out_dir = Path(out_dir)
    written: list[Path] = []
    followed = {c["ticker"] for c in ctx["companies"]}

    def page(template: str, path: str, **extra) -> None:
        depth = path.count("/")
        html = env.get_template(template).render(ctx=ctx, site=ctx["site"], root="../" * depth, path=path,
                                                 followed=followed, **extra)
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        written.append(target)

    page("index.html", "index.html", active="home")
    page("portafoglio.html", "portafoglio.html", active="portafoglio")
    page("decisioni.html", "decisioni.html", active="decisioni")
    page("aziende.html", "aziende.html", active="aziende")
    for c in ctx["companies"]:
        page("azienda.html", f"aziende/{c['slug']}.html", active="aziende", c=c,
             alerts=[a for a in ctx["alerts"] if a.get("ticker") == c["ticker"]])
    page("fondi.html", "fondi.html", active="fondi")
    for f in ctx["funds"]:
        page("fondo.html", f"fondi/{f['slug']}.html", active="fondi", f=f,
             alerts=[a for a in ctx["alerts"] if a["kind"] == "13f" and a["href"] == f"fondi/{f['slug']}.html"])
    page("avvisi.html", "avvisi.html", active="avvisi")
    page("metodo.html", "metodo.html", active="metodo")

    feeds = [("avvisi.xml", ctx["alerts"], f"{ctx['site']['title']}: avvisi",
              "Nuove decisioni, operazioni, giudizi cambiati, nuovi 13F dei fondi e acquisti degli insider.")]
    for c in ctx["companies"]:
        feeds.append((f"aziende/{c['slug']}.xml", [a for a in ctx["alerts"] if a.get("ticker") == c["ticker"]],
                      f"{ctx['site']['title']}: {c['ticker']}", f"Avvisi su {c['name'] or c['ticker']}."))
    for path, items, title, desc in feeds:
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_feed(env, ctx, items, title, desc, path), encoding="utf-8")
        written.append(target)

    shutil.copytree(STATIC, out_dir / "static", dirs_exist_ok=True)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages: servi i file così come sono
    (out_dir / MARKER).write_text(f"generato {ctx['site']['generated_at']}\n", encoding="utf-8")
    return written


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def leaks(out_dir: Path, cfg: dict) -> list[str]:
    """Dati privati nell'output: email, user agent della SEC, percorsi locali, percorso del database."""
    secrets = {s for s in [(cfg.get("sec") or {}).get("user_agent"), str(Path.home()), str(ROOT),
                           (cfg.get("data") or {}).get("db_path")] if s and len(str(s)) > 3}
    found = []
    for p in Path(out_dir).rglob("*"):
        if not p.is_file() or p.suffix not in {".html", ".xml", ".js", ".css", ".svg", ".json", ".txt"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for s in secrets:
            if str(s) in text:
                found.append(f"{p.name}: contiene un valore privato della configurazione")
        if "/Users/" in text:
            found.append(f"{p.name}: contiene un percorso locale")
        m = EMAIL.search(text)
        if m:
            found.append(f"{p.name}: contiene un indirizzo email ({m.group(0)[:3]}…)")
    return found


def build(cfg: dict, db, out_dir=None, d=None, data=None, reports_dir=None, now: datetime | None = None) -> dict:
    """Genera il sito in `out_dir` (default `public_site.out_dir`). Scrive prima in una cartella accanto, controlla
    che non ci siano dati privati e poi la sostituisce a quella pubblicata (solo se ha il segno MARKER: mai una
    cartella qualunque). Restituisce {out_dir, pages, alerts, companies, seconds}."""
    t0 = datetime.now()
    s = settings(cfg)
    out = Path(out_dir or s["out_dir"])
    if not out.is_absolute():
        out = ROOT / out
    if out.exists() and any(p.name not in PRESERVE for p in out.iterdir()) and not (out / MARKER).exists():
        raise ValueError(f"La cartella {out.name} esiste e non è un sito generato: scegli un'altra cartella.")
    ctx = collect(cfg, db, d=d, data=data, reports_dir=reports_dir, now=now)
    json.dumps(ctx, allow_nan=False)  # un NaN o una data rimasta qui sarebbe un errore nel sito
    tmp = out.parent / f".{out.name}.nuovo"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        written = render(ctx, tmp)
        found = leaks(tmp, cfg)
        if found:
            raise ValueError("Sito non pubblicato, dati privati nell'output: " + "; ".join(sorted(set(found))[:5]))
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if out.exists():
        for name in PRESERVE:  # un clone di git (GitHub Pages) e il dominio personale restano dove sono
            if (out / name).exists():
                shutil.move(str(out / name), str(tmp / name))
        shutil.rmtree(out)
    tmp.rename(out)
    if not ctx["site"]["base_url"]:
        logger.warning("Sito: public_site.base_url vuoto, i feed RSS non hanno link assoluti")
    seconds = (datetime.now() - t0).total_seconds()
    logger.info("Sito pubblico: %d pagine in %s (%.1f s)", len(written), out, seconds)
    return {"out_dir": str(out), "pages": len(written), "alerts": len(ctx["alerts"]),
            "companies": len(ctx["companies"]), "seconds": seconds}
