"""Scheda azienda: funzione GP (prezzo a candele o linea con volumi), "perché questa decisione", fair value nel tempo."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from modules.config import DEFAULTS
from modules.dashboard_data import DashboardData
from ui.common import (ACTION_LABEL, CURRENCY_SYMBOL, DOWN, GRID, INK2, MUTED, SERIES, STATUS, TERMINAL, UP, _layout,
                       _line, pct)
from ui.components import empty_state, fmt, notice


PART_LABELS = {"regime": "Regime di mercato", "value": "Margine DCF", "multi_factor": "Multi-factor"}


def _s(v) -> str:
    """Punteggio 0-1 con due decimali in italiano (0,67)."""
    return fmt(v, "score")


def _why_section(d: DashboardData, ticker: str):
    b = d.confidence_breakdown(ticker)
    st.subheader("Perché questa decisione")
    if b is None:
        empty_state("Il titolo non era nell'ultima analisi", "La strategia automatica non l'ha valutato.")
        return
    if not b["evaluated"]:
        notice(f"{b['reasoning']}. La confidenza non è stata calcolata.", "info",
               title=f"{ACTION_LABEL.get(b['action'], b['action'])}:")
        return
    parts, w = b["parts"], b["weights"]
    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure()
        for i, key in enumerate(["regime", "value", "multi_factor"]):
            fig.add_trace(go.Bar(
                x=[parts[key]], y=["Confidenza"], orientation="h", name=f"{PART_LABELS[key]} (max {_s(w[key])})",
                marker=dict(color=SERIES[i], line=dict(color="rgba(255,255,255,1)", width=2)),
                text=[_s(parts[key])], textposition="inside", insidetextanchor="middle",
                hovertemplate=f"{PART_LABELS[key]}: %{{x:.3f}} su {_s(w[key])}<extra></extra>"))
        fig.add_vline(x=b["threshold"], line=dict(color=INK2, width=2, dash="dash"),
                      annotation_text=f"soglia {_s(b['threshold'])}", annotation_position="top")
        if b["cutoff"] is not None and abs(b["cutoff"] - b["threshold"]) > 0.01:
            fig.add_vline(x=b["cutoff"], line=dict(color=INK2, width=2, dash="dot"),
                          annotation_text=f"ultimo dei primi 25: {_s(b['cutoff'])}", annotation_position="top")
        fig.update_layout(barmode="stack", bargap=0.35,
                          legend=dict(orientation="h", traceorder="normal", yanchor="top", y=-0.55, x=0))
        fig.update_xaxes(range=[0, 1], title="Confidenza (0–1)")
        fig.update_yaxes(showticklabels=False)
        st.plotly_chart(_layout(fig, 250).update_layout(
            hovermode="closest", margin=dict(t=36, b=8),
            legend=dict(orientation="h", traceorder="normal", yanchor="top", y=-0.55, x=0)), width="stretch")
    with right:
        st.markdown(_why_text(b))


def _why_text(b: dict) -> str:
    parts, w, th = b["parts"], b["weights"], _s(b["threshold"])
    lines = [f"Confidenza **{_s(b['confidence'])}** con regime **{b['regime']}**: "
             f"regime {_s(parts['regime'])}/{_s(w['regime'])}, margine DCF {_s(parts['value'])}/{_s(w['value'])}, "
             f"multi-factor {_s(parts['multi_factor'])}/{_s(w['multi_factor'])}."]
    if b["action"] == "BUY":
        lines.append(f"Supera la soglia di {th} ed è tra i titoli con la confidenza più alta: **BUY**.")
        return "\n\n".join(lines)
    if b["max_possible"] < b["threshold"]:
        lines.append(f"Con il regime {b['regime']} la confidenza massima possibile è {_s(b['max_possible'])}, "
                     f"sotto la soglia di {th}: **nessun titolo può essere BUY**.")
        return "\n\n".join(lines)
    if b["target"] > b["threshold"] and b["confidence"] >= b["threshold"]:
        lines.append(f"Supera la soglia ({th}), ma per entrare tra i primi 25 serviva **{_s(b['target'])}**.")
    else:
        lines.append(f"Per un BUY serviva almeno **{_s(b['target'])}**.")
    margin_now = "non calcolato" if b["margin_now"] is None else fmt(b["margin_now"], "pct", digits=0, signed=True)
    if b["margin_needed"] is not None:
        lines.append(f"A parità del resto, bastava un **margine DCF di almeno {fmt(b['margin_needed'], 'pct', digits=0)}"
                     f"** (oggi {margin_now}).")
    elif b["value_needed"] > 1:
        lines.append(f"Neanche il punteggio DCF pieno basterebbe (margine oggi {margin_now}).")
    if 0 < b["mf_needed"] <= 1:
        lines.append(f"Oppure un multi-factor di almeno **{_s(b['mf_needed'])}** (oggi {_s(b['mf_score'])}).")
    elif b["mf_needed"] > 1:
        lines.append("Neanche il multi-factor pieno basterebbe da solo.")
    if b["margin_now"] is None:
        lines.append(f"Senza DCF il {fmt(w['value'], 'pct', digits=0)} della confidenza vale zero per costruzione: "
                     "per un titolo così è molto difficile diventare BUY.")
    return "\n\n".join(lines)


def _events(fig, d: DashboardData, ticker: str, view: pd.DataFrame, show_trades: bool, show_filings: bool):
    """Operazioni del backtest (sulla linea del prezzo) e depositi dei 10-K (linee verticali)."""
    start, end = view.index[0], view.index[-1]
    if show_filings:
        for day in d.filing_dates(ticker):
            if start <= day <= end:
                fig.add_vline(x=day, line=dict(color=MUTED, width=1, dash="dot"), row=1, col=1)
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name="Deposito 10-K",
                                 line=dict(color=MUTED, width=1, dash="dot")), row=1, col=1)
    if show_trades:
        trades = d.trades_for(ticker)
        trades = trades[(trades["date"] >= start) & (trades["date"] <= end)] if not trades.empty else trades
        styles = {  # (simbolo, colore, etichetta): mai solo il colore a distinguere
            "BUY": ("triangle-up", STATUS["BUY"], "Acquisto (backtest)"),
            "stop-loss": ("x", STATUS["BLOCKED"], "Stop-loss (backtest)"),
            "take-profit": ("star", "#eda100", "Take-profit (backtest)"),
            "SELL": ("triangle-down", INK2, "Vendita/riduzione (backtest)"),
        }
        if not trades.empty:
            kind = np.where(trades["side"] == "BUY", "BUY",
                            np.where(trades["reason"].isin(["stop-loss", "take-profit"]), trades["reason"], "SELL"))
            for k, (symbol, color, label) in styles.items():
                sub = trades[kind == k]
                if not sub.empty:
                    fig.add_trace(go.Scatter(
                        x=sub["date"], y=sub["price"], mode="markers", name=label,
                        marker=dict(symbol=symbol, size=11, color=color, line=dict(color="white", width=1.5)),
                        customdata=np.stack([sub["shares"], sub["value"]], axis=1),
                        hovertemplate=label + ": %{customdata[0]:,.1f} azioni a %{y:,.2f} $<extra></extra>"),
                        row=1, col=1)


def _fair_value_history(d: DashboardData, ticker: str):
    st.subheader("Prezzo e fair value nel tempo")
    if d.valuation(ticker)["dcf_skipped_sector"]:
        empty_state("Nessuna storia del fair value", "Settore escluso dal DCF (valuation.skip_sectors).")
        return
    hist = _fv_history(d, ticker, f"{d.run_id}-{d.data.last_date}")
    if hist.empty or hist["fair_value"].isna().all():
        note = hist["note"].dropna().iloc[-1] if not hist.empty and hist["note"].notna().any() else "dati insufficienti"
        empty_state("Fair value non calcolabile", f"{note[0].upper() + note[1:]}.")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35], vertical_spacing=0.06)
    _line(fig, hist.index, hist["price"], "Prezzo", SERIES[0], row=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist["fair_value"], name="Fair value (DCF di quel giorno)",
                             mode="lines+markers", line=dict(color=SERIES[1], width=2, shape="hv"),
                             marker=dict(size=6)), row=1, col=1)
    full = d.cfg["confidence"]["full_value_margin"]
    _line(fig, hist.index, hist["margin"], "Margine di sicurezza", SERIES[2], row=2)
    fig.add_hline(y=0, line=dict(color=MUTED, width=1), row=2, col=1)
    fig.add_hline(y=full, line=dict(color=INK2, width=1, dash="dash"), row=2, col=1,
                  annotation_text=f"punteggio DCF pieno ({fmt(full, 'pct', digits=0)})", annotation_position="top left")
    fig.update_yaxes(title_text="$", row=1, col=1)
    fig.update_yaxes(title_text="Margine", tickformat=".0%", row=2, col=1)
    st.plotly_chart(_layout(fig, 460), width="stretch")
    ok = hist.dropna(subset=["margin"])
    above = (ok["margin"] >= full).mean() if not ok.empty else float("nan")
    st.caption(
        f"Il DCF è ricalcolato ogni mese con i soli dati disponibili allora (prezzi passati, bilanci già depositati), "
        f"come faceva la strategia. Il fair value salta soprattutto quando esce un nuovo 10-K (linee grigie nel "
        f"grafico del prezzo); tra un 10-K e l'altro si muove poco, con beta e tassi. "
        f"Margine sopra il {fmt(full, 'pct', digits=0)} nel {fmt(above, 'pct', digits=0)} dei mesi.")


@st.cache_data(show_spinner="Ricalcolo il DCF mese per mese…")
def _fv_history(_d: DashboardData, ticker: str, key: str) -> pd.DataFrame:  # noqa: ARG001
    return _d.fair_value_history(ticker)




# ---------------------------------------------------------------------------------------------- GP: grafico del prezzo
PERIODS = ["1M", "3M", "6M", "YTD", "1A", "5A", "MAX"]
PERIOD_DAYS = {"1M": 21, "3M": 63, "6M": 126, "1A": 252, "5A": 1260}
LONG_PERIODS = {"5A", "MAX"}            # candele giornaliere illeggibili: predefinita la linea, candele aggregate
CHART_DEFAULTS = DEFAULTS["price_chart"]  # soglie del grafico: `price_chart:` in config.yaml
VOL_MA = 20
BAR_NAME = {"D": ("seduta", "sedute"), "W": ("settimana", "settimane"), "M": ("mese", "mesi")}


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


def _hover_text(bars: pd.DataFrame, cur: str, unit: str) -> list[str]:
    out = []
    for r in bars.itertuples():
        out.append(f"A {fmt(r.open, 'price')}  M {fmt(r.high, 'price')}  "
                   f"m {fmt(r.low, 'price')}  C {fmt(r.close, 'price')} {cur}<br>"
                   f"Var. {fmt(r.change, 'pct', digits=2, arrow=True)} sulla chiusura {unit} · "
                   f"Vol {fmt(r.volume, 'int')}")
    return out


def _missing_days(index: pd.DatetimeIndex) -> list[str]:
    """Giorni feriali senza sedute nel periodo (festivi): da saltare sull'asse insieme ai fine settimana."""
    if len(index) < 2:
        return []
    days = pd.bdate_range(index[0], index[-1]).difference(index.normalize())
    return [x.strftime("%Y-%m-%d") for x in days]


def _reference_lines(d: DashboardData, ticker: str) -> list[tuple[str, float, str]]:
    """Il tuo fair value (note), le tue soglie "compra sotto"/"vendi sopra" e i valori per scenario dell'ultima
    valutazione salvata (prezzi obiettivo). Terzo elemento: stile della linea ("buy"/"sell" = soglie)."""
    out = []
    fv = d.note(ticker).get("fair_value")
    if fv is not None and pd.notna(fv) and float(fv) > 0:
        out.append(("Il tuo fair value", float(fv), "dash"))
    try:
        diary = d.valuation_diary(ticker)
    except Exception:  # noqa: BLE001 - il diario è un di più: il grafico va disegnato comunque
        diary = pd.DataFrame()
    try:  # le tue soglie di prezzo (scheda: "Soglie di prezzo"; Le mie aziende)
        targets = d.db.load_price_targets()
    except Exception:  # noqa: BLE001 - tabella assente in un database vecchio
        targets = pd.DataFrame()
    if not targets.empty and ticker in set(targets["ticker"]):
        row = targets[targets["ticker"] == ticker].iloc[-1]
        for col, label, style in (("buy_below", "Compra sotto", "buy"), ("sell_above", "Vendi sopra", "sell")):
            v = pd.to_numeric(row.get(col), errors="coerce")
            if pd.notna(v) and float(v) > 0:
                out.append((label, float(v), style))
    if not diary.empty:
        last = diary.iloc[-1]
        for name, v in (last.get("per_scenario") or {}).items():
            if isinstance(v, (int, float)) and np.isfinite(v) and v > 0 and name != "Base":
                out.append((f"Scenario {name} ({fmt(last['created_at'], 'date')})", float(v), "dot"))
    return out


def _price_tab(d: DashboardData, ticker: str, px: pd.DataFrame):
    cur_code = d.currency(ticker)
    cur = CURRENCY_SYMBOL.get(cur_code, cur_code)
    o1, o2, o3, o4, o5, o6, o7 = st.columns([3.4, 2.4, 1.1, 0.9, 1.3, 1.5, 1], vertical_alignment="bottom")
    period = o1.segmented_control("Periodo", PERIODS, default="1A", key="gp_period") or "1A"
    style = o2.segmented_control("Grafico", ["Auto", "Candele", "Linea"], default="Auto", key="gp_style",
                                 help="Auto: candele fino a 1A, linea per 5A e MAX") or "Auto"
    if st.session_state.get("gp_bench"):  # nel confronto (base 100) le medie non si disegnano: spento e bloccato,
        # con un'altra chiave, così la scelta dell'utente resta per quando il confronto si toglie
        show_ma = o3.toggle("Medie", value=False, disabled=True, key="gp_ma_off",
                            help=f"Non disponibili nel confronto con {d.data.benchmark}")
    else:
        show_ma = o3.toggle("Medie", value=True, key="gp_ma", help="Medie mobili delle chiusure a 50 e 200 sedute")
    log_scale = o4.toggle("Log", key="gp_log", help="Scala logaritmica del prezzo")
    vs_bench = o5.toggle(f"vs {d.data.benchmark}", value=False, key="gp_bench",
                         help=f"Confronta con {d.data.benchmark}, base 100 all'inizio del periodo")
    if vs_bench:  # come le medie: spenti e bloccati nel confronto, con altre chiavi (la scelta dell'utente resta)
        off = f"Non disponibili nel confronto con {d.data.benchmark}"
        show_trades = o6.toggle("Operazioni", value=False, disabled=True, key="gp_trades_off", help=off)
        show_filings = o7.toggle("10-K", value=False, disabled=True, key="gp_10k_off", help=off)
    else:
        show_trades = o6.toggle("Operazioni", value=True, key="gp_trades",
                                help="Acquisti, vendite, stop-loss e take-profit del backtest")
        show_filings = o7.toggle("10-K", value=True, key="gp_10k", help="Date di deposito dei bilanci annuali alla SEC")

    start = period_start(px, period)
    view = px[px.index >= start]
    view = view.assign(drawdown=view["close"] / view["close"].cummax() - 1)  # dal massimo del periodo scelto

    s = period_summary(view)
    c = st.columns(6)
    c[0].metric("Rendimento", pct(s["return"]))
    c[1].metric("Massimo", f"{fmt(s['high'], 'price')} {cur}", help=f"il {fmt(s['high_date'], 'date')}")
    c[2].metric("Minimo", f"{fmt(s['low'], 'price')} {cur}", help=f"il {fmt(s['low_date'], 'date')}")
    c[3].metric("Volatilità annua", pct(s["volatility"], signed=False), help="Deviazione standard dei rendimenti "
                "giornalieri (logaritmici) del periodo × √252")
    c[4].metric("Dal massimo", pct(s["from_high"]), help="Ultima chiusura rispetto al massimo del periodo")
    c[5].metric("Drawdown massimo", pct(s["max_drawdown"]), help="Peggior discesa delle chiusure dal massimo precedente, "
                "nel periodo")

    if vs_bench:
        bench = d.benchmark().reindex(view.index).ffill()
        fig = go.Figure()
        _line(fig, view.index, view["close"] / view["close"].iloc[0] * 100, ticker, SERIES[0])
        _line(fig, bench.index, bench / bench.dropna().iloc[0] * 100, d.data.benchmark, SERIES[1])
        fig.update_yaxes(title="Base 100 all'inizio del periodo", type="log" if log_scale else "linear")
        fig.update_xaxes(hoverformat="%d/%m/%Y",
                         rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=_missing_days(view.index))])
        st.plotly_chart(_layout(fig, 460), width="stretch", config={"displayModeBar": False})
        st.caption(f"Ultima seduta {fmt(view.index[-1], 'date')}. Fine settimana e festivi tolti dall'asse.")
    else:
        notes = _candle_chart(d, ticker, px, view, period, style, show_ma, log_scale, show_trades, show_filings, cur)
        st.caption(" · ".join(notes))

    fig = go.Figure(go.Scatter(x=view.index, y=view["drawdown"], fill="tozeroy", mode="lines", name="Drawdown",
                               line=dict(color=STATUS["BLOCKED"], width=1.5), hovertemplate="%{y:.1%}"))
    fig.update_yaxes(tickformat=".0%", title="Dal massimo")
    fig.update_xaxes(hoverformat="%d/%m/%Y", rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=_missing_days(view.index))])
    st.plotly_chart(_layout(fig, 200, legend=False), width="stretch", config={"displayModeBar": False})


def _candle_chart(d: DashboardData, ticker: str, px: pd.DataFrame, view: pd.DataFrame, period: str, style: str,
                  show_ma: bool, log_scale: bool, show_trades: bool, show_filings: bool, cur: str) -> list[str]:
    """Prezzo (candele o linea) sopra, volumi sotto con lo stesso asse del tempo. Restituisce le note per la
    didascalia (barre aggregate, aperture mancanti, valori fuori scala)."""
    notes = []
    chart = chart_settings(d.cfg)
    want_candles = style == "Candele" or (style == "Auto" and period not in LONG_PERIODS)
    ohlc_ok = all(c in view for c in ("open", "high", "low"))
    missing = (view[["open", "high", "low"]].isna().any(axis=1).mean() if ohlc_ok else 1.0)
    if want_candles and missing > chart["max_missing_open"]:
        want_candles = False
        notes.append("apertura/massimo/minimo non disponibili per questo titolo: solo linea")
    freq = bar_frequency(px, period, chart["monthly_after_years"]) if want_candles else "D"
    bars = ohlc_bars(px, freq)
    bars = bars[bars.index >= view.index[0]]
    one, many = BAR_NAME[freq]
    if want_candles and freq != "D":
        notes.append(f"candele {'settimanali' if freq == 'W' else 'mensili'} (una per {one}: apertura della prima "
                     f"seduta, chiusura dell'ultima, massimo e minimo del gruppo, volume sommato)")
    elif want_candles and missing > 0:
        notes.append(f"{int(round(missing * len(view)))} sedute senza apertura: candela non disegnata")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24], vertical_spacing=0.03)
    hover = _hover_text(bars, cur, {"D": "precedente", "W": "della settimana prima", "M": "del mese prima"}[freq])
    if want_candles:
        fig.add_trace(go.Candlestick(
            x=bars.index, open=bars["open"], high=bars["high"], low=bars["low"], close=bars["close"], name=ticker,
            increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
            decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
            whiskerwidth=0.3, text=hover, hoverinfo="text", showlegend=False), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(x=bars.index, y=bars["close"], name="Chiusura", mode="lines",
                                 line=dict(color=SERIES[0], width=1.6), text=hover, hoverinfo="text"), row=1, col=1)
    if show_ma:
        for col, label, color in (("sma50", "Media 50 sedute", SERIES[1]), ("sma200", "Media 200 sedute", SERIES[2])):
            if col in view and view[col].notna().any():
                fig.add_trace(go.Scatter(x=view.index, y=view[col], name=label, mode="lines",
                                         line=dict(color=color, width=1.2),
                                         hovertemplate=f"{label}: %{{y:,.2f}}<extra></extra>"), row=1, col=1)

    lo = float(view["low"].min()) if ohlc_ok and view["low"].notna().any() else float(view["close"].min())
    hi = float(view["high"].max()) if ohlc_ok and view["high"].notna().any() else float(view["close"].max())
    out_of_range = []
    fv_lo, fv_hi = chart["fair_value_range"]
    for label, value, dash in _reference_lines(d, ticker):
        if fv_lo * lo <= value <= fv_hi * hi:
            color = {"dash": INK2, "buy": UP, "sell": DOWN}.get(dash, MUTED)
            line_dash = "longdash" if dash in ("buy", "sell") else dash
            fig.add_hline(y=value, line=dict(color=color, width=1.2, dash=line_dash), row=1, col=1,
                          annotation_text=f"{label} {fmt(value, 'price')}", annotation_position="top left",
                          annotation_font=dict(color=color, size=10))
        else:
            out_of_range.append(f"{label} {fmt(value, 'price', currency=d.currency(ticker))}")
    if out_of_range:
        notes.append("fuori scala, non disegnati: " + ", ".join(out_of_range))

    if bars["volume"].notna().any():
        by_change = bars["change"].fillna(0) >= 0
        up = np.where(bars["open"].notna(), bars["close"] >= bars["open"], by_change) if want_candles else by_change
        fig.add_trace(go.Bar(x=bars.index, y=bars["volume"], name="Volume", showlegend=False,
                             marker=dict(color=np.where(up, UP, DOWN), line=dict(width=0)),
                             hovertemplate="Volume %{y:,.0f}<extra></extra>"), row=2, col=1)
        if bars["vol_ma"].notna().any():
            fig.add_trace(go.Scatter(x=bars.index, y=bars["vol_ma"], name=f"Media volumi {VOL_MA} {many}",
                                     mode="lines", line=dict(color=INK2, width=1),
                                     hovertemplate=f"Media {VOL_MA} {many}: %{{y:,.0f}}<extra></extra>"), row=2, col=1)
    _events(fig, d, ticker, view, show_trades, show_filings)

    breaks = [dict(bounds=["sat", "mon"]), dict(values=_missing_days(view.index))] if freq == "D" else []
    fig.update_xaxes(rangeslider_visible=False, rangebreaks=breaks, hoverformat="%d/%m/%Y")
    fig.update_yaxes(title_text=f"Prezzo ({cur})", type="log" if log_scale else "linear", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    _layout(fig, 560)
    if TERMINAL:
        fig.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    notes.append(f"ultima seduta {fmt(view.index[-1], 'date')}")
    if freq == "D":
        notes.append("fine settimana e festivi tolti dall'asse")
    rule = "sopra o sotto l'apertura" if want_candles else "sopra o sotto la chiusura precedente"
    notes.append(f"volumi colorati come la barra: chiusura {rule}")
    return [n[0].upper() + n[1:] for n in notes]
