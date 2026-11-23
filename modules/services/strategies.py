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
