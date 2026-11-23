"""
Motore di avvisi con storico: a fine aggiornamento dei dati (main.py) valuta le regole per le aziende seguite, quelle
in portafoglio e quelle con una soglia di prezzo, e registra nel database (tabella `alerts`) solo gli avvisi NUOVI.

Deduplicazione: ogni avviso ha una chiave (ticker, codice, chiave). Finché la condizione resta vera l'avviso resta
"attivo" (cleared_at vuoto) e non si ripete; quando la condizione cessa viene chiuso (cleared_at = giorno della
valutazione) e, se torna vera, ricompare come avviso nuovo. La chiave distingue i casi che devono ripresentarsi anche
senza interruzione: un bilancio nuovo (periodo), un 8-K nuovo (accession), una soglia cambiata dall'utente.

Codici (attivabili in config.yaml, `alerts.codes`):
  PX-  prezzo sotto la soglia d'acquisto dell'utente (tabella price_targets)
  PX+  prezzo sopra la soglia di vendita dell'utente
  FV   prezzo sotto il fair value dell'utente (note) con il margine `alerts.fair_value_margin`
  FV~  vicino al fair value (come la home; spento di default)
  CHG  bilancio nuovo con cambi rilevanti (modules/changes.py)
  INS  acquisti sul mercato degli insider
  MOV  movimento forte in un mese; MOV1 in un giorno
  52S  lontano dal massimo di 52 settimane (spento di default)
  REV  revisione scaduta (scheda di ricerca o diario delle decisioni)
  8K   8-K con voci gravi (sec_filings.IMPORTANT_ITEMS)
  VRD  giudizio cambiato (storico in verdict_history; regole contro il rumore in verdict_history.verdict_changes)

Le funzioni `*_alert` qui sotto sono le stesse regole degli avvisi della home (DashboardData.watchlist_table): una
sola definizione per ogni avviso. `evaluate` è pura rispetto all'orologio: il giorno lo passa chi la chiama.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from modules import changes
from modules.market_data import MarketData

logger = logging.getLogger(__name__)

PRICE_GAP_DAYS = 7  # un prezzo più vecchio di così (giorni di calendario) non fa scattare avvisi di prezzo


# ------------------------------------------------------------------ regole (condivise con la home)
def fair_value_alert(price: float, fair_value: float | None, near: float = 0.05,
                     margin: float = 0.0) -> tuple[str, str] | None:
    """FV se fair value / prezzo - 1 >= `margin`; FV~ se il prezzo è sopra il fair value di meno di `near`."""
    if fair_value is None or not np.isfinite(price) or price <= 0 or not np.isfinite(fair_value):
        return None
    m = fair_value / price - 1
    if m >= margin:
        return "FV", f"sotto il tuo fair value: margine {m:+.0%}"
    if m < 0 and m > -near:
        return "FV~", f"vicino al tuo fair value ({m:+.0%})"
    return None


def filing_alert(filed, form: str | None, as_of, days: int) -> tuple[str, str] | None:
    if filed is None or pd.isna(filed) or as_of is None or (pd.Timestamp(as_of) - pd.Timestamp(filed)).days > days:
        return None
    return "DOC", f"nuovo {form} depositato il {pd.Timestamp(filed):%d/%m}"


def move_alert(ret: float, threshold: float, period: str = "in un mese", code: str = "MOV") -> tuple[str, str] | None:
    if ret is None or not np.isfinite(ret) or abs(ret) < threshold:
        return None
    return code, f"{ret:+.0%} {period}"


def drawdown_alert(dd: float, threshold: float) -> tuple[str, str] | None:
    if dd is None or not np.isfinite(dd) or dd > threshold:
        return None
    return "52S", f"{dd:.0%} dal massimo di 52 settimane"


def insider_alert(buys: pd.DataFrame, min_value: float, days: int) -> tuple[str, str] | None:
    """`buys`: acquisti sul mercato (codice P) di UNA azienda già filtrati sulla finestra."""
    if buys is None or buys.empty or buys["value"].sum() < min_value:
        return None
    return "INS", (f"{buys['owner_cik'].nunique()} insider hanno comprato per {buys['value'].sum() / 1e6:,.1f} mln $ "
                   f"negli ultimi {days} giorni")


def review_alert(review, as_of) -> tuple[str, str] | None:
    if not review or pd.isna(review) or as_of is None or pd.Timestamp(review) > pd.Timestamp(as_of):
        return None
    return "REV", f"revisione della tesi prevista dal {pd.Timestamp(review):%d/%m/%Y}"


def margin_alert(now: float, before: float, drop: float) -> tuple[str, str] | None:
    if not (np.isfinite(now) and np.isfinite(before)) or before - now < drop:
        return None
    return "MRG", f"margine operativo in calo: {before:.1%} → {now:.1%}"


# ------------------------------------------------------------------ chi seguire
def followed_tickers(db) -> list[str]:
    """Aziende seguite + posizioni aperte del portafoglio + aziende con una soglia di prezzo."""
    out = list(db.load_watchlist())
    tx = db.load_transactions()
    if not tx.empty:
        q = tx[tx["kind"].isin(["BUY", "SELL"])].assign(
            signed=lambda d: np.where(d["kind"] == "BUY", 1.0, -1.0) * d["quantity"].fillna(0.0))
        held = q.groupby("ticker")["signed"].sum()
        out += [t for t in held.index if held[t] > 1e-9]
    targets = db.load_price_targets()
    out += list(targets["ticker"]) if not targets.empty else []
    return list(dict.fromkeys(out))


def _currency(db, ticker: str) -> str:
    for df in (db.load_custom_companies(), db.load_foreign()):
        if not df.empty and "currency" in df and ticker in set(df["ticker"]):
            cur = df.set_index("ticker")["currency"].get(ticker)
            if isinstance(cur, str) and cur:
                return cur
    return "USD"


# ------------------------------------------------------------------ condizioni
def _alert(ticker: str, code: str, key: str, message: str, data: dict | None = None) -> dict:
    return {"ticker": ticker, "code": code, "key": str(key), "message": message, "data": data or {}}


def conditions(db, data: MarketData, cfg: dict, today, tickers: list[str] | None = None) -> tuple[list[dict], set]:
    """Tutti gli avvisi la cui condizione è vera il giorno `today` (anche quelli già registrati) e l'insieme dei
    ticker valutati senza errori (solo per questi si chiudono gli avvisi non più veri)."""
    a = cfg.get("alerts", {})
    w = cfg.get("watchlist", {})
    codes = set(a.get("codes") or [])
    today = pd.Timestamp(today)
    tickers = followed_tickers(db) if tickers is None else list(tickers)
    if not tickers:
        return [], set()
    sub = changes.single(data, tickers).as_of(today)
    with_prices = [t for t in tickers if t in sub.close]
    from modules.metrics import price_metrics

    pm = price_metrics(sub, with_prices) if with_prices else pd.DataFrame()
    notes = db.load_notes().set_index("ticker")
    targets = db.load_price_targets().set_index("ticker")
    research = db.load_research()
    reviews = dict(zip(research["ticker"], research["review_date"])) if not research.empty else {}
    journal = db.load_decisions_journal()
    events = pd.DataFrame()
    if "8K" in codes:
        events = db.load_events(tickers, since=(today - pd.Timedelta(days=int(a.get("event_days", 7)))).strftime(
            "%Y-%m-%d"))
    comp = db.load_companies(only_index=False)
    sectors = dict(zip(comp["ticker"], comp["sector"])) if not comp.empty else {}
    ins = sub.insiders
    ins_days = int(a.get("insider_days", 30))
    if not ins.empty:
        ins = ins[ins["code"].eq("P") & (ins["filed"] > today - pd.Timedelta(days=ins_days))]

    vrd = {}
    if "VRD" in codes:
        try:
            from modules.verdict_history import change_conditions

            vrd = change_conditions(db, tickers, cfg, today)
        except Exception as exc:  # noqa: BLE001 - senza storico restano gli avvisi VRD già attivi
            logger.warning("Cambi di giudizio non valutati: %s", exc)
            act = db.active_alerts()
            for r in act[act["code"] == "VRD"].itertuples(index=False):
                vrd.setdefault(r.ticker, []).append((r.key, r.message, {}))

    out, ok = [], set()
    for t in tickers:
        try:
            out += _ticker_conditions(t, db, sub, cfg, today, codes, pm, notes, targets, reviews, journal, events,
                                      sectors, ins, ins_days, a, w)
            out += [_alert(t, "VRD", k, m, dt) for k, m, dt in vrd.get(t, [])]
            ok.add(t)
        except Exception as exc:  # noqa: BLE001 - un'azienda con dati strani non deve fermare le altre
            logger.warning("Avvisi di %s non valutati: %s", t, exc)
    return [x for x in out if x["code"] in codes], ok


def _ticker_conditions(t, db, sub, cfg, today, codes, pm, notes, targets, reviews, journal, events, sectors, ins,
                       ins_days, a, w) -> list[dict]:
    out = []
    px_series = sub.prices(t)
    fresh = not px_series.empty and (today - px_series.index[-1]).days <= PRICE_GAP_DAYS
    price = float(px_series.iloc[-1]) if fresh else np.nan
    r = pm.loc[t] if t in pm.index else pd.Series(dtype=float)

    # soglie di prezzo dell'utente
    if t in targets.index and np.isfinite(price):
        buy, sell = targets.at[t, "buy_below"], targets.at[t, "sell_above"]
        if pd.notna(buy) and price <= float(buy):
            out.append(_alert(t, "PX-", f"{float(buy):g}", f"prezzo {price:,.2f} sotto la tua soglia d'acquisto "
                                                            f"{float(buy):,.2f}", {"price": price, "buy_below": buy}))
        if pd.notna(sell) and price >= float(sell):
            out.append(_alert(t, "PX+", f"{float(sell):g}", f"prezzo {price:,.2f} sopra la tua soglia di vendita "
                                                             f"{float(sell):,.2f}", {"price": price, "sell_above": sell}))
    # fair value delle note
    fv = notes["fair_value"].get(t) if t in notes.index else None
    fv = float(fv) if fv is not None and pd.notna(fv) else None
    hit = fair_value_alert(price, fv, w.get("near_fair_value", 0.05), float(a.get("fair_value_margin", 0.0)))
    if hit:
        out.append(_alert(t, hit[0], f"{fv:.2f}", hit[1], {"price": price, "fair_value": fv}))
    # movimenti
    hit = move_alert(r.get("ret_1m", np.nan), float(a.get("big_move_1m", 0.15)))
    if hit and fresh:
        out.append(_alert(t, "MOV", "su" if r["ret_1m"] > 0 else "giu", hit[1], {"ret_1m": float(r["ret_1m"])}))
    if fresh and len(px_series) >= 2:
        d1 = float(px_series.iloc[-1] / px_series.iloc[-2] - 1)
        hit = move_alert(d1, float(a.get("big_move_1d", 0.07)), f"il {px_series.index[-1]:%d/%m}", "MOV1")
        if hit:
            out.append(_alert(t, "MOV1", f"{px_series.index[-1]:%Y-%m-%d}", hit[1], {"ret_1d": d1}))
    hit = drawdown_alert(r.get("drawdown_52w", np.nan), w.get("drawdown_alert", -0.25))
    if hit and fresh:
        out.append(_alert(t, "52S", "52s", hit[1]))
    # insider
    if not ins.empty:
        mine = ins[ins["ticker"] == t]
        hit = insider_alert(mine, float(a.get("insider_min_value", 100000)), ins_days)
        if hit:
            out.append(_alert(t, "INS", f"{mine['filed'].max():%Y-%m-%d}", hit[1],
                              {"value": float(mine["value"].sum()), "buyers": int(mine["owner_cik"].nunique())}))
    # revisioni scadute
    review = reviews.get(t)
    hit = review_alert(review, today)
    if hit:
        out.append(_alert(t, "REV", f"ricerca-{pd.Timestamp(review):%Y-%m-%d}", hit[1]))
    if not journal.empty:
        due = journal[(journal["ticker"] == t) & journal["review_date"].notna() & (journal["review_date"] <= today)
                      & journal["reviewed_at"].isna()]
        for j in due.itertuples(index=False):
            out.append(_alert(t, "REV", f"diario-{j.id}", f"revisione della decisione {j.action} del {j.date:%d/%m/%Y} "
                                                         f"prevista dal {j.review_date:%d/%m/%Y}"))
    # 8-K gravi
    if not events.empty:
        from modules.sec_filings import describe_items, important

        for e in events[(events["ticker"] == t) & (events["filed"] <= today)].itertuples(index=False):
            if important(e.items):
                out.append(_alert(t, "8K", e.accession, f"8-K del {e.filed:%d/%m}: {describe_items(e.items)}",
                                  {"items": e.items}))
    # bilancio nuovo con cambi rilevanti / documento nuovo / margine in calo
    if codes & {"CHG", "DOC", "MRG"}:
        out += _filing_conditions(t, db, sub, cfg, today, codes, a, w, sectors)
    return out


def _filing_conditions(t, db, sub, cfg, today, codes, a, w, sectors) -> list[dict]:
    out = []
    periods = changes.filing_dates(sub, t)
    if periods.empty:
        return out
    last = periods.iloc[-1]
    days = int(a.get("new_filing_days", 14))
    hit = filing_alert(last["filed"], last["form"], today, int(w.get("new_filing_days", 14)))
    if hit and "DOC" in codes:
        out.append(_alert(t, "DOC", f"{last['period_end']:%Y-%m-%d}", hit[1]))
    if "MRG" in codes:
        st = sub.fundamentals(t)
        if not st.empty and {"operating_income", "revenue"} <= set(st.columns):
            prev = sub.year_ago(st)
            margin = lambda row: row.get("operating_income", np.nan) / row["revenue"] \
                if row.get("revenue", 0) > 0 else np.nan  # noqa: E731
            hit = margin_alert(float(margin(st.iloc[-1])), float(margin(prev)) if len(prev) else np.nan,
                               w.get("margin_drop", 0.03))
            if hit:
                out.append(_alert(t, "MRG", f"{st.index[-1]:%Y-%m-%d}", hit[1]))
    if "CHG" in codes and (today - last["filed"]).days <= days:
        res = changes.what_changed(sub, t, as_of=today, cfg=cfg, sector=sectors.get(t), currency=_currency(db, t),
                                   db=db)
        if res and res["summary"]:
            n = int(a.get("summary_lines", 3))
            lt = res["latest"]
            text = "; ".join(s["text"] for s in res["summary"][:n])
            out.append(_alert(t, "CHG", f"{lt['period_end']:%Y-%m-%d}",
                              f"nuovo {lt['form']} (periodo al {lt['period_end']:%d/%m/%Y}): {text}",
                              {"summary": res["summary"], "thesis": res["thesis"], "form": lt["form"],
                               "period_end": f"{lt['period_end']:%Y-%m-%d}", "filed": f"{lt['filed']:%Y-%m-%d}"}))
    return out


# ------------------------------------------------------------------ valutazione con storico
def evaluate(db, data: MarketData, cfg: dict, today, tickers: list[str] | None = None) -> list[dict]:
    """Valuta le regole il giorno `today`, registra gli avvisi nuovi, chiude quelli la cui condizione non è più vera.
    Restituisce gli avvisi nuovi (con `id`). Nessuna lettura dell'orologio: la data la decide chi chiama."""
    if not cfg.get("alerts", {}).get("enabled", True):
        return []
    stamp = pd.Timestamp(today).isoformat(timespec="seconds")
    current, evaluated = conditions(db, data, cfg, today, tickers)
    active = db.active_alerts()
    active_keys = {(r.ticker, r.code, r.key): int(r.id) for r in active.itertuples(index=False)}
    now_keys = {(c["ticker"], c["code"], c["key"]) for c in current}
    gone = [i for k, i in active_keys.items() if k not in now_keys and k[0] in evaluated]
    if gone:
        db.clear_alerts(gone, stamp)
    seen, new = set(), []
    for c in current:
        k = (c["ticker"], c["code"], c["key"])
        if k in active_keys or k in seen:
            continue
        seen.add(k)
        new.append(c)
    if new:
        for c, i in zip(new, db.add_alerts(new, stamp)):
            c["id"] = i
            c["created_at"] = stamp
    return new


def notification_text(alerts, limit: int = 4) -> str:
    """Una riga per la notifica del Mac: quanti avvisi nuovi e i primi (ticker, codice, messaggio accorciato)."""
    rows = alerts.to_dict("records") if isinstance(alerts, pd.DataFrame) else list(alerts or [])
    if not rows:
        return ""
    parts = []
    for r in rows[:limit]:
        msg = str(r.get("message") or "")
        parts.append(f"{r['ticker']} {r['code']}: " + (msg if len(msg) <= 60 else msg[:59] + "…"))
    more = f" (+{len(rows) - limit})" if len(rows) > limit else ""
    return f"{len(rows)} avvisi nuovi: " + " | ".join(parts) + more


# ------------------------------------------------------------------ presentazione (webapp e interfaccia web)
# tono di ogni codice (verde = a favore di chi vuole comprare, rosso = attenzione, giallo = da guardare)
ALERT_TONES = {"PX-": "pos", "FV": "pos", "FV~": "pos", "INS": "pos", "PX+": "neg", "8K": "neg", "CHG": "warn",
               "REV": "warn", "52S": "warn", "MRG": "warn", "DOC": "info", "MOV": "warn", "MOV1": "warn",
               "VRD": "info"}  # VRD: giudizio cambiato (verdict_history)
# ordine di importanza nella pagina Oggi; i movimenti di prezzo stanno a parte (riquadro richiuso)
ALERT_PRIORITY = ["PX-", "PX+", "FV", "FV~", "VRD", "CHG", "INS", "REV", "8K"]
PRICE_MOVES = {"MOV", "MOV1", "52S"}
# codici i cui messaggi hanno numeri all'inglese (1,234.5 e 26.8%); CHG viene già in italiano, 8K ha numeri di voce
ENGLISH_NUMBER_CODES = {"PX-", "PX+", "FV", "FV~", "MOV", "MOV1", "52S", "INS", "MRG", "DOC", "REV"}


def alert_tone(code: str, data: dict | None = None) -> str:
    """Tono del distintivo; MOV/MOV1 secondo il verso del movimento."""
    tone = ALERT_TONES.get(code, "neutral")
    if code in ("MOV", "MOV1") and data:
        try:
            r = float(data.get("ret_1m", data.get("ret_1d")))
        except (TypeError, ValueError):
            return "neutral"
        tone = "neutral" if r != r else ("pos" if r > 0 else "neg")  # r != r: NaN
    return tone


def alert_message_it(code: str, message) -> str:
    """Messaggio con i numeri in italiano (solo per i codici che li scrivono all'inglese)."""
    from modules.formats import it_number

    msg = str(message or "")
    return it_number(msg) if code in ENGLISH_NUMBER_CODES else msg
