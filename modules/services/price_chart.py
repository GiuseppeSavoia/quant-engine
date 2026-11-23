"""Grafico del prezzo della scheda (funzione GP): periodi, barre (giornaliere o aggregate per settimana/mese), sintesi del
periodo, medie mobili, volumi, linee di riferimento dell'utente (fair value, soglie, scenari), depositi dei 10-K,
operazioni del backtest, confronto con il benchmark in base 100. Una sola definizione per la webapp Streamlit
(ui/pages/azienda/prezzo.py importa da qui) e per l'interfaccia web (api/: /companies/{t}/price)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.config import DEFAULTS
from modules.formats import fmt
from modules.services.base import Service

PERIODS = ["1M", "3M", "6M", "YTD", "1A", "5A", "MAX"]
PERIOD_DAYS = {"1M": 21, "3M": 63, "6M": 126, "1A": 252, "5A": 1260}
LONG_PERIODS = {"5A", "MAX"}            # candele giornaliere illeggibili: predefinita la linea, candele aggregate
CHART_DEFAULTS = DEFAULTS["price_chart"]  # soglie del grafico: `price_chart:` in config.yaml
STYLES = ["Auto", "Candele", "Linea"]
VOL_MA = 20
BAR_NAME = {"D": ("seduta", "sedute"), "W": ("settimana", "settimane"), "M": ("mese", "mesi")}
MAX_LINE_POINTS = 800                   # linea su periodi lunghi: oltre, chiusure settimanali


def period_start(px: pd.DataFrame, period: str) -> pd.Timestamp:
    """Primo giorno del periodo scelto (sulle sedute disponibili)."""
    if period == "YTD":
        first = px.index[px.index >= pd.Timestamp(px.index[-1].year, 1, 1)]
        return first[0] if len(first) else px.index[-1]
    days = PERIOD_DAYS.get(period)
    return px.index[-min(days, len(px))] if days else px.index[0]


def chart_settings(cfg: dict | None) -> dict:
    """`price_chart:` della configurazione sopra i default (monthly_after_years, max_missing_open, fair_value_range)."""
    return {**CHART_DEFAULTS, **((cfg or {}).get("price_chart") or {})}


def bar_frequency(px: pd.DataFrame, period: str, monthly_after_years: float | None = None) -> str:
    """D per i periodi fino a 1A; W per 5A (e MAX corto); M per MAX su più di `monthly_after_years` anni."""
    if period not in LONG_PERIODS:
        return "D"
    years = CHART_DEFAULTS["monthly_after_years"] if monthly_after_years is None else monthly_after_years
    if period == "MAX" and (px.index[-1] - px.index[0]).days > years * 365:
        return "M"
    return "W"


def ohlc_bars(px: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Barre OHLCV con variazione sulla chiusura precedente e media dei volumi su 20 barre. Le barre aggregate
    (settimana/mese) hanno come data l'ultima seduta del gruppo, così l'asse resta sui giorni di borsa veri."""
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in px]
    df = px[cols].copy()
    for c in ("open", "high", "low", "volume"):
        if c not in df:
            df[c] = np.nan
    if freq != "D":
        key = df.index.to_period("W-FRI" if freq == "W" else "M")
        g = df.assign(date=df.index).groupby(key)
        df = pd.DataFrame({
            "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
            "volume": g["volume"].sum(min_count=1), "date": g["date"].last(),
        }).set_index("date")
        df.index.name = None
    df["change"] = df["close"].pct_change()
    df["vol_ma"] = df["volume"].rolling(VOL_MA, min_periods=VOL_MA).mean()
    return df


def period_summary(view: pd.DataFrame) -> dict:
    """Sintesi del periodo: rendimento, massimo e minimo (sulle massime/minime se ci sono), volatilità annua dalle
    chiusure giornaliere, distanza dell'ultima chiusura dal massimo, drawdown massimo delle chiusure."""
    close = view["close"]
    high = view["high"] if "high" in view and view["high"].notna().any() else close
    low = view["low"] if "low" in view and view["low"].notna().any() else close
    rets = np.log(close).diff().dropna()
    hi = float(high.max())
    return {
        "return": float(close.iloc[-1] / close.iloc[0] - 1),
        "high": hi, "high_date": high.idxmax(), "low": float(low.min()), "low_date": low.idxmin(),
        "volatility": float(rets.std() * np.sqrt(252)) if len(rets) > 1 else np.nan,
        "from_high": float(close.iloc[-1] / hi - 1),
        "max_drawdown": float((close / close.cummax() - 1).min()),
    }


def reference_lines(ctx, ticker: str) -> list[tuple[str, float, str]]:
    """Il tuo fair value (note), le tue soglie "compra sotto"/"vendi sopra" e i valori per scenario dell'ultima
    valutazione salvata (prezzi obiettivo). Terzo elemento: stile della linea ("buy"/"sell" = soglie)."""
    out = []
    fv = ctx.note(ticker).get("fair_value")
    if fv is not None and pd.notna(fv) and float(fv) > 0:
        out.append(("Il tuo fair value", float(fv), "dash"))
    try:
        diary = ctx.valuation_diary(ticker)
    except Exception:  # noqa: BLE001 - il diario è un di più: il grafico va disegnato comunque
        diary = pd.DataFrame()
    target = ctx.price_target(ticker)
    for col, label, style in (("buy_below", "Compra sotto", "buy"), ("sell_above", "Vendi sopra", "sell")):
        v = target.get(col)
        if v is not None and v > 0:
            out.append((label, float(v), style))
    if not diary.empty:
        last = diary.iloc[-1]
        for name, v in (last.get("per_scenario") or {}).items():
            if isinstance(v, (int, float)) and np.isfinite(v) and v > 0 and name != "Base":
                out.append((f"Scenario {name} ({fmt(last['created_at'], 'date')})", float(v), "dot"))
    return out


class PriceChartService(Service):
    def price_chart(self, ticker: str, period: str = "1A", style: str = "Auto", bench: bool = False) -> dict:
        """Il grafico del prezzo pronto da disegnare. Valori allineati alle date delle barre. {"empty": True} senza
        prezzi; ValueError per periodo o stile sconosciuti."""
        if period not in PERIODS:
            raise ValueError(f"Periodo sconosciuto: {period} (ammessi: {', '.join(PERIODS)})")
        if style not in STYLES:
            raise ValueError(f"Grafico sconosciuto: {style} (ammessi: {', '.join(STYLES)})")
        ctx = self.ctx
        px = ctx.prices(ticker)
        base = {"ticker": ticker, "currency": ctx.currency(ticker), "benchmark": self.data.benchmark or "",
                "periods": PERIODS, "styles": STYLES, "period": period, "style": style}
        if px.empty or px["close"].dropna().empty:
            return {**base, "empty": True}
        chart = chart_settings(self.cfg)
        view = px[px.index >= period_start(px, period)]
        notes = []
        want_candles = style == "Candele" or (style == "Auto" and period not in LONG_PERIODS)
        ohlc_ok = all(c in view for c in ("open", "high", "low"))
        missing = float(view[["open", "high", "low"]].isna().any(axis=1).mean()) if ohlc_ok else 1.0
        if want_candles and missing > chart["max_missing_open"]:
            want_candles = False
            notes.append("apertura/massimo/minimo non disponibili per questo titolo: solo linea")
        freq = bar_frequency(px, period, chart["monthly_after_years"]) if want_candles else "D"
        if not want_candles and len(view) > MAX_LINE_POINTS:
            freq = "W"
        bars = ohlc_bars(px, freq)
        bars = bars[bars.index >= view.index[0]]
        one, many = BAR_NAME[freq]
        if want_candles and freq != "D":
            notes.append(f"candele {'settimanali' if freq == 'W' else 'mensili'} (una per {one}: apertura della prima "
                         f"seduta, chiusura dell'ultima, massimo e minimo del gruppo, volume sommato)")
        elif not want_candles and freq != "D":
            notes.append("linea sulle chiusure settimanali (periodo lungo)")
        elif want_candles and missing > 0:
            notes.append(f"{int(round(missing * len(view)))} sedute senza apertura: candela non disegnata")
        at = lambda s: s.reindex(bars.index, method="ffill") if len(s) else pd.Series(np.nan, index=bars.index)  # noqa: E731
        drawdown = view["close"] / view["close"].cummax() - 1
        lo = float(view["low"].min()) if ohlc_ok and view["low"].notna().any() else float(view["close"].min())
        hi = float(view["high"].max()) if ohlc_ok and view["high"].notna().any() else float(view["close"].max())
        fv_lo, fv_hi = chart["fair_value_range"]
        refs = [{"label": label, "value": value, "style": st, "in_range": bool(fv_lo * lo <= value <= fv_hi * hi)}
                for label, value, st in reference_lines(ctx, ticker)]
        out_of_range = [f"{r['label']} {fmt(r['value'], 'price', currency=base['currency'])}" for r in refs if not r["in_range"]]
        if out_of_range:
            notes.append("fuori scala, non disegnati: " + ", ".join(out_of_range))
        start, end = view.index[0], view.index[-1]
        filings = [d for d in ctx.filing_dates(ticker) if start <= d <= end]
        trades = []
        try:
            tr = ctx.trades_for(ticker)
        except Exception:  # noqa: BLE001 - senza backtest nessuna operazione
            tr = pd.DataFrame()
        if not tr.empty:
            tr = tr[(tr["date"] >= start) & (tr["date"] <= end)]
            labels = {"BUY": "Acquisto (backtest)", "stop-loss": "Stop-loss (backtest)",
                      "take-profit": "Take-profit (backtest)", "SELL": "Vendita/riduzione (backtest)"}
            for _, r in tr.iterrows():
                kind = "BUY" if r["side"] == "BUY" else r["reason"] if r.get("reason") in ("stop-loss", "take-profit") else "SELL"
                trades.append({"date": r["date"], "price": r["price"], "kind": kind, "label": labels[kind],
                               "shares": r.get("shares")})
        bench_out = None
        if bench:
            b = ctx.benchmark()
            if len(b):
                b = b.reindex(view.index).ffill()
                first = b.dropna()
                if len(first):
                    bench_out = {"stock": (at(view["close"]) / view["close"].iloc[0] * 100).tolist(),
                                 "benchmark": (at(b) / first.iloc[0] * 100).tolist()}
        notes.append(f"ultima seduta {fmt(view.index[-1], 'date')}")
        rule = "sopra o sotto l'apertura" if want_candles else "sopra o sotto la chiusura precedente"
        notes.append(f"volumi colorati come la barra: chiusura {rule}")
        return {**base, "empty": False, "candles": want_candles, "freq": freq, "bar": {"one": one, "many": many},
                "dates": list(bars.index), "open": bars["open"].tolist(), "high": bars["high"].tolist(),
                "low": bars["low"].tolist(), "close": bars["close"].tolist(), "volume": bars["volume"].tolist(),
                "change": bars["change"].tolist(), "vol_ma": bars["vol_ma"].tolist(),
                "sma50": at(view["sma50"]).tolist() if "sma50" in view else [],
                "sma200": at(view["sma200"]).tolist() if "sma200" in view else [],
                "drawdown": at(drawdown).tolist(), "summary": period_summary(view), "references": refs,
                "filings": filings, "trades": trades, "bench": bench_out,
                "notes": [n[0].upper() + n[1:] for n in notes]}
