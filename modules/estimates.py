"""
Stime degli analisti (consenso): utile per azione e ricavi dell'esercizio in corso e del prossimo, prezzi obiettivo.

Due fonti:
- import da CSV (la strada pulita per il lavoro: esportazioni dai sistemi della banca, es. FactSet o Bloomberg).
  Colonne: ticker, metric (eps | revenue | price_target), period (0y = esercizio in corso, +1y = prossimo, oppure
  FY2026...), value, e facoltative low, high, analysts, source, date (AAAA-MM-GG, default oggi).
- Yahoo (yfinance), solo su richiesta e solo per le aziende seguite o nella pipeline (centinaia di titoli darebbero
  "Too Many Requests"); mai in demo.

SOLO informazione: le stime non hanno uno storico point-in-time e NON entrano nel giudizio, nelle strategie né nei
backtest. Si salvano con la data dello scarico (`as_of`), così si vedono anche le revisioni nel tempo.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

COLUMNS = ["ticker", "period", "metric", "avg", "low", "high", "analysts", "year_ago", "growth", "currency",
           "source", "as_of"]
METRICS = {"eps": "Utile per azione", "revenue": "Ricavi", "price_target": "Prezzo obiettivo"}
PERIODS = {"0q": "trimestre in corso", "+1q": "trimestre prossimo", "0y": "esercizio in corso",
           "+1y": "esercizio prossimo"}


def parse_yahoo(ticker: str, raw: dict, as_of) -> pd.DataFrame:
    """Righe di COLUMNS dai prospetti di yfinance (`earnings_estimate`, `revenue_estimate`,
    `analyst_price_targets`)."""
    as_of = pd.Timestamp(as_of).normalize()
    rows = []
    for metric, key, ago in (("eps", "earnings_estimate", "yearAgoEps"), ("revenue", "revenue_estimate",
                                                                         "yearAgoRevenue")):
        df = raw.get(key)
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for period, r in df.iterrows():
            if str(period) not in PERIODS:
                continue
            rows.append({"ticker": ticker, "period": str(period), "metric": metric, "avg": _f(r.get("avg")),
                         "low": _f(r.get("low")), "high": _f(r.get("high")),
                         "analysts": _f(r.get("numberOfAnalysts")), "year_ago": _f(r.get(ago)),
                         "growth": _f(r.get("growth")), "currency": r.get("currency"), "source": "yahoo",
                         "as_of": as_of})
    pt = raw.get("analyst_price_targets")
    if isinstance(pt, dict) and _f(pt.get("mean")) == _f(pt.get("mean")):
        rows.append({"ticker": ticker, "period": "12m", "metric": "price_target", "avg": _f(pt.get("mean")),
                     "low": _f(pt.get("low")), "high": _f(pt.get("high")), "analysts": np.nan,
                     "year_ago": np.nan, "growth": np.nan, "currency": None, "source": "yahoo", "as_of": as_of})
    return pd.DataFrame(rows, columns=COLUMNS)


def fetch_yahoo(ticker: str) -> dict:
    """Le stime di Yahoo per un titolo (rete). Errore se Yahoo non restituisce nulla."""
    import warnings

    import yfinance as yf

    k = yf.Ticker(ticker)
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in ("earnings_estimate", "revenue_estimate", "analyst_price_targets"):
            try:
                out[name] = getattr(k, name)
            except Exception:  # noqa: BLE001 - una parte mancante non ferma le altre
                out[name] = None
    if all(v is None or (isinstance(v, pd.DataFrame) and v.empty) or (isinstance(v, dict) and not v)
           for v in out.values()):
        raise ValueError(f"Yahoo non ha stime per {ticker}")
    return out


def parse_csv(text: str, today=None) -> pd.DataFrame:
    """Stime da un CSV (separatore , o ;, decimali col punto). Errore chiaro se mancano colonne o valori."""
    sep = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep)
    df.columns = [str(c).strip().lower() for c in df.columns]
    need = {"ticker", "metric", "period", "value"}
    if not need <= set(df.columns):
        raise ValueError(f"colonne mancanti: {', '.join(sorted(need - set(df.columns)))} (servono ticker, metric, "
                         "period, value)")
    df["metric"] = df["metric"].astype(str).str.strip().str.lower()
    bad = sorted(set(df["metric"]) - set(METRICS))
    if bad:
        raise ValueError(f"metriche non riconosciute: {', '.join(bad)} (ammesse: {', '.join(METRICS)})")
    today = pd.Timestamp(today or pd.Timestamp.now()).normalize()
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else np.nan  # noqa: E731
    out = pd.DataFrame({
        "ticker": df["ticker"].astype(str).str.strip().str.upper(), "period": df["period"].astype(str).str.strip(),
        "metric": df["metric"], "avg": num("value"), "low": num("low"), "high": num("high"),
        "analysts": num("analysts"), "year_ago": np.nan, "growth": np.nan,
        "currency": df["currency"] if "currency" in df else None,
        "source": df["source"].astype(str) if "source" in df else "csv",
        "as_of": pd.to_datetime(df["date"]) if "date" in df else today,
    })[COLUMNS]
    if out["avg"].isna().any():
        raise ValueError(f"valori non numerici alle righe {', '.join(str(i + 2) for i in out.index[out['avg'].isna()])}")
    return out


def latest(est: pd.DataFrame) -> pd.DataFrame:
    """L'ultima stima per (ticker, periodo, metrica): a pari data vince un import da CSV su Yahoo."""
    if est.empty:
        return est
    rank = (est["source"] != "yahoo").astype(int)
    return (est.assign(_r=rank).sort_values(["as_of", "_r"])
            .drop_duplicates(["ticker", "period", "metric"], keep="last").drop(columns="_r"))


def fiscal_fraction(last_annual_end, today=None) -> float:
    """Quota già trascorsa dell'esercizio in corso (che chiude un anno dopo l'ultimo bilancio annuale), 0-1."""
    if last_annual_end is None or pd.isna(last_annual_end):
        return 0.5
    today = pd.Timestamp(today or pd.Timestamp.now())
    start = pd.Timestamp(last_annual_end)
    while start + pd.DateOffset(years=1) < today:  # bilancio annuale non ancora caricato: si va avanti di un anno
        start = start + pd.DateOffset(years=1)
    return float(np.clip((today - start).days / 365.25, 0, 1))


def next_twelve_months(est: pd.DataFrame, metric: str, fraction: float) -> float:
    """Stima dei prossimi 12 mesi: media dell'esercizio in corso e del prossimo pesata per il tempo che manca
    (il metodo usato dai fornitori di dati per i multipli "forward")."""
    e = est[est["metric"] == metric].set_index("period")["avg"]
    fy0, fy1 = e.get("0y", np.nan), e.get("+1y", np.nan)
    if not np.isfinite(fy1):
        return float(fy0) if np.isfinite(fy0) else np.nan
    if not np.isfinite(fy0):
        return float(fy1)
    return float((1 - fraction) * fy0 + fraction * fy1)


def summary(est: pd.DataFrame, price: float, market_cap: float, enterprise_value: float, last_annual_end,
            today=None) -> dict:
    """Multipli sulle stime dei prossimi 12 mesi e crescite attese, per la scheda."""
    est = latest(est)
    f = fiscal_fraction(last_annual_end, today)
    eps, rev = next_twelve_months(est, "eps", f), next_twelve_months(est, "revenue", f)
    g = lambda m, p: est[(est["metric"] == m) & (est["period"] == p)]["growth"]  # noqa: E731
    pt = est[est["metric"] == "price_target"]["avg"]
    ok = lambda x: x is not None and np.isfinite(x)  # noqa: E731
    out = {"eps_ntm": eps, "revenue_ntm": rev,
           "pe_ntm": price / eps if ok(price) and ok(eps) and eps > 0 else np.nan,
           "ev_sales_ntm": enterprise_value / rev if ok(enterprise_value) and ok(rev) and rev > 0 else np.nan,
           "eps_growth_fy0": float(g("eps", "0y").iloc[0]) if len(g("eps", "0y")) else np.nan,
           "eps_growth_fy1": float(g("eps", "+1y").iloc[0]) if len(g("eps", "+1y")) else np.nan,
           "revenue_growth_fy0": float(g("revenue", "0y").iloc[0]) if len(g("revenue", "0y")) else np.nan,
           "revenue_growth_fy1": float(g("revenue", "+1y").iloc[0]) if len(g("revenue", "+1y")) else np.nan,
           "price_target": float(pt.iloc[0]) if len(pt) else np.nan,
           "as_of": est["as_of"].max() if not est.empty else None,
           "sources": sorted(set(est["source"])) if not est.empty else []}
    out["peg"] = out["pe_ntm"] / (out["eps_growth_fy1"] * 100) \
        if ok(out["pe_ntm"]) and ok(out["eps_growth_fy1"]) and out["eps_growth_fy1"] > 0 else np.nan
    out["upside"] = out["price_target"] / price - 1 if ok(out["price_target"]) and ok(price) and price > 0 else np.nan
    return out


def revisions(est: pd.DataFrame, metric: str = "eps", period: str = "+1y") -> pd.Series:
    """Media delle stime nel tempo (una per data di scarico): le revisioni al rialzo o al ribasso."""
    e = est[(est["metric"] == metric) & (est["period"] == period)]
    return e.groupby("as_of")["avg"].last().sort_index() if not e.empty else pd.Series(dtype=float)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def refresh(db, tickers, max_age_days: int = 3, max_tickers: int = 60, fetcher=None, today=None,
            pause: float = 0.5) -> dict:
    """Scarica da Yahoo le stime dei `tickers` che non ne hanno di recenti (al massimo `max_tickers`, i più vecchi
    prima). Restituisce {aggiornate, errori}."""
    import time

    fetcher = fetcher or fetch_yahoo
    today = pd.Timestamp(today or pd.Timestamp.now()).normalize()
    tickers = list(dict.fromkeys(tickers))
    have = db.load_estimates(tickers)
    have = have[have["source"] == "yahoo"] if not have.empty else have
    last = have.groupby("ticker")["as_of"].max() if not have.empty else pd.Series(dtype="datetime64[ns]")
    due = [t for t in tickers if t not in last.index or (today - last[t]).days >= max_age_days]
    due.sort(key=lambda t: last.get(t, pd.Timestamp("1900-01-01")))
    done, errors = 0, []
    for t in due[:max_tickers]:
        try:
            rows = parse_yahoo(t, fetcher(t), today)
            if rows.empty:
                raise ValueError("nessuna stima")
            db.save_estimates(rows)
            done += 1
        except Exception as exc:  # noqa: BLE001 - un titolo senza stime non ferma gli altri
            errors.append(f"{t}: {exc}")
        if pause:
            time.sleep(pause)
    return {"aggiornate": done, "errori": errors, "in_attesa": max(len(due) - max_tickers, 0)}


def to_currency(est: pd.DataFrame, currency: str, fx=None) -> tuple[pd.DataFrame, list[str]]:
    """Stime di utile e ricavi nella valuta di quotazione `currency` (Yahoo dà le stime nella valuta di bilancio: per
    Tenaris, quotata in euro, sono in dollari). `fx(src, dst, day)` = cambio; se manca o fallisce, quelle righe si
    tolgono (meglio un multiplo vuoto che sbagliato). Restituisce (stime, note)."""
    if est.empty or "currency" not in est:
        return est, []
    cur = est["currency"].fillna(currency).astype(str)
    other = (cur != currency) & est["metric"].isin(["eps", "revenue"])
    if not other.any():
        return est, []
    out, notes, drop = est.copy(), [], []
    for (src, day), idx in out[other].groupby([cur[other], out.loc[other, "as_of"]]).groups.items():
        rate = None
        try:
            rate = float(fx(src, currency, pd.Timestamp(day))) if fx is not None else None
        except Exception:  # noqa: BLE001 - cambio non disponibile: le righe si tolgono
            rate = None
        if rate is None or not np.isfinite(rate) or rate <= 0:
            drop += list(idx)
            notes.append(f"stime in {src} senza cambio verso {currency}: utile e ricavi esclusi dai multipli")
            continue
        for c in ("avg", "low", "high", "year_ago"):
            out.loc[idx, c] = out.loc[idx, c] * rate
        out.loc[idx, "currency"] = currency
        notes.append(f"stime in {src} convertite in {currency} al cambio {rate:.4f}".replace(".", ","))
    return out.drop(index=drop), sorted(set(notes))
