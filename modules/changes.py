"""
"Cosa è cambiato" in un'azienda: confronto point-in-time tra due momenti, con frasi brevi ordinate per importanza.

Idea: un'istantanea (`snapshot`) è quello che si sapeva di un'azienda a un giorno (`MarketData.as_of`): l'ultimo
periodo di bilancio già depositato, le voci principali (ricavi, utile, FCF, debito netto, azioni) e le metriche del
catalogo (`metrics.fundamental_metrics`, nessun numero ridefinito qui) al prezzo di quel giorno. Due istantanee si
confrontano voce per voce (`compare`): valore prima/dopo, variazione (relativa per gli importi e i multipli, in punti
per le percentuali), se è rilevante secondo le soglie (`alerts.change_thresholds` in config.yaml) e se va nel verso
buono o cattivo (`better` del catalogo).

`what_changed` fa il caso tipico: l'ultimo bilancio depositato contro
  - il periodo noto subito prima del deposito (di solito il trimestre precedente, ultimi 12 mesi contro ultimi 12 mesi);
  - lo stesso periodo di un anno prima (per le aziende europee, solo annuali, è l'unico confronto: esercizio su
    esercizio);
più la valutazione al prezzo del deposito contro quella al prezzo di oggi e, se ci sono, le ipotesi dell'utente
(fair value e DCF nelle note, scenari dell'ultima valutazione a leve) contro i numeri realizzati.

Banche, assicurazioni e REIT: margini, FCF e debito netto dicono poco, si usano le metriche del gruppo "Settore"
(P/TBV, ROTCE, margine di interesse, efficienza, accantonamenti, loss ratio, FFO). Una voce che manca in una delle
due istantanee non compare (mai uno zero al posto di un dato mancante).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from modules.market_data import MarketData, is_ttm
from modules.metrics import MAX_PRICE_GAP_DAYS, METRIC_BY_KEY, fundamental_metrics

# ------------------------------------------------------------------ voci confrontate
# kind: "rel" = variazione relativa (importi, azioni, multipli), "pt" = differenza in punti (percentuali e rapporti)
# weight: importanza nel riassunto (a parità di superamento della soglia)


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    fmt: str            # money | shares | pct | x | num
    better: str | None  # high | low | None
    kind: str           # rel | pt
    weight: float = 1.0


LINE_ITEMS = {
    "revenue": Item("revenue", "Ricavi", "money", "high", "rel", 1.2),
    "net_income": Item("net_income", "Utile netto", "money", "high", "rel", 1.0),
    "free_cash_flow": Item("free_cash_flow", "Free cash flow", "money", "high", "rel", 1.0),
    "net_debt": Item("net_debt", "Debito netto", "money", "low", "rel", 0.9),
    "shares": Item("shares", "Azioni in circolazione", "shares", "low", "rel", 0.9),
}


def _metric_item(key: str, weight: float = 1.0, kind: str | None = None) -> Item:
    m = METRIC_BY_KEY[key]
    return Item(key, m.label, m.fmt, m.better, kind or ("pt" if m.fmt == "pct" else "rel"), weight)


# il rapporto cassa/utile è un multiplo (x) ma si legge in differenza assoluta (da 1,2 a 0,8 = -0,4)
METRIC_ITEMS = {
    "gross_margin": _metric_item("gross_margin", 0.9),
    "operating_margin": _metric_item("operating_margin", 1.3),
    "net_margin": _metric_item("net_margin", 0.9),
    "roic": _metric_item("roic", 1.1),
    "cash_conversion": _metric_item("cash_conversion", 0.8, kind="pt"),
    "pe": _metric_item("pe", 0.7),
    "fcf_yield": _metric_item("fcf_yield", 0.7),
    # settore
    "ptbv": _metric_item("ptbv", 0.8), "rotce": _metric_item("rotce", 1.2), "nim": _metric_item("nim", 1.1),
    "efficiency_ratio": _metric_item("efficiency_ratio", 1.0), "provision_ratio": _metric_item("provision_ratio", 1.0),
    "loss_ratio": _metric_item("loss_ratio", 1.2), "ffo_yield": _metric_item("ffo_yield", 1.0),
    "p_ffo": _metric_item("p_ffo", 0.7),
}
ITEMS = {**LINE_ITEMS, **METRIC_ITEMS}

# voci per tipo di azienda (riconosciuto dalle metriche di settore disponibili, o dal settore GICS)
PROFILES = {
    "general": ["revenue", "gross_margin", "operating_margin", "net_margin", "net_income", "free_cash_flow",
                "cash_conversion", "roic", "net_debt", "shares", "pe", "fcf_yield"],
    "bank": ["revenue", "net_income", "nim", "efficiency_ratio", "provision_ratio", "rotce", "ptbv", "shares", "pe"],
    "insurance": ["revenue", "net_income", "loss_ratio", "net_margin", "rotce", "ptbv", "shares", "pe"],
    "financial": ["revenue", "net_income", "net_margin", "rotce", "ptbv", "shares", "pe"],
    "reit": ["revenue", "operating_margin", "net_income", "ffo_yield", "p_ffo", "net_debt", "shares"],
}
PROFILE_LABELS = {"general": "azienda industriale/commerciale", "bank": "banca", "insurance": "assicurazione",
                  "financial": "finanziaria", "reit": "immobiliare (REIT)"}
# valutazione: prezzo del deposito contro prezzo di oggi, stessi bilanci
VALUATION_KEYS = {"general": ["pe", "fcf_yield"], "bank": ["pe", "ptbv"], "insurance": ["pe", "ptbv"],
                  "financial": ["pe", "ptbv"], "reit": ["p_ffo", "ffo_yield"]}

# soglie predefinite (config.yaml alerts.change_thresholds le sostituisce): relative per "rel", in punti per "pt"
DEFAULT_THRESHOLDS = {
    "revenue": 0.10, "net_income": 0.20, "free_cash_flow": 0.25, "net_debt": 0.25, "shares": 0.02,
    "gross_margin": 0.02, "operating_margin": 0.02, "net_margin": 0.02, "roic": 0.03, "cash_conversion": 0.30,
    "pe": 0.15, "fcf_yield": 0.01, "ptbv": 0.15, "rotce": 0.03, "nim": 0.0025, "efficiency_ratio": 0.03,
    "provision_ratio": 0.02, "loss_ratio": 0.03, "ffo_yield": 0.01, "p_ffo": 0.15,
}
# debito netto: la variazione relativa non ha senso vicino a zero; si misura anche sui ricavi (vedi _delta)
NET_DEBT_REVENUE_SHARE = 0.10
YEAR_TOLERANCE_DAYS = 45
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF"}
COMPARISON_LABELS = {"previous": "sul periodo precedente", "year_ago": "su un anno prima", "between": "",
                     "valuation": "dal deposito a oggi"}


# ------------------------------------------------------------------ formati in italiano
def _it(text: str) -> str:
    """1,234.5 -> 1.234,5"""
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_value(fmt: str, value: float, currency: str = "USD") -> str:
    if value is None or not np.isfinite(value):
        return "–"
    if fmt == "pct":  # sotto il 5% (es. margine di interesse delle banche) servono due decimali
        return _it(f"{value * 100:.2f}%" if 0 < abs(value) < 0.05 else f"{value * 100:.1f}%")
    if fmt == "x":
        return _it(f"{value:.1f}x")
    if fmt in ("money", "shares"):
        unit = "" if fmt == "shares" else " " + CURRENCY_SYMBOLS.get(currency, currency)
        for div, name in ((1e12, " T"), (1e9, " mld"), (1e6, " mln")):
            if abs(value) >= div:
                return _it(f"{value / div:,.1f}") + name + unit
        return _it(f"{value:,.0f}") + unit
    return _it(f"{value:.2f}")


def fmt_delta(item: Item, delta: float, currency: str = "USD") -> str:
    if item.kind == "pt":
        if item.fmt == "pct":
            return _it(f"{delta * 100:+.2f}" if abs(delta) < 0.005 else f"{delta * 100:+.1f}") + " punti"
        return _it(f"{delta:+.2f}")
    return _it(f"{delta * 100:+.1f}%")


# ------------------------------------------------------------------ istantanee
def single(data: MarketData, tickers) -> MarketData:
    """MarketData ridotto ad alcuni titoli (+ benchmark e tasso): `as_of` diventa rapido anche sul database vero."""
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    cols = [c for c in dict.fromkeys([*tickers, data.benchmark, data.risk_free_ticker]) if c and c in data.close]
    sub = lambda df: df[df["ticker"].isin(tickers)] if not df.empty and "ticker" in df else df  # noqa: E731
    return MarketData(close=data.close[cols], high=data.high.reindex(columns=cols),
                      low=data.low.reindex(columns=cols), volume=data.volume.reindex(columns=cols),
                      financials=sub(data.financials), benchmark=data.benchmark,
                      risk_free_ticker=data.risk_free_ticker, has_manual=data.has_manual,
                      insiders=sub(data.insiders), holdings=data.holdings, quarterly=sub(data.quarterly),
                      open=data.open.reindex(columns=cols) if not data.open.empty else data.open)


def price_at(data: MarketData, ticker: str, day) -> float:
    """Ultima chiusura entro `day` (al massimo MAX_PRICE_GAP_DAYS giorni prima, come le metriche)."""
    px = data.prices(ticker)
    px = px[px.index <= pd.Timestamp(day)]
    if px.empty or (pd.Timestamp(day) - px.index[-1]).days > MAX_PRICE_GAP_DAYS + 2:  # +2: fine settimana
        return np.nan
    return float(px.iloc[-1])


def filing_dates(data: MarketData, ticker: str) -> pd.DataFrame:
    """Periodi di bilancio del titolo con la data del PRIMO deposito e il tipo (10-K = esercizio, 10-Q = 12 mesi da
    un trimestrale), in ordine di chiusura. Le correzioni successive non cambiano la data."""
    fin = data.financials
    if fin.empty:
        return pd.DataFrame(columns=["period_end", "filed", "form"])
    f = fin[fin["ticker"] == ticker]
    if f.empty:
        return pd.DataFrame(columns=["period_end", "filed", "form"])
    src = f["source"].astype(str) if "source" in f else pd.Series("sec", index=f.index)
    f = f.assign(ttm=is_ttm(f), sec=src.str.startswith("sec"))
    out = f.groupby("period_end").agg(filed=("filed", "min"), ttm=("ttm", "all"), sec=("sec", "any")).reset_index()
    # 10-K/10-Q solo per i depositi SEC (le europee hanno relazioni annuali ESEF, i dati a mano non sono documenti)
    out["form"] = np.select([out["ttm"] & out["sec"], out["sec"], out["ttm"]],
                            ["10-Q", "10-K", "ultimi 12 mesi"], "bilancio annuale")
    return out.drop(columns=["ttm", "sec"]).sort_values("period_end").reset_index(drop=True)


def profile_of(values: dict, sector: str | None = None) -> str:
    """Tipo di azienda dalle metriche di settore disponibili (o dal settore GICS)."""
    ok = lambda k: k in values and np.isfinite(values[k])  # noqa: E731
    if ok("nim"):
        return "bank"
    if ok("loss_ratio"):
        return "insurance"
    if ok("ffo_yield") or ok("p_ffo") or sector == "Real Estate":
        return "reit"
    if sector == "Financials":
        return "financial"
    return "general"


def snapshot(data: MarketData, ticker: str, day, price: float | None = None) -> dict:
    """Quello che si sapeva di `ticker` il giorno `day`: periodo dell'ultimo bilancio, voci principali e metriche al
    prezzo di quel giorno (o a `price`). {} se non c'è nessun bilancio."""
    day = pd.Timestamp(day)
    snap = data.as_of(day)
    st = snap.fundamentals(ticker)
    if st.empty:
        return {}
    px = price_at(snap, ticker, day) if price is None else float(price)
    values = {k: v for k, v in fundamental_metrics(snap, ticker, px).items() if k != "statement_date"}
    cur, latest = st.iloc[-1], st.ffill().iloc[-1]
    g = lambda row, k: float(row.get(k, np.nan))  # noqa: E731
    values.update(revenue=g(cur, "revenue"), net_income=g(cur, "net_income"),
                  free_cash_flow=g(cur, "free_cash_flow"), shares=snap.last_shares(st))
    debt, cash = g(latest, "total_debt"), g(latest, "cash")
    values["net_debt"] = debt - cash if np.isfinite(debt) and np.isfinite(cash) else np.nan
    values = {k: (float(v) if v is not None and np.isfinite(v) else np.nan) for k, v in values.items()
              if isinstance(v, (int, float, np.floating, np.integer)) or v is None}
    periods = filing_dates(snap, ticker)
    last = periods[periods["period_end"] == st.index[-1]]
    return {"day": day, "price": px, "period_end": st.index[-1],
            "filed": last["filed"].iloc[0] if not last.empty else pd.NaT,
            "form": last["form"].iloc[0] if not last.empty else None, "values": values}


# ------------------------------------------------------------------ confronto
@dataclass
class Change:
    key: str
    label: str
    comparison: str        # previous | year_ago | between | valuation
    before: float
    after: float
    delta: float           # relativa (kind rel) o in punti (kind pt)
    kind: str
    relevant: bool
    favorable: bool | None
    score: float           # |delta| / soglia x peso: ordina il riassunto
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def thresholds_from(cfg: dict | None) -> dict:
    return {**DEFAULT_THRESHOLDS, **((cfg or {}).get("alerts", {}).get("change_thresholds") or {})}


def _delta(item: Item, before: float, after: float, revenue: float) -> tuple[float, float]:
    """(variazione da mostrare, variazione da confrontare con la soglia)."""
    if item.kind == "pt":
        d = after - before
        return d, d
    if before == 0:
        return np.nan, np.nan
    rel = (after - before) / abs(before)
    if item.key == "net_debt" and np.isfinite(revenue) and revenue > 0:
        # vicino a zero la variazione relativa esplode: conta anche quanto pesa sui ricavi
        return rel, np.sign(rel) * min(abs(rel), abs(after - before) / revenue / NET_DEBT_REVENUE_SHARE
                                       * DEFAULT_THRESHOLDS["net_debt"])
    return rel, rel


def _phrase(item: Item, before: float, after: float, delta: float, comparison: str, currency: str) -> str:
    a, b = fmt_value(item.fmt, before, currency), fmt_value(item.fmt, after, currency)
    if item.kind == "rel" and (before < 0 or after < 0) and item.fmt == "money":
        diff = fmt_value("money", after - before, currency)
        change = ("+" if after >= before else "") + diff
    else:
        change = fmt_delta(item, delta, currency)
    where = COMPARISON_LABELS.get(comparison, "")
    return f"{item.label} da {a} a {b}: {change}" + (f" {where}" if where else "")


def compare(before: dict, after: dict, keys: list[str], comparison: str, thresholds: dict,
            currency: str = "USD") -> list[Change]:
    """Confronta due istantanee (o due dizionari di valori) sulle voci `keys`. Voci assenti in una delle due: omesse."""
    vb = before.get("values", before)
    va = after.get("values", after)
    revenue = va.get("revenue", np.nan)
    out = []
    for key in keys:
        item = ITEMS[key]
        b, a = vb.get(key, np.nan), va.get(key, np.nan)
        if b is None or a is None or not (np.isfinite(b) and np.isfinite(a)):
            continue
        shown, measured = _delta(item, b, a, revenue)
        if not np.isfinite(shown):
            continue
        thr = float(thresholds.get(key, DEFAULT_THRESHOLDS.get(key, 0.1)))
        score = abs(measured) / thr * item.weight if thr > 0 else 0.0
        relevant = bool(thr > 0 and abs(measured) >= thr)
        favorable = None
        if item.better and a != b:
            favorable = bool((a > b) == (item.better == "high"))
        out.append(Change(key, item.label, comparison, float(b), float(a), float(shown), item.kind, relevant,
                          favorable, float(score), _phrase(item, b, a, shown, comparison, currency)))
    return out


def compare_dates(data: MarketData, ticker: str, start, end, cfg: dict | None = None, sector: str | None = None,
                  currency: str = "USD") -> dict:
    """Cosa è cambiato tra due date qualsiasi: quello che si sapeva il giorno `start` contro il giorno `end`
    (bilanci e prezzi di allora). {} se in una delle due date non c'era nessun bilancio."""
    sub = single(data, ticker)
    a, b = snapshot(sub, ticker, start), snapshot(sub, ticker, end)
    if not a or not b:
        return {}
    profile = profile_of(b["values"], sector)
    items = compare(a, b, PROFILES[profile], "between", thresholds_from(cfg), currency)
    return {"ticker": ticker, "profile": profile, "before": _head(a), "after": _head(b), "items": items,
            "summary": summarize(items)}


def _head(snap: dict) -> dict:
    return {k: snap.get(k) for k in ("day", "price", "period_end", "filed", "form")}


def summarize(changes: list[Change], limit: int | None = None) -> list[dict]:
    """Solo le variazioni rilevanti, dalla più importante; per ogni voce la più forte tra i confronti."""
    best: dict[str, Change] = {}
    for c in changes:
        if c.relevant and (c.key not in best or c.score > best[c.key].score):
            best[c.key] = c
    ordered = sorted(best.values(), key=lambda c: (-c.score, c.key))
    out = [{"key": c.key, "text": c.text, "favorable": c.favorable, "comparison": c.comparison,
            "score": round(c.score, 2)} for c in ordered]
    return out[:limit] if limit else out


# ------------------------------------------------------------------ la tesi dell'utente
def _scenario_assumptions(valuation: dict | None) -> dict:
    """Crescita dei ricavi e margine obiettivo dell'ultima valutazione a leve: scenario "Base" se c'è, altrimenti
    media pesata con le probabilità."""
    if not valuation:
        return {}
    scen = valuation.get("scenarios") or {}
    if not isinstance(scen, dict) or not scen:
        return {}
    if "Base" in scen:
        lv = scen["Base"]
        return {k: float(lv[k]) for k in ("growth", "target_margin", "margin_years") if lv.get(k) is not None}
    probs = valuation.get("probabilities") or {}
    tot = sum(max(float(probs.get(n, 0)), 0) for n in scen)
    out = {}
    for k in ("growth", "target_margin", "margin_years"):
        vals = [(float(lv[k]), max(float(probs.get(n, 0)), 0)) for n, lv in scen.items() if lv.get(k) is not None]
        if vals and tot > 0:
            out[k] = sum(v * w for v, w in vals) / tot
    return out


def _years(a, b) -> float:
    return (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25


def thesis_check(note: dict | None, valuation: dict | None, values: dict, year_ago: dict, price: float,
                 gap: float = 0.03, currency: str = "USD", start: dict | None = None, period_end=None,
                 as_of=None) -> list[dict]:
    """Ipotesi dell'utente contro i numeri realizzati, solo dove esistono entrambi.
    - fair value delle note contro il prezzo di oggi;
    - crescita del FCF del DCF semplice (note.assumptions.growth) contro la crescita del FCF in un anno;
    - crescita dei ricavi e margine obiettivo dell'ultima valutazione a leve contro il PERCORSO atteso dalla
      valutazione: `start` = quello che si sapeva il giorno della valutazione (snapshot: period_end, values),
      `period_end` = periodo dell'ultimo bilancio. Il margine obiettivo si raggiunge in `margin_years` anni: oggi ci si
      aspetta il margine di partenza più la parte di strada già fatta (interpolazione lineare, come il modello); i
      ricavi attesi sono quelli di partenza cresciuti al tasso ipotizzato per il tempo trascorso. Senza bilanci nuovi
      dalla valutazione, o senza il punto di partenza prima della fine dell'orizzonte, la tesi è "in corso".
    `on_track`: True = realizzato non peggiore dell'ipotesi di oltre `gap` (punti all'anno per la crescita), False =
    sotto, None = in corso (ancora niente da confrontare)."""
    out = []
    pct = lambda v: _it(f"{v * 100:.1f}%")  # noqa: E731
    fv = (note or {}).get("fair_value")
    if fv is not None and pd.notna(fv) and np.isfinite(price) and price > 0:
        margin = float(fv) / price - 1
        out.append({"key": "fair_value", "assumed": float(fv), "actual": float(price), "on_track": margin >= 0,
                    "text": f"Prezzo {fmt_value('num', price)} contro il tuo fair value {fmt_value('num', float(fv))}: "
                            f"margine {_it(f'{margin * 100:+.0f}%')}"})

    def growth(key):
        b, a = year_ago.get(key, np.nan), values.get(key, np.nan)
        return a / b - 1 if np.isfinite(a) and np.isfinite(b) and b > 0 else np.nan

    assumptions = (note or {}).get("assumptions") or {}
    g_fcf = growth("free_cash_flow")
    if assumptions.get("growth") is not None and np.isfinite(g_fcf):
        g = float(assumptions["growth"])
        out.append({"key": "fcf_growth", "assumed": g, "actual": float(g_fcf), "on_track": g_fcf >= g - gap,
                    "text": f"Crescita del FCF: ipotizzata {pct(g)} l'anno nel tuo DCF, realizzata {pct(g_fcf)} "
                            "nell'ultimo anno"})
    lv = _scenario_assumptions(valuation)
    start = start or {}
    sv = start.get("values") or {}
    # anni di bilanci passati dalla valutazione (fine del periodo di allora -> fine dell'ultimo periodo)
    elapsed = _years(start["period_end"], period_end) if start.get("period_end") is not None \
        and period_end is not None else None
    since = (valuation or {}).get("data_as_of") or (valuation or {}).get("created_at")
    calendar = _years(since, as_of) if since is not None and as_of is not None and pd.notna(since) else None
    new_data = elapsed is not None and elapsed > 0
    waiting = " (in corso: nessun bilancio nuovo dalla tua valutazione)"

    r0, r = sv.get("revenue", np.nan), values.get("revenue", np.nan)
    if "growth" in lv:
        g = lv["growth"]
        if new_data and np.isfinite(r0) and np.isfinite(r) and r0 > 0:
            done, expected = r / r0 - 1, (1 + g) ** elapsed - 1
            out.append({"key": "revenue_growth", "assumed": g, "actual": float(done), "expected": float(expected),
                        "on_track": r >= r0 * max(1 + g - gap, 0.0) ** elapsed,
                        "text": f"Ricavi: crescita ipotizzata {pct(g)} l'anno nella tua valutazione, dalla valutazione "
                                f"{_it(f'{done * 100:+.1f}%')} in {_months(elapsed)} (attesa "
                                f"{_it(f'{expected * 100:+.1f}%')})"})
        elif start and np.isfinite(r0):
            out.append({"key": "revenue_growth", "assumed": g, "actual": np.nan, "on_track": None,
                        "text": f"Ricavi: crescita ipotizzata {pct(g)} l'anno nella tua valutazione" + waiting})
    m = values.get("operating_margin", np.nan)
    if "target_margin" in lv and np.isfinite(m):
        target, years = lv["target_margin"], max(float(lv.get("margin_years", 5)), 1.0)
        m0 = sv.get("operating_margin", np.nan)
        head = f"Margine operativo: obiettivo {pct(target)} in {_it(f'{years:.0f}')} anni nella tua valutazione"
        if new_data and np.isfinite(m0):
            expected = m0 + (target - m0) * min(elapsed / years, 1.0)
            out.append({"key": "operating_margin", "assumed": target, "actual": float(m), "expected": float(expected),
                        "on_track": m >= expected - gap,
                        "text": f"{head} (partenza {pct(m0)}): atteso ora {pct(expected)}, oggi {pct(m)}"})
        elif not np.isfinite(m0) and calendar is not None and calendar >= years:  # orizzonte passato
            out.append({"key": "operating_margin", "assumed": target, "actual": float(m), "expected": target,
                        "on_track": m >= target - gap, "text": f"{head}: orizzonte passato, oggi {pct(m)}"})
        else:
            out.append({"key": "operating_margin", "assumed": target, "actual": float(m), "on_track": None,
                        "text": f"{head}, oggi {pct(m)}" + (waiting if np.isfinite(m0) else " (in corso)")})
    for t in out:
        t["on_track"] = None if t["on_track"] is None else bool(t["on_track"])  # niente numpy.bool_ (si confronta con is)
        t["text"] += "" if t["on_track"] is not False else " (sotto la tua ipotesi)"
    return out


def _months(years: float) -> str:
    n = max(int(round(years * 12)), 1)
    return f"{n} {'mese' if n == 1 else 'mesi'}"


def thesis_inputs(db, ticker: str) -> tuple[dict, dict | None]:
    """(nota con fair value e ipotesi, ultima valutazione a leve salvata) dal database; vuote se non ci sono."""
    note = db.load_note(ticker) if hasattr(db, "load_note") else {}
    valuation = None
    if hasattr(db, "load_valuations"):
        v = db.load_valuations(ticker)
        if not v.empty:
            valuation = v.iloc[-1].to_dict()
    return note or {}, valuation


# ------------------------------------------------------------------ il caso tipico
def what_changed(data: MarketData, ticker: str, as_of=None, cfg: dict | None = None, sector: str | None = None,
                 currency: str = "USD", note: dict | None = None, valuation: dict | None = None,
                 db=None) -> dict:
    """Ultimo bilancio depositato (entro `as_of`, default l'ultima data dei prezzi) contro il periodo noto prima del
    deposito e contro lo stesso periodo di un anno prima; valutazione al deposito contro oggi; tesi dell'utente
    (note e ultima valutazione, passate o lette da `db`). {} se l'azienda non ha bilanci.

    Restituisce: ticker, profile, latest {period_end, filed, form}, comparisons {previous, year_ago}: {period_end,
    filed, items: [Change]}, valuation [Change], thesis [dict], summary [dict] (rilevanti, dalla più importante)."""
    sub = single(data, ticker)
    as_of = pd.Timestamp(as_of) if as_of is not None else sub.last_date
    if as_of is None:
        return {}
    known = sub.as_of(as_of)
    periods = filing_dates(known, ticker)
    st = known.fundamentals(ticker)
    if st.empty or periods.empty:
        return {}
    last_end = st.index[-1]
    row = periods[periods["period_end"] == last_end]
    filed = row["filed"].iloc[0] if not row.empty else periods["filed"].max()
    thr = thresholds_from(cfg)
    gap = float((cfg or {}).get("alerts", {}).get("thesis_gap", 0.03))

    after = snapshot(sub, ticker, filed)
    if not after or after["period_end"] != last_end:  # stesso giorno di un altro deposito: si usa quello che c'è
        after = snapshot(sub, ticker, as_of)
        after["price"] = price_at(sub, ticker, filed)
    profile = profile_of(after["values"], sector)
    keys = PROFILES[profile]
    comparisons = {}

    before = snapshot(sub, ticker, filed - pd.Timedelta(days=1))
    # periodo dell'anno prima: lo stesso trimestre (o esercizio) chiuso ~12 mesi prima
    target = last_end - pd.DateOffset(years=1)
    cand = periods[(periods["period_end"] < last_end)
                   & ((periods["period_end"] - target).abs() <= pd.Timedelta(days=YEAR_TOLERANCE_DAYS))]
    year = {}
    if not cand.empty:
        y = cand.iloc[((cand["period_end"] - target).abs()).argmin()]
        year = snapshot(sub, ticker, y["filed"])
        if year and year["period_end"] != y["period_end"]:
            year = {}
    if before and before["period_end"] < last_end and not (year and before["period_end"] == year["period_end"]):
        comparisons["previous"] = {**_head(before), "items": compare(before, after, keys, "previous", thr, currency)}
    if year:
        comparisons["year_ago"] = {**_head(year), "items": compare(year, after, keys, "year_ago", thr, currency)}

    # valutazione: stessi bilanci, prezzo del deposito contro prezzo di `as_of`
    today = snapshot(sub, ticker, as_of)
    val_items = []
    if today and today["period_end"] == last_end:
        val_items = compare(after, today, VALUATION_KEYS[profile], "valuation", thr, currency)

    if db is not None and note is None and valuation is None:
        note, valuation = thesis_inputs(db, ticker)
    price_now = today.get("price", np.nan) if today else np.nan
    start = {}
    since = (valuation or {}).get("data_as_of") or (valuation or {}).get("created_at")
    if since is not None and pd.notna(since):  # quello che si sapeva il giorno della valutazione (punto di partenza)
        start = snapshot(sub, ticker, min(pd.Timestamp(since), as_of))
    thesis = thesis_check(note, valuation, after["values"], year.get("values", {}) if year else {}, price_now, gap,
                          currency, start=start, period_end=last_end, as_of=as_of) if (note or valuation) else []

    all_items = [c for comp in comparisons.values() for c in comp["items"]] + val_items
    return {"ticker": ticker, "as_of": as_of, "profile": profile,
            "latest": {"period_end": last_end, "filed": filed, "form": after.get("form"), "price": after["price"]},
            "price_now": price_now, "comparisons": comparisons, "valuation": val_items, "thesis": thesis,
            "summary": summarize(all_items) + [{"key": f"thesis:{t['key']}", "text": t["text"],
                                                "favorable": bool(t["on_track"]), "comparison": "thesis",
                                                "score": 0.0} for t in thesis if t["on_track"] is False]}


def to_text(result: dict, limit: int = 8) -> str:
    """Riassunto leggibile (log, notifiche, rapporto): intestazione + una riga per variazione rilevante."""
    if not result:
        return "nessun bilancio"
    lt = result["latest"]
    head = (f"{result['ticker']} — {lt.get('form') or 'bilancio'} al {lt['period_end']:%d/%m/%Y}, depositato il "
            f"{lt['filed']:%d/%m/%Y} ({PROFILE_LABELS[result['profile']]})")
    lines = [head]
    for s in result["summary"][:limit]:
        sign = {True: "+", False: "-", None: "·"}[s["favorable"]]
        lines.append(f"  [{sign}] {s['text']}")
    if len(lines) == 1:
        lines.append("  nessuna variazione oltre le soglie")
    return "\n".join(lines)
