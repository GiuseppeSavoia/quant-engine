"""Strategie: decisioni e ordini simulati dell'ultima analisi (con il perché della confidenza e il rischio del
portafoglio), catalogo standard e strategie dell'utente, risultati dei backtest (esperimenti) e posizioni di oggi."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.backtester import performance_metrics
from modules.confidence_engine import REGIME_SCORE
from modules.risk import RiskManager
from modules import lab, run_info, strategies
from modules.services.base import Service
from modules.strategy import Strategy


class StrategyService(Service):
    # ------------------------------------------------------------------ strategia automatica (ultima analisi)
    def decisions(self) -> pd.DataFrame:
        """Decisioni dell'ultima analisi, con nome dell'azienda."""
        ctx = self.ctx
        run = getattr(ctx, "decisions_run_id", ctx.run_id)
        if run is None:
            return pd.DataFrame()
        dec = self.db.load_decisions(run)
        names = ctx.companies["name"] if "name" in ctx.companies else pd.Series(dtype=object)
        return dec.assign(name=dec["ticker"].map(names))

    def orders(self) -> pd.DataFrame:
        run = getattr(self.ctx, "decisions_run_id", self.ctx.run_id)
        return self.db.load_orders(run) if run else pd.DataFrame()

    def portfolio_risk(self) -> dict:
        """Misure di rischio e prove di stress del portafoglio simulato dell'ultima analisi."""
        orders = self.ctx.orders()
        if orders.empty:
            return {}
        weights = orders.groupby("ticker")["weight"].sum()
        rm = RiskManager(self.cfg.get("risk", {}), self.ctx.sectors(), self.cfg["portfolio"]["max_weight"])
        return {"measures": rm.measure(weights, self.data), "stress": rm.stress_test(weights, self.data)}

    def decision_history(self, ticker: str) -> pd.DataFrame:
        return self.db.load_decisions(ticker=ticker).sort_values("run_id", ascending=False)

    def confidence_breakdown(self, ticker: str) -> dict | None:
        """Scompone la confidenza dell'ultima analisi nei tre contributi e calcola cosa mancava per un BUY.

        "Obiettivo" è la confidenza da raggiungere: la soglia del regime, oppure, se il titolo l'ha
        superata ma è rimasto fuori dai migliori `max_positions`, la confidenza dell'ultimo titolo entrato.
        """
        dec = self.ctx.decisions()
        if dec.empty or not (dec["ticker"] == ticker).any():
            return None
        row = dec[dec["ticker"] == ticker].iloc[0]
        out = {"action": row["action"], "regime": row["regime"], "reasoning": row["reasoning"]}
        if not np.isfinite(row.get("confidence", np.nan)):
            return {**out, "evaluated": False}  # bloccato o senza prezzo: la confidenza non è stata calcolata
        c = self.cfg["confidence"]
        total_w = sum(c["weights"].values())
        w = {k: v / total_w for k, v in c["weights"].items()}
        r = REGIME_SCORE.get(row["regime"], 0.5)
        v = float(row["value_score"]) if np.isfinite(row["value_score"]) else 0.0
        m = float(row["mf_score"]) if np.isfinite(row["mf_score"]) else 0.0
        parts = {"regime": w["regime"] * r, "value": w["value"] * v, "multi_factor": w["multi_factor"] * m}
        buys = dec.loc[dec["action"] == "BUY", "confidence"]
        cutoff = float(buys.min()) if len(buys) >= int(self.cfg["portfolio"].get("max_positions", 25)) else None
        threshold = float(row["threshold"])
        target = max(threshold, cutoff) if cutoff is not None and row["action"] != "BUY" else threshold

        def needed(score_now: float, weight: float) -> float:
            """Punteggio (0-1) che quel fattore dovrebbe avere, a parità degli altri, per arrivare all'obiettivo."""
            return (target - (sum(parts.values()) - weight * score_now)) / weight

        value_needed = needed(v, w["value"])
        mf_needed = needed(m, w["multi_factor"])
        return {
            **out, "evaluated": True, "parts": parts, "confidence": float(row["confidence"]),
            "threshold": threshold, "cutoff": cutoff, "target": target,
            "max_possible": parts["regime"] + w["value"] + w["multi_factor"],
            "value_score": v, "mf_score": m, "value_needed": value_needed, "mf_needed": mf_needed,
            # il punteggio Value è lineare nel margine: 1 = margine di `full_value_margin`
            "margin_now": float(row["margin_of_safety"]) if np.isfinite(row["margin_of_safety"]) else None,
            "margin_needed": value_needed * c["full_value_margin"] if 0 < value_needed <= 1 else None,
            "weights": w,
        }

    # ------------------------------------------------------------------ strategie a regole e laboratorio
    def strategy_catalog(self) -> dict[str, dict]:
        """Strategie standard; la combinazione di fattori c'è se `python main.py --factors` ha stimato i pesi."""
        catalog = dict(strategies.CATALOG)
        weights = self.ctx.factor_weights()
        if weights:
            catalog["Combinazione di fattori"] = {
                "kind": "config", "overrides": lab.factor_preset(weights),
                "description": "I fattori classici con i pesi stimati dalla ricerca SOLO sul 2012-2019 "
                               "(python main.py --factors): la seconda metà del periodo è fuori campione."}
        return catalog

    def user_strategies(self) -> dict[str, dict]:
        return self.db.load_strategies()

    def strategy_config(self, name: str) -> tuple[dict, dict]:
        """Configurazione completa del motore e descrizione da registrare con l'esperimento."""
        mine = self.ctx.user_strategies()
        if name in mine:
            d = mine[name]
            return strategies.to_config(self.cfg, d, self.ctx.watchlist()), {"strategy": name, "definition": d}
        entry = self.ctx.strategy_catalog()[name]
        if entry["kind"] == "rules":
            return strategies.to_config(self.cfg, entry["definition"]), {"strategy": name}
        return lab.apply_overrides(self.cfg, entry["overrides"]), {"strategy": name, **entry["overrides"]}

    def strategy_result(self, name: str, run: bool = False, latest: bool = True) -> dict | None:
        """Backtest dal primo giorno configurato a oggi (riletto se già fatto con questi dati e questa
        configurazione; con `run` lo esegue se manca). Con `latest`, se manca quello con i dati di oggi si
        rilegge il più recente con la stessa configurazione (`stale` = True: i dati sono cambiati da allora,
        es. dopo l'aggiornamento serale)."""
        ctx = self.ctx
        cfg, record = ctx.strategy_config(name)
        key = (run_info.config_hash(cfg), "all", str(pd.Timestamp(self.data.last_date).date()))
        row = self.db.find_experiment(*key)
        if row is None and run:
            exp = lab.run_config(cfg, name, record, self.data, ctx.index_tickers, ctx.sectors(), ctx.universe_fn())
            self.db.log_experiment(exp)
            row = self.db.find_experiment(*key)
        if row is None and latest and not run:
            row = self.db.find_experiment(key[0], "all", None)
            if row is not None:
                row["stale"] = True
        return row

    def period_table(self, result: dict, baseline: dict | None = None) -> pd.DataFrame:
        """Risultati sull'intero periodo e sulle due metà (dallo stesso backtest): strategia, indice a pesi
        uguali e benchmark. Righe = (periodo, cosa), colonne = metriche."""
        eq, bench = result["equity"], result["benchmark"]
        if eq.empty:
            return pd.DataFrame()
        start, end = eq.index[0], eq.index[-1]
        mid = start + (end - start) / 2
        rf = float(result["metrics"].get("risk_free_used", 0.0) or 0.0)
        series = {"Strategia": eq, "Indice a pesi uguali": baseline["equity"] if baseline else None,
                  str(self.cfg["universe"].get("benchmark") or "Benchmark"): bench}
        periods = {f"Tutto ({start.year}-{end.year})": (start, end),
                   f"1a metà ({start.year}-{mid.year})": (start, mid),
                   f"2a metà ({mid.year}-{end.year})": (mid, end)}
        rows = []
        for pname, (a, b) in periods.items():
            for what, s in series.items():
                if s is None or s.empty:
                    continue
                m = performance_metrics(s.loc[a:b], rf)
                if m:
                    rows.append({"period": pname, "what": what, **{k: m[k] for k in (
                        "total_return", "cagr", "annual_volatility", "sharpe_ratio", "max_drawdown")}})
        return pd.DataFrame(rows)

    def strategy_holdings(self, name: str) -> pd.DataFrame:
        """Cosa terrebbe la strategia oggi: pesi, nome, settore e punteggio (0-1) della classifica."""
        ctx = self.ctx
        cfg, _ = ctx.strategy_config(name)
        res = Strategy(cfg, tickers=ctx.index_tickers, sectors=ctx.sectors()).run(self.data)
        if res.weights.empty:
            return pd.DataFrame()
        dec = pd.DataFrame(res.decisions).set_index("ticker")
        out = pd.DataFrame({"weight": res.weights})
        out["name"] = [ctx.info(t).get("name") for t in out.index]
        out["sector"] = [ctx.info(t).get("sector") for t in out.index]
        out["score"] = dec["confidence"].reindex(out.index) if "confidence" in dec else np.nan
        out.index.name = "ticker"
        return out.reset_index()

    # ------------------------------------------------------------------ pagina Strategie (webapp e interfaccia nuova)
    def strategy_label(self, name: str) -> dict:
        """Nome, tipo ("mia", gruppo del catalogo o "Di base") e se si può modificare."""
        mine = self.ctx.user_strategies()
        if name in mine:
            return {"name": name, "group": "Mie", "mine": True}
        entry = self.ctx.strategy_catalog().get(name)
        if entry is None:
            raise KeyError(name)
        return {"name": name, "group": entry.get("group") or "Di base", "mine": False}

    def strategies_list(self) -> list[dict]:
        """Tutte le strategie in ordine: catalogo standard, grandi investitori, le mie."""
        return [self.ctx.strategy_label(n) for n in [*self.ctx.strategy_catalog(), *self.ctx.user_strategies()]]

    def strategy_run_key(self, name: str) -> str:
        """Chiave di un backtest (configurazione + data dei dati): due richieste uguali sono lo stesso lavoro."""
        cfg, _ = self.ctx.strategy_config(name)
        return f"backtest:{run_info.config_hash(cfg)}:{pd.Timestamp(self.data.last_date).date()}"

    def run_strategy_backtest(self, name: str) -> None:
        """Backtest della strategia e, se manca, dell'indice a pesi uguali (serve al confronto)."""
        self.ctx.strategy_result(name, run=True)
        if name != strategies.BASELINE:
            self.ctx.strategy_result(strategies.BASELINE, run=True)

    @staticmethod
    def _sample(frame: pd.DataFrame, max_points: int) -> dict:
        frame = frame.dropna(how="all")
        if len(frame) > max_points:
            step = int(np.ceil(len(frame) / max_points))
            keep = list(range(0, len(frame), step))
            if keep[-1] != len(frame) - 1:
                keep.append(len(frame) - 1)
            frame = frame.iloc[keep]
        return {"dates": [d.strftime("%Y-%m-%d") for d in frame.index],
                "series": [{"key": c, "values": [None if not np.isfinite(v) else float(v) for v in frame[c]]}
                           for c in frame.columns]}

    def _report(self, name: str, result: dict, baseline: dict | None, max_points: int) -> dict:
        from modules import significance
        from modules.backtester import yearly_returns
        from modules.formats import it_text

        bench = str(self.cfg["universe"].get("benchmark") or "Benchmark")
        table = self.ctx.period_table(result, baseline)
        summary = None
        if not table.empty:
            second = table[table["period"].str.startswith("2a")].set_index("what")
            if "Strategia" in second.index and strategies.BASELINE in second.index:
                s, e = second.loc["Strategia"], second.loc[strategies.BASELINE]
                summary = {"better": bool(s["cagr"] > e["cagr"] and s["sharpe_ratio"] >= e["sharpe_ratio"]),
                           "cagr": float(s["cagr"]), "baseline_cagr": float(e["cagr"]),
                           "sharpe": float(s["sharpe_ratio"]), "baseline_sharpe": float(e["sharpe_ratio"]),
                           "bench_cagr": float(second.loc[bench, "cagr"]) if bench in second.index else None}
        eq, bench_eq = result["equity"], result["benchmark"]
        base_eq = baseline["equity"] if baseline else None
        curves = pd.DataFrame({"strategy": eq})
        for key, s in (("baseline", base_eq), ("benchmark", bench_eq)):
            if s is not None and not s.empty and not eq.empty:
                curves[key] = s.reindex(curves.index).ffill()
        if not eq.empty:
            curves = curves.loc[eq.index[0]:]
            curves = curves / curves.bfill().iloc[0]
        yearly = yearly_returns(eq, base_eq.loc[eq.index[0]:] if base_eq is not None and not eq.empty else None)
        yb = yearly_returns(bench_eq)
        years = [{"year": int(y), "strategy": float(r["strategia"]),
                  "baseline": float(r["benchmark"]) if "benchmark" in yearly and pd.notna(r.get("benchmark")) else None,
                  "benchmark": float(yb.loc[y, "strategia"]) if y in yb.index else None}
                 for y, r in yearly.iterrows()]
        sig = None
        try:
            a = significance.assess(eq, self.db.load_experiments())
            sig = {"text": it_text(significance.verdict_text(a)), "dsr": a.get("dsr")}
        except Exception:  # noqa: BLE001 - nota informativa: mai bloccare la pagina
            pass
        m = result["metrics"]
        mid = eq.index[0] + (eq.index[-1] - eq.index[0]) / 2 if not eq.empty else None
        return {
            "stale": bool(result.get("stale")), "data_as_of": result.get("data_as_of"), "benchmark": bench,
            "summary": summary, "periods": table.to_dict("records"),
            "curves": {**self._sample(curves, max_points), "mid": mid.strftime("%Y-%m-%d") if mid is not None else None},
            "years": years,
            "facts": {k: m.get(k) for k in ("turnover_annual", "total_costs", "avg_exposure", "avg_coverage")},
            "variants": self.db.count_variants(), "significance": sig,
        }

    def strategy_page(self, name: str, max_points: int = 500) -> dict:
        """Una strategia: chi è, le regole in chiaro, le cautele, il risultato del backtest (se c'è) con le due metà,
        il valore nel tempo, gli anni, i fatti (rotazione, costi...) e lo Sharpe corretto per i tentativi."""
        import re

        from modules.formats import it_text

        ctx = self.ctx
        info = ctx.strategy_label(name)
        mine = ctx.user_strategies()
        entry = mine.get(name) or ctx.strategy_catalog()[name]
        definition = mine.get(name) or (entry.get("definition") if entry.get("kind") == "rules" else None)
        rules = []
        if definition:
            for line in strategies.describe(definition):
                m = re.match(r"\*\*(.+?)\*\*: (.*)", line)
                rules.append({"label": m.group(1), "text": it_text(m.group(2))} if m else {"label": "", "text": it_text(line)})
        result = ctx.strategy_result(name)
        baseline = ctx.strategy_result(strategies.BASELINE)
        ready = result is not None and baseline is not None
        return {
            **info, "description": (definition or {}).get("description") or entry.get("description") or "",
            "investor": entry.get("investor") if not info["mine"] else None,
            "definition": definition, "rules": rules,
            "watchlist_only": bool((definition or {}).get("universe", {}).get("watchlist_only")),
            "baseline_name": strategies.BASELINE,
            "report": self._report(name, result, None if name == strategies.BASELINE else baseline, max_points)
            if ready else None,
        }

    def strategy_holdings_view(self, name: str) -> dict:
        """Cosa terrebbe oggi (non è un consiglio): titoli, pesi, punteggio della classifica."""
        hold = self.ctx.strategy_holdings(name)
        rows = [] if hold.empty else hold.to_dict("records")
        return {"rows": rows, "invested": float(hold["weight"].sum()) if not hold.empty else 0.0}

    def strategies_overview(self) -> dict:
        """Tutte le strategie con un backtest ai dati di oggi (o il più recente), ordinate per la seconda metà; le
        altre in `missing`."""
        ctx = self.ctx
        baseline = ctx.strategy_result(strategies.BASELINE)
        rows, missing = [], []
        for item in ctx.strategies_list():
            res = ctx.strategy_result(item["name"])
            if res is None or res["equity"].empty:
                missing.append(item["name"])
                continue
            t = ctx.period_table(res, baseline)
            own = t[t["what"] == "Strategia"]

            def get(prefix: str, col: str):
                r = own[own["period"].str.startswith(prefix)]
                return float(r[col].iloc[0]) if not r.empty else None

            rows.append({**item, "since": int(res["equity"].index[0].year), "stale": bool(res.get("stale")),
                         "all": get("Tutto", "cagr"), "first": get("1a", "cagr"), "second": get("2a", "cagr"),
                         "sharpe": get("Tutto", "sharpe_ratio"), "drawdown": get("Tutto", "max_drawdown"),
                         "turnover": res["metrics"].get("turnover_annual")})
        rows.sort(key=lambda r: -np.inf if r["second"] is None else r["second"], reverse=True)
        return {"rows": rows, "missing": missing}

    def strategy_builder_options(self) -> dict:
        """Cosa serve al costruttore: settori, metriche per la classifica (con un verso migliore, mai il voto della
        checklist), ricerche pronte e salvate da cui partire, pesi e ribilanciamenti, definizione vuota."""
        from modules import screener
        from modules.metrics import JUDGMENT_KEYS, METRICS

        saved = self.db.load_screens()
        return {
            "sectors": sorted({s for s in self.ctx.sectors().values() if isinstance(s, str) and s}),
            "rankable": [{"key": m.key, "label": m.label, "group": m.group} for m in METRICS
                         if m.better and m.key not in JUDGMENT_KEYS],
            "excluded": sorted(JUDGMENT_KEYS),
            "presets": [{"name": k, "rules": v["rules"]} for k, v in screener.PRESETS.items()],
            "saved": [{"name": k, "rules": v["rules"], "sectors": list(v.get("sectors") or [])} for k, v in saved.items()],
            "weightings": [{"value": k, "label": v} for k, v in strategies.WEIGHTINGS.items()],
            "rebalance": [{"value": k, "label": v} for k, v in strategies.REBALANCE.items()],
            "default": strategies.default_definition(),
            "reserved": list(self.ctx.strategy_catalog()),
        }

    def strategy_preview(self, definition: dict) -> dict:
        """Quante aziende passano oggi i filtri e quante ne terrebbe la strategia; `error` se la definizione non va."""
        from modules import screener

        try:
            strategies.validate_definition(definition)
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        table = self.ctx.explore_table()
        u = definition.get("universe") or {}
        if u.get("sectors"):
            table = table[table["sector"].isin(u["sectors"])]
        if u.get("watchlist_only"):
            table = table[table["followed"]]
        passing = int(screener.apply_rules(table, definition.get("filters", []), self.ctx.sectors()).sum())
        return {"ok": True, "error": None, "passing": passing, "universe": int(len(table)),
                "held": min(passing, int(definition["portfolio"]["positions"]))}

    def save_user_strategy(self, definition: dict, editing: str | None = None) -> str:
        """Salva una strategia dell'utente (con `editing`, sostituisce quella con quel nome); ValueError in italiano."""
        name = str(definition.get("name") or "").strip()
        if not name:
            raise ValueError("Dai un nome alla strategia.")
        if name in self.ctx.strategy_catalog():
            raise ValueError("C'è già una strategia standard con questo nome.")
        definition = {**strategies.default_definition(), **definition, "name": name}
        strategies.validate_definition(definition)
        mine = self.ctx.user_strategies()
        if name in mine and name != editing:
            raise ValueError(f"Hai già una strategia \"{name}\": scegli un altro nome.")
        if editing and editing != name and editing in mine:
            self.db.delete_strategy(editing)
        self.db.save_strategy(definition)
        return name

    def delete_user_strategy(self, name: str) -> None:
        """Toglie una strategia dell'utente (i backtest già fatti restano tra gli esperimenti)."""
        if name not in self.ctx.user_strategies():
            raise KeyError(name)
        self.db.delete_strategy(name)
