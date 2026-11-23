"""Report in reports/: CSV per i dati grezzi e una pagina HTML leggibile."""
from __future__ import annotations

import base64
import html
import io
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:32px auto;
padding:0 16px;color:#1d1d1f;background:#fff}
h1{font-size:24px;margin-bottom:4px}h2{font-size:18px;margin-top:32px}
.sub{color:#6e6e73;margin-top:0}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:6px 8px;border-bottom:1px solid #e5e5ea;text-align:right;white-space:nowrap}
th{background:#f5f5f7;font-weight:600}td:first-child,th:first-child{text-align:left}
td.l{text-align:left;white-space:normal;min-width:280px}td.s{text-align:left}
.wrap{overflow-x:auto;margin-top:8px}
.BUY{color:#0a7f3f;font-weight:600}.SKIP{color:#6e6e73}.BLOCKED{color:#c4161c;font-weight:600}
.note{background:#fff8e1;border-left:4px solid #f5a623;padding:10px 14px;font-size:13px}
img{max-width:100%}
"""

_PCT_COLS = {"margin_of_safety", "wacc", "growth", "implied_growth", "weight", "stress_portfolio",
             "stress_benchmark", "stress_coverage"}
_NUM2 = {"confidence", "threshold", "value_score", "mf_score", "momentum", "quality", "volatility_score", "beta"}


def _fmt(col: str, v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    if col in _PCT_COLS:
        return f"{v:.1%}"
    if col in _NUM2:
        return f"{v:.2f}"
    if col in {"price", "fair_value", "reference_price", "limit_price", "stop_loss", "take_profit"}:
        return f"{v:,.2f}"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return html.escape(str(v))


def _table(df: pd.DataFrame, cols: list[str], labels: dict[str, str]) -> str:
    cols = [c for c in cols if c in df.columns]
    head = "".join(f"<th>{labels.get(c, c)}</th>" for c in cols)
    body = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            cls = f' class="{v}"' if c == "action" else (
                ' class="l"' if c == "reasoning" else (' class="s"' if c == "sector" else ""))
            cells.append(f"<td{cls}>{_fmt(c, v)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='wrap'><table><tr>{head}</tr>{''.join(body)}</table></div>"


def _page(title: str, subtitle: str, sections: str) -> str:
    return (f"<!doctype html><html lang='it'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><style>{_CSS}</style></head>"
            f"<body><h1>{html.escape(title)}</h1><p class='sub'>{html.escape(subtitle)}</p>{sections}</body></html>")


DECISION_LABELS = {
    "ticker": "Ticker", "sector": "Settore", "action": "Decisione", "confidence": "Confidenza", "threshold": "Soglia",
    "price": "Prezzo", "fair_value": "Fair value", "margin_of_safety": "Margine", "wacc": "WACC",
    "beta": "Beta", "growth": "Crescita usata", "implied_growth": "Crescita implicita",
    "mf_score": "Multi-factor", "momentum": "Momentum", "quality": "Quality",
    "volatility_score": "Low vol", "reasoning": "Motivazione",
}


def _subtitle(text: str, version: str | None) -> str:
    return f"{text} · {version}" if version else text


QUALITY_LABELS = {"severity": "Gravità", "check": "Controllo", "tickers": "Titoli", "ticker": "Ticker",
                  "detail": "Dettaglio"}


def _quality_section(issues: pd.DataFrame | None, run_id: str) -> str:
    from modules.data_quality import summary

    if issues is None:
        return ""
    if issues.empty:
        return "<h2>Qualità dei dati</h2><p>Nessun problema trovato.</p>"
    errors = issues[issues["severity"] == "errore"]
    detail = pd.concat([errors, issues[issues["severity"] != "errore"]]).head(40)
    more = (f"<p class='sub'>Mostrati 40 problemi su {len(issues)}: elenco completo in "
            f"qualita_dati_{run_id}.csv</p>") if len(issues) > 40 else ""
    return ("<h2>Qualità dei dati</h2>" + _table(summary(issues), ["severity", "check", "tickers"], QUALITY_LABELS)
            + _table(detail, ["ticker", "severity", "check", "detail"], QUALITY_LABELS) + more)


RISK_MEASURES = [  # (chiave, etichetta, formato)
    ("exposure", "Investito", "pct"), ("positions", "Posizioni", "int"),
    ("effective_positions", "Posizioni equivalenti (concentrazione)", "num1"),
    ("largest_weight", "Peso massimo di un titolo", "pct"),
    ("volatility", "Volatilità annua stimata (pesi di oggi, ultimo anno)", "pct"),
    ("beta", "Beta verso il benchmark", "num2"),
    ("var_1d", "VaR 1 giorno (perdita superata nel 5% dei giorni)", "pct"),
    ("cvar_1d", "CVaR 1 giorno (perdita media in quei giorni)", "pct"),
    ("var_1m", "VaR 1 mese (approssimato: VaR 1 giorno x radice di 21)", "pct"),
    ("worst_day", "Giorno peggiore dell'ultimo anno", "pct"),
]


def _risk_section(result, risk: dict | None, run_id: str, reports_dir: Path) -> str:
    if not risk or not risk.get("measures"):
        return ""
    m = risk["measures"]
    fmt = {"pct": "{:.1%}", "int": "{:d}", "num1": "{:.1f}", "num2": "{:.2f}"}
    rows = "".join(f"<tr><td class='l'>{label}</td><td>{fmt[kind].format(m[key])}</td></tr>"
                   for key, label, kind in RISK_MEASURES if key in m and m[key] is not None
                   and (kind == "int" or np.isfinite(m[key])))
    out = "<h2>Rischio del portafoglio</h2><div class='wrap'><table>" + rows + "</table></div>"
    notes = getattr(result, "risk", None)
    if notes is not None and notes.notes:
        out += "<p>Limiti applicati: " + html.escape("; ".join(notes.notes)) + ".</p>"
    sw = m.get("sector_weights")
    if sw is not None and len(sw):
        out += "<p class='sub'>Per settore: " + html.escape(", ".join(f"{s} {w:.0%}" for s, w in sw.items())) + "</p>"
    stress = risk.get("stress")
    if stress is not None and not stress.empty:
        stress.to_csv(reports_dir / f"rischio_stress_{run_id}.csv", index=False)
        view = stress.rename(columns={"portfolio": "stress_portfolio", "benchmark": "stress_benchmark",
                                      "coverage": "stress_coverage"})
        out += ("<h3>Prove di stress: il portafoglio di oggi in crisi passate e con shock ipotetici</h3>"
                + _table(view, ["scenario", "start", "end", "stress_portfolio", "stress_benchmark", "stress_coverage"],
                         {"scenario": "Scenario", "start": "Dal", "end": "Al", "stress_portfolio": "Portafoglio",
                          "stress_benchmark": "Benchmark", "stress_coverage": "Con prezzi veri"})
                + "<p class='sub'>Titoli non quotati all'epoca: beta di oggi x rendimento del benchmark. "
                  "La liquidità conta zero.</p>")
    return out


def write_analysis_report(result, orders: list[dict], run_id: str, reports_dir: Path,
                          quality: pd.DataFrame | None = None, version: str | None = None,
                          risk: dict | None = None) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    dec = result.decisions_frame()
    dec.to_csv(reports_dir / f"decisioni_{run_id}.csv", index=False)
    if orders:
        pd.DataFrame(orders).to_csv(reports_dir / f"ordini_paper_{run_id}.csv", index=False)
    if quality is not None:
        quality.to_csv(reports_dir / f"qualita_dati_{run_id}.csv", index=False)

    r = result.regime
    prob = f" (probabilità {r.probability:.0%})" if np.isfinite(r.probability) else ""
    sections = [f"<h2>Regime di mercato: {r.label}{prob}</h2><p class='sub'>Metodo: {html.escape(r.method)}</p>"]
    counts = dec["action"].value_counts().to_dict() if not dec.empty else {}
    no_dcf = int(dec["fair_value"].isna().sum()) if "fair_value" in dec else len(dec)
    sections.append(
        f"<p>Titoli analizzati: <b>{len(dec)}</b> · BUY: <b>{counts.get('BUY', 0)}</b> · "
        f"SKIP: {counts.get('SKIP', 0)} · BLOCKED: {counts.get('BLOCKED', 0)} · senza DCF: {no_dcf}</p>")
    # in cima i BUY, poi i bloccati, poi i migliori scartati; l'elenco completo è nel CSV
    if not dec.empty:
        order = {"BUY": 0, "BLOCKED": 1, "SKIP": 2}
        conf = dec["confidence"] if "confidence" in dec else pd.Series(0, index=dec.index)
        view = dec.assign(_o=dec["action"].map(order), _c=conf.fillna(-1)).sort_values(["_o", "_c"], ascending=[True, False])
        skips = view[view["action"] == "SKIP"]
        shown = pd.concat([view[view["action"] != "SKIP"], skips.head(30)])
        note = (f"<p class='sub'>Mostrati i migliori 30 SKIP su {len(skips)}: elenco completo in "
                f"decisioni_{run_id}.csv</p>") if len(skips) > 30 else ""
        sections.append("<h2>Decisioni</h2>" + _table(shown, list(DECISION_LABELS), DECISION_LABELS) + note)
    if orders:
        o = pd.DataFrame(orders)
        sections.append("<h2>Ordini simulati (paper trading)</h2>" + _table(
            o, ["ticker", "order_type", "shares", "weight", "reference_price", "limit_price", "stop_loss", "take_profit"],
            {"ticker": "Ticker", "order_type": "Tipo", "shares": "Azioni", "weight": "Peso",
             "reference_price": "Prezzo rif.", "limit_price": "Limite", "stop_loss": "Stop-loss",
             "take_profit": "Take-profit"}))
    else:
        sections.append("<h2>Ordini simulati</h2><p>Nessun titolo ha superato i filtri: nessun ordine.</p>")
    sections.append(_risk_section(result, risk, run_id, reports_dir))
    sections.append(_quality_section(quality, run_id))
    sections.append("<p class='note'>Ordini simulati: il sistema non è collegato a un broker. "
                    "Non è una raccomandazione di investimento.</p>")
    path = reports_dir / f"analisi_{run_id}.html"
    path.write_text(_page("QUANT_ENGINE — Analisi",
                          _subtitle(f"Dati al {pd.Timestamp(result.as_of).date()} · run {run_id}", version),
                          "".join(sections)), encoding="utf-8")
    return path


def _equity_chart(bt) -> str:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return "<p class='sub'>Installa matplotlib per vedere il grafico.</p>"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(bt.equity.index, bt.equity.values, label="Strategia", color="#1f5fbf", linewidth=1.6)
    if not bt.benchmark.empty:
        ax1.plot(bt.benchmark.index, bt.benchmark.values, label=f"{bt.benchmark.name} (buy & hold)",
                 color="#8e8e93", linewidth=1.2)
    ax1.set_ylabel("Valore portafoglio")
    ax1.legend(frameon=False)
    ax1.grid(alpha=0.25)
    dd = bt.equity / bt.equity.cummax() - 1
    ax2.fill_between(dd.index, dd.values * 100, 0, color="#c4161c", alpha=0.35)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.25)
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return f"<img alt='Equity curve' src='data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}'>"


METRIC_LABELS = [
    ("total_return", "Rendimento totale", "pct"), ("cagr", "Rendimento annuo (CAGR)", "pct"),
    ("annual_volatility", "Volatilità annua", "pct"), ("sharpe_ratio", "Sharpe ratio", "num"),
    ("max_drawdown", "Drawdown massimo", "pct"), ("calmar_ratio", "Calmar ratio", "num"),
    ("final_value", "Valore finale", "money"),
]


def write_backtest_report(bt, run_id: str, reports_dir: Path, version: str | None = None) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"strategia": bt.equity, "benchmark": bt.benchmark, "esposizione": bt.exposure}) \
        .to_csv(reports_dir / f"backtest_equity_{run_id}.csv", index_label="date")
    if not bt.trades.empty:
        bt.trades.to_csv(reports_dir / f"backtest_trades_{run_id}.csv", index=False)
    if not bt.decisions.empty:
        bt.decisions.to_csv(reports_dir / f"backtest_decisioni_{run_id}.csv", index=False)
    if not bt.coverage.empty:
        bt.coverage.to_csv(reports_dir / f"backtest_copertura_{run_id}.csv", index=False)

    def f(v, kind):
        if v is None or not np.isfinite(v):
            return "–"
        return f"{v:.1%}" if kind == "pct" else (f"{v:,.0f}" if kind == "money" else f"{v:.2f}")

    rows = "".join(
        f"<tr><td>{label}</td><td>{f(bt.metrics.get(k), kind)}</td><td>{f(bt.benchmark_metrics.get(k), kind)}</td></tr>"
        for k, label, kind in METRIC_LABELS)
    m = bt.metrics
    extra = (f"<tr><td>Operazioni</td><td>{m.get('n_trades', 0)}</td><td>–</td></tr>"
             f"<tr><td>Costi totali</td><td>{m.get('total_costs', 0):,.0f}</td><td>–</td></tr>"
             f"<tr><td>Esposizione media</td><td>{m.get('avg_exposure', 0):.0%}</td><td>100%</td></tr>"
             f"<tr><td>Turnover annuo</td><td>{m.get('turnover_annual', 0):.1f}x</td><td>–</td></tr>"
             + (f"<tr><td>Copertura dell'universo (media / minima)</td><td>{m['avg_coverage']:.1%} / "
                f"{m['min_coverage']:.1%}</td><td>–</td></tr>" if "avg_coverage" in m else ""))
    bench_name = bt.benchmark.name if not bt.benchmark.empty else "Benchmark"
    table = (f"<table><tr><th>Metrica</th><th>Strategia</th><th>{html.escape(str(bench_name))}</th></tr>"
             f"{rows}{extra}</table>")
    p = bt.params
    note = ("<p class='note'>Backtest point-in-time: a ogni data la strategia vede solo i prezzi passati, i bilanci "
            "già depositati e i titoli che erano nell'indice in quel momento (ex membri compresi). Limiti: gli ex membri "
            "delistati non hanno prezzi (la copertura dice quanti mancano: survivorship bias residuo) e i risultati "
            "passati non garantiscono quelli futuri.</p>")
    yearly = bt.yearly
    year_rows = "".join(
        f"<tr><td>{y}</td><td>{r['strategia']:+.1%}</td>"
        + (f"<td>{r['benchmark']:+.1%}</td><td>{r['differenza']:+.1%}</td>" if "benchmark" in r else "")
        + "</tr>" for y, r in yearly.iterrows())
    year_head = "<tr><th>Anno</th><th>Strategia</th>" + ("<th>Benchmark</th><th>Differenza</th>"
                                                        if "benchmark" in yearly else "") + "</tr>"
    beaten = (yearly["differenza"] > 0).sum() if "differenza" in yearly else None
    year_note = f"<p>Anni in cui la strategia batte il benchmark: <b>{beaten} su {len(yearly)}</b>.</p>" \
        if beaten is not None else ""
    sections = (f"<h2>Andamento</h2>{_equity_chart(bt)}<h2>Metriche</h2>{table}{note}"
                f"<h2>Anno per anno</h2>{year_note}<table>{year_head}{year_rows}</table>")
    if not bt.trades.empty:
        reasons = bt.trades["reason"].value_counts().to_dict()
        sections += "<h2>Operazioni per motivo</h2><p>" + ", ".join(f"{k}: {v}" for k, v in reasons.items()) + "</p>"
    path = reports_dir / f"backtest_{run_id}.html"
    path.write_text(_page("QUANT_ENGINE — Backtest",
                          _subtitle(f"{p.get('start')} → {p.get('end')} · ribilanciamento ogni "
                                    f"{p.get('rebalance_days')} giorni · run {run_id}", version),
                          sections), encoding="utf-8")
    return path


RESEARCH_LABELS = {
    "signal": "Segnale", "horizon": "Orizzonte (gg)", "ic_mean": "IC medio", "ic_t": "t-stat",
    "ic_hit_rate": "% date IC > 0", "ic_sector_neutral": "IC dentro i settori", "spread_annual": "Spread annuo Q5-Q1",
    "monotonicity": "Monotonia", "avg_names": "Titoli/data", "n_dates": "Date", "verdict": "Verdetto",
}


def write_factor_report(res, run_id: str, reports_dir: Path, version: str | None = None) -> Path:
    """Report della ricerca sui fattori: tutti i fattori, pesi scelti sulla prima metà, combinazione fuori campione."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    res.summary.to_csv(reports_dir / f"fattori_sintesi_{run_id}.csv", index=False)
    res.summary_first.to_csv(reports_dir / f"fattori_prima_meta_{run_id}.csv", index=False)
    res.composite.to_csv(reports_dir / f"fattori_combinazione_{run_id}.csv", index=False)
    pd.Series(res.weights, name="peso").rename_axis("fattore").to_csv(reports_dir / f"fattori_pesi_{run_id}.csv")
    p = res.params
    h = p["weight_horizon"]

    def num(v, fmt):
        return "–" if v is None or not np.isfinite(v) else format(v, fmt)

    def rows(df, cols):
        return "".join("<tr>" + "".join(
            f"<td class=s>{html.escape(str(r[c]))}</td>" if c in ("signal", "verdict", "period")
            else f"<td>{num(r[c], '+.1%' if c == 'spread_annual' else '.0%' if c == 'ic_hit_rate' else '+.3f' if c.startswith('ic') else '+.2f')}</td>"
            for c in cols) + "</tr>" for _, r in df.iterrows())

    head = ("<tr><th>Fattore</th><th>IC medio</th><th>t-stat</th><th>% date IC&gt;0</th><th>IC nei settori</th>"
            "<th>Spread annuo Q5-Q1</th><th>Verdetto</th></tr>")
    cols = ["signal", "ic_mean", "ic_t", "ic_hit_rate", "ic_sector_neutral", "spread_annual", "verdict"]
    main = res.summary[res.summary["horizon"] == h].sort_values("ic_t", ascending=False)
    weights = "".join(f"<tr><td class=s>{html.escape(k)}</td><td>{v:.0%}</td></tr>"
                      for k, v in sorted(res.weights.items(), key=lambda kv: -kv[1])) or \
        "<tr><td colspan=2>nessun fattore con IC positivo nella prima metà</td></tr>"
    comp = res.composite[res.composite["horizon"] == h]
    comp_head = "<tr><th>Periodo</th><th>IC medio</th><th>t-stat</th><th>% date IC&gt;0</th><th>Spread annuo Q5-Q1</th></tr>"
    sections = [
        f"<h2>Fattori, tutto il periodo (orizzonte {h} giorni)</h2><table>{head}{rows(main, cols)}</table>",
        f"<h2>Combinazione walk-forward</h2><p>Pesi scelti guardando solo le date prima del {p['split']} "
        "(proporzionali all'IC medio dei fattori con IC positivo):</p>"
        f"<table><tr><th>Fattore (chiave)</th><th>Peso</th></tr>{weights}</table>"
        f"<table>{comp_head}{rows(comp, ['period', 'ic_mean', 'ic_t', 'ic_hit_rate', 'spread_annual'])}</table>",
        "<p class='note'>Conta solo la riga \"fuori campione\": nella prima metà la combinazione è costruita per "
        "andare bene. Se fuori campione l'IC crolla o cambia segno, i pesi erano adattati al rumore. Rendimenti lordi; "
        "universo = membri dell'indice a ogni data, ex membri delistati esclusi (survivorship bias residuo).</p>",
    ]
    for hh in [x for x in p["horizons"] if x != h]:
        other = res.summary[res.summary["horizon"] == hh].sort_values("ic_t", ascending=False)
        sections.append(f"<h2>Orizzonte {hh} giorni</h2><table>{head}{rows(other, cols)}</table>")
    path = reports_dir / f"fattori_{run_id}.html"
    path.write_text(_page("QUANT_ENGINE — Ricerca fattori",
                          _subtitle(f"{p['start']} → {p['end']} · {p['n_dates']} date ogni {p['step_days']} giorni · "
                                    f"run {run_id}", version), "".join(sections)), encoding="utf-8")
    return path


def write_research_report(res, run_id: str, reports_dir: Path, version: str | None = None) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    res.summary.to_csv(reports_dir / f"ricerca_sintesi_{run_id}.csv", index=False)
    res.quantiles.to_csv(reports_dir / f"ricerca_quantili_{run_id}.csv", index=False)
    res.signals.to_csv(reports_dir / f"ricerca_segnali_{run_id}.csv", index=False)

    def fmt(col, v):
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "–"
        if col in {"ic_hit_rate", "spread_annual"}:
            return f"{v:.1%}"
        if col in {"ic_mean", "ic_sector_neutral"}:
            return f"{v:+.3f}"
        if col in {"ic_t", "monotonicity"}:
            return f"{v:+.2f}"
        if col == "avg_names":
            return f"{v:.0f}"
        return html.escape(str(v))

    def table(df, cols):
        head = "".join(f"<th>{RESEARCH_LABELS.get(c, c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td{' class=s' if c in ('signal', 'verdict') else ''}>{fmt(c, r[c])}</td>"
                                        for c in cols) + "</tr>" for _, r in df.iterrows())
        return f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"

    cols = [c for c in RESEARCH_LABELS if c in res.summary.columns]
    sections = ["<h2>Sintesi per segnale e orizzonte</h2>", table(res.summary, cols)]
    if not res.quantiles.empty:
        pivot = res.quantiles.pivot_table(index=["signal", "horizon"], columns="quantile", values="mean_return")
        pivot.columns = [f"Q{c}" for c in pivot.columns]
        pivot = pivot.reset_index()
        qcols = [c for c in pivot.columns if c.startswith("Q")]
        head = "".join(f"<th>{h}</th>" for h in ["Segnale", "Orizzonte (gg)", *qcols])
        body = "".join("<tr><td class=s>" + html.escape(str(r["signal"])) + f"</td><td>{r['horizon']}</td>"
                       + "".join(f"<td>{r[q]:.2%}</td>" for q in qcols) + "</tr>" for _, r in pivot.iterrows())
        sections += ["<h2>Rendimento medio per quantile</h2><p class='sub'>Q1 = punteggio più basso, "
                     f"Q{len(qcols)} = più alto. Un buon segnale sale in modo regolare da Q1 a Q{len(qcols)}.</p>",
                     f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"]
    sections.append(
        "<p class='note'>Come leggerlo: un IC medio di 0,02-0,05 con t-stat sopra 3 è un segnale azionario serio; "
        "sopra 0,10 è sospetto (look-ahead). Con tanti segnali e orizzonti qualche t-stat sopra 2 esce per caso. "
        "Se l'IC dentro i settori è molto più basso, il segnale scommette sui settori. Rendimenti lordi, senza costi; "
        "l'universo esclude le aziende uscite dall'indice prima di oggi (survivorship bias).</p>")
    p = res.params
    path = reports_dir / f"ricerca_{run_id}.html"
    path.write_text(_page("QUANT_ENGINE — Ricerca segnali",
                          _subtitle(f"{p['start']} → {p['end']} · {p['n_dates']} date ogni {p['step_days']} giorni · "
                                    f"run {run_id}", version), "".join(sections)), encoding="utf-8")
    return path
