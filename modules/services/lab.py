"""Backtest della strategia automatica originale (risultati già calcolati da main.py: curva, anni, copertura, ricerca
dei segnali e dei fattori) e Laboratorio (esperimenti = configurazione + modifiche dei parametri in `lab.PARAMS`,
stesso Backtester; non scrive mai config.yaml). Valori grezzi per l'interfaccia nuova."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import lab
from modules.services.base import Service


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _sample(frame: pd.DataFrame, max_points: int = 500) -> dict:
    frame = frame.dropna(how="all")
    if len(frame) > max_points:
        step = int(np.ceil(len(frame) / max_points))
        keep = list(range(0, len(frame), step))
        if keep[-1] != len(frame) - 1:
            keep.append(len(frame) - 1)
        frame = frame.iloc[keep]
    return {"dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in frame.index],
            "series": [{"key": str(c), "values": [_f(v) for v in frame[c]]} for c in frame.columns]}


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        out.append({c: (_f(r[c]) if isinstance(r.get(c), (int, float, np.floating, np.integer)) and not isinstance(r.get(c), bool)
                        else r.get(c)) for c in cols if c in df})
    return out


def _run_label(run_id) -> str | None:
    try:
        return pd.to_datetime(str(run_id), format="%Y%m%d_%H%M%S").isoformat()
    except (TypeError, ValueError):
        return None


class LabService(Service):
    # ------------------------------------------------------------------ pagina Backtest
    def backtest_page(self, max_points: int = 500) -> dict:
        """Ultimo backtest della strategia automatica (se c'è), anni, copertura dell'universo, ricerca dei fattori
        e dei segnali. Comandi da terminale per ciò che manca (l'API non lancia calcoli di main.py)."""
        from modules.backtester import yearly_returns
        from modules.factors import FACTOR_LABELS

        ctx = self.ctx
        runs, eq = ctx.backtest_runs(), ctx.backtest_equity()
        out: dict = {"strategy": None, "factors": None, "signals": None,
                     "commands": {"backtest": "python main.py --no-update --only-backtest",
                                  "research": "python main.py --no-update --research",
                                  "factors": "python main.py --no-update --factors"}}
        if not runs.empty and not eq.empty:
            last = runs.iloc[-1]
            m, b = last["metrics"]["strategy"], last["metrics"].get("benchmark") or {}
            yearly = yearly_returns(eq["strategia"], eq["benchmark"] if "benchmark" in eq else None)
            cov = ctx.backtest_coverage()
            out["strategy"] = {
                "computed_at": _run_label(last["run_id"]), "params": last["params"],
                "metrics": {k: _f(m.get(k)) for k in ("total_return", "sharpe_ratio", "max_drawdown", "n_trades",
                                                       "total_costs", "avg_coverage", "min_coverage")},
                "benchmark": {k: _f(b.get(k)) for k in ("total_return", "sharpe_ratio")},
                "curves": _sample(eq[[c for c in ("strategia", "benchmark", "esposizione") if c in eq]], max_points),
                "years": [{"year": int(y), "strategy": _f(r["strategia"]), "benchmark": _f(r.get("benchmark"))}
                          for y, r in yearly.iterrows()],
                "coverage": None if cov.empty else _sample(
                    pd.DataFrame({"share": (cov["with_price"] / cov["members"]).to_numpy()},
                                 index=pd.to_datetime(cov["date"])), max_points),
            }
        fs = ctx.factor_summary()
        if not fs.empty:
            h = int(self.cfg.get("factors", {}).get("weight_horizon", 21))
            view = fs[fs["horizon"] == h].sort_values("ic_t", ascending=False)
            comp = ctx.factor_composite()
            weights = ctx.factor_weights()
            out["factors"] = {
                "horizon": h,
                "ic": [{"factor": FACTOR_LABELS.get(r["signal"], r["signal"]), "ic_mean": _f(r["ic_mean"]),
                        "ic_t": _f(r["ic_t"])} for _, r in view.iterrows()],
                "weights": sorted([{"factor": FACTOR_LABELS.get(k, k), "weight": _f(v)} for k, v in (weights or {}).items()],
                                  key=lambda x: -(x["weight"] or 0)),
                "composite": _records(comp[comp["horizon"] == h] if not comp.empty else comp,
                                      ["period", "ic_mean", "ic_t", "ic_hit_rate", "spread_annual"]),
            }
        rs = ctx.research_summary()
        if not rs.empty:
            out["signals"] = _records(rs.sort_values(["signal", "horizon"]),
                                      ["signal", "horizon", "ic_mean", "ic_t", "ic_hit_rate", "ic_sector_neutral",
                                       "spread_annual", "monotonicity", "verdict"])
        return out

    # ------------------------------------------------------------------ Laboratorio
    def lab_options(self) -> dict:
        """Parametri modificabili (con il valore della configurazione), punti di partenza, periodi, settori."""
        base = self.cfg
        presets = dict(lab.PRESETS)
        if self.ctx.factor_weights():
            presets["Fattori: pesi dalla ricerca"] = lab.factor_preset(self.ctx.factor_weights())
        comp = self.ctx.companies
        return {
            "params": [{"key": k, "label": p.label, "group": p.group, "kind": p.kind, "min": p.min, "max": p.max,
                        "step": p.step, "options": list(p.options), "help": p.help, "value": lab.get_value(base, p.path)}
                       for k, p in lab.PARAM_BY_KEY.items()],
            "presets": [{"name": n, "values": v} for n, v in presets.items()],
            "periods": [{"value": k, "label": v} for k, v in lab.PERIODS.items()],
            "sectors": sorted(comp["sector"].dropna().unique()) if "sector" in comp else [],
        }

    def lab_preview(self, values: dict, period: str = "all") -> dict:
        """Cosa cambia rispetto alla configurazione, se è valida, e le date del backtest."""
        base = self.cfg
        if period not in lab.PERIODS:
            return {"ok": False, "error": f"Periodo sconosciuto: {period}"}
        unknown = set(values) - set(lab.PARAM_BY_KEY)
        if unknown:
            return {"ok": False, "error": f"Parametri sconosciuti: {', '.join(sorted(unknown))}"}
        overrides = lab.changes(base, values)
        try:
            lab.apply_overrides(base, overrides)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        start, end = lab.period_bounds(base, self.data.calendar, period)
        return {"ok": True, "error": None, "overrides": overrides, "description": lab.describe(base, overrides),
                "start": start, "end": end}

    def lab_run_key(self, values: dict, period: str = "all", with_research: bool = False) -> str:
        from modules import run_info

        overrides = lab.changes(self.cfg, values)
        cfg = lab.apply_overrides(self.cfg, overrides)
        return (f"lab:{run_info.config_hash(cfg)}:{period}:{int(bool(with_research))}:"
                f"{pd.Timestamp(self.data.last_date).date()}")

    def run_lab_experiment(self, values: dict, name: str = "", period: str = "all",
                           with_research: bool = False) -> list[int]:
        """Esegue l'esperimento (prima la configurazione attuale, se manca sugli stessi dati e periodo, come termine
        di confronto) e lo salva; restituisce gli id."""
        ctx, base = self.ctx, self.cfg
        prev = ctx.lab_preview(values, period)
        if not prev["ok"]:
            raise ValueError(prev["error"])
        overrides = prev["overrides"]
        as_of = str(pd.Timestamp(self.data.last_date).date())
        exps = self.db.load_experiments()
        same = exps[(exps["data_as_of"] == as_of) & (exps["period"] == period)] if not exps.empty else exps
        has_baseline = not same.empty and any(not o for o in same["overrides"])
        ids = []
        if overrides and not has_baseline:
            ids.append(self.db.log_experiment(lab.run_experiment(
                base, {}, self.data, ctx.index_tickers, ctx.sectors(), ctx.universe_fn(), with_research=with_research,
                period=period)))
        exp = lab.run_experiment(base, overrides, self.data, ctx.index_tickers, ctx.sectors(), ctx.universe_fn(),
                                 name=(name or "").strip(), with_research=with_research, period=period)
        ids.append(self.db.log_experiment(exp))
        return ids

    def lab_experiments(self) -> dict:
        """Tutti gli esperimenti (Laboratorio e Strategie) con le metriche principali; i più recenti per primi."""
        exps = self.db.load_experiments()
        as_of = str(pd.Timestamp(self.data.last_date).date())
        rows = []
        for r in exps.itertuples():
            m = r.metrics or {}
            is_lab = all(k in lab.PARAM_BY_KEY for k in (r.overrides or {}))
            period = "" if r.period == "all" else f" [{'1ª' if r.period == 'first' else '2ª'} metà]"
            rows.append({"id": int(r.id), "label": f"#{r.id} · {r.name}{period}", "name": r.name, "period": r.period,
                         "data_as_of": r.data_as_of, "created_at": r.created_at, "lab": bool(is_lab),
                         "baseline": not r.overrides, "same_data": r.data_as_of == as_of,
                         "total_return": _f(m.get("total_return")), "sharpe": _f(m.get("sharpe_ratio"))})
        rows.sort(key=lambda x: -x["id"])
        return {"as_of": as_of, "rows": rows, "same_data": sum(r["same_data"] for r in rows)}

    def lab_compare(self, ids: list[int], max_points: int = 500) -> dict:
        """Gli esperimenti scelti (al massimo 4): tabella, curve (in dollari, con il benchmark del primo), IC dei
        segnali a 21 giorni se c'è la ricerca, dettagli (modifiche in YAML, versione del codice)."""
        ids = list(dict.fromkeys(int(i) for i in ids))[:4]
        exps = self.db.load_experiments()
        exps = exps[exps["id"].isin(ids)].set_index("id").reindex([i for i in ids if i in set(exps["id"])])
        if exps.empty:
            return {"rows": [], "curves": None, "ic": [], "warnings": []}
        base = self.cfg
        rows, curves = [], {}
        for i, r in exps.iterrows():
            m = r.metrics or {}
            is_lab = all(k in lab.PARAM_BY_KEY for k in (r.overrides or {}))
            label = f"#{i} · {r['name']}" + ("" if r.period == "all" else f" [{'1ª' if r.period == 'first' else '2ª'} metà]")
            rows.append({"id": int(i), "label": label, "period": lab.PERIODS.get(r.period, r.period).split(" (")[0],
                         "data_as_of": r.data_as_of, "lab": bool(is_lab),
                         **{k: _f(m.get(k)) for k in ("total_return", "excess_return_vs_benchmark", "cagr",
                                                       "sharpe_ratio", "max_drawdown", "n_trades", "total_costs",
                                                       "avg_exposure", "turnover_annual", "avg_coverage")},
                         "description": lab.describe(base, r.overrides) if is_lab else None,
                         "yaml": lab.overrides_yaml(r.overrides) if is_lab and r.overrides else None,
                         "created_at": r.created_at, "seconds": _f(r.seconds), "git_commit": r.git_commit,
                         "git_dirty": bool(r.git_dirty), "config_hash": r.config_hash})
            curves[f"e{i}"] = r.equity
        bench = exps.iloc[0].benchmark
        if bench is not None and not bench.empty:
            curves["benchmark"] = bench
        frame = pd.DataFrame(curves).sort_index()
        ic = []
        research = [(row["label"], r.research) for row, (_, r) in zip(rows, exps.iterrows()) if not r.research.empty]
        if research:
            tab = pd.DataFrame({name: rs[rs["horizon"] == 21].set_index("signal")["ic_mean"] for name, rs in research})
            ic = [{"signal": s, "values": {c: _f(tab.at[s, c]) for c in tab.columns}} for s in tab.index]
        warnings = []
        if len({r["data_as_of"] for r in rows}) > 1:
            warnings.append("Esperimenti fatti su dati di date diverse: i numeri non sono confrontabili.")
        if len({r["period"] for r in rows}) > 1:
            warnings.append("Periodi diversi: i rendimenti non sono confrontabili tra loro, solo con il benchmark "
                            "dello stesso periodo.")
        return {"rows": rows, "curves": _sample(frame, max_points), "ic": ic, "warnings": warnings}

    def delete_experiment(self, exp_id: int) -> None:
        self.db.delete_experiment(int(exp_id))

    # ------------------------------------------------------------------ strategia automatica (pagina)
    def auto_strategy_page(self) -> dict:
        """Decisioni BUY/SKIP/BLOCKED dell'ultima analisi della strategia automatica (sperimentale, ordini solo
        simulati), portafoglio simulato, peso per settore, rischio e prove di stress."""
        from modules.formats import it_text

        ctx = self.ctx
        dec = ctx.decisions()
        out: dict = {"empty": dec.empty, "command": "python main.py --no-update --strategy"}
        if dec.empty:
            return out
        as_of = dec["as_of"].iloc[0]
        try:
            stale = self.data.last_date is not None and pd.Timestamp(as_of) < pd.Timestamp(self.data.last_date)
        except (TypeError, ValueError):
            stale = False
        counts = dec["action"].value_counts()
        orders = ctx.orders()
        rows_orders, by_sector = [], []
        if not orders.empty:
            view = orders.merge(dec[["ticker", "name", "sector", "confidence", "margin_of_safety"]], on="ticker",
                                how="left").sort_values("weight", ascending=False)
            rows_orders = [{"ticker": r["ticker"], "name": r.get("name"), "sector": r.get("sector"),
                            **{k: _f(r.get(k)) for k in ("weight", "shares", "reference_price", "stop_loss",
                                                         "take_profit", "confidence", "margin_of_safety")},
                            "order_type": r.get("order_type")} for _, r in view.iterrows()]
            sec = view.groupby("sector")["weight"].sum().sort_values(ascending=False)
            by_sector = [{"sector": s, "weight": _f(w)} for s, w in sec.items()]
        risk = ctx.portfolio_risk()
        m = risk.get("measures", {}) if risk else {}
        stress = risk.get("stress", pd.DataFrame()) if risk else pd.DataFrame()
        cols = ["ticker", "name", "sector", "action", "confidence", "threshold", "price", "fair_value",
                "margin_of_safety", "mf_score", "momentum", "quality", "volatility_score", "reasoning"]
        decisions = [{c: (it_text(str(r.get(c) or "")) if c == "reasoning" else
                          _f(r.get(c)) if c not in ("ticker", "name", "sector", "action") else r.get(c))
                      for c in cols} for _, r in dec.sort_values("confidence", ascending=False).iterrows()]
        out.update({
            "as_of": as_of, "stale": bool(stale), "run": _run_label(getattr(ctx, "decisions_run_id", ctx.run_id)),
            "regime": dec["regime"].iloc[0], "counts": {k: int(counts.get(k, 0)) for k in ("BUY", "SKIP", "BLOCKED")},
            "orders": rows_orders, "invested": _f(orders["weight"].sum()) if not orders.empty else 0.0,
            "by_sector": by_sector,
            "risk": {k: _f(m.get(k)) for k in ("volatility", "beta", "var_1d", "cvar_1d", "effective_positions",
                                               "positions")} if "volatility" in m else None,
            "stress": [] if stress is None or stress.empty else [
                {"scenario": r["scenario"], "portfolio": _f(r["portfolio"]), "benchmark": _f(r.get("benchmark"))}
                for _, r in stress.iterrows()],
            "decisions": decisions, "sectors": sorted(dec["sector"].dropna().unique()),
        })
        return out
