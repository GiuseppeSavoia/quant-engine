"""Pipeline: le aziende che sto studiando, per fase (Idee -> In studio -> Pronta per decidere -> In portafoglio /
Scartata), con cosa manca per stare a ragione nella fase (modules/workflow.py: "fatto quando"), prezzo, margine sul
mio fair value, giudizio e giorni nella fase; spostamenti e storico."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import workflow as wf
from modules.services.base import Service


class PipelineService(Service):
    def _completeness(self, ticker: str) -> dict[str, bool]:
        fv = self.ctx.note(ticker).get("fair_value")
        try:
            has_val = not self.ctx.valuation_diary(ticker).empty
        except Exception:  # noqa: BLE001 - azienda senza diario
            has_val = False
        return wf.completeness(self.ctx.research(ticker), fv, has_val)

    def pipeline_stage(self, ticker: str) -> dict:
        """Fase di un'azienda (None se non è nella pipeline), con le fasi possibili e cosa manca per ognuna."""
        pipe = self.db.load_pipeline()
        row = pipe[pipe["ticker"] == ticker]
        stage = None if row.empty else str(row["stage"].iloc[0])
        done = self._completeness(ticker)
        return {
            "ticker": ticker, "stage": stage, "label": wf.STAGE_LABELS.get(stage) if stage else None,
            "note": None if row.empty or pd.isna(row["note"].iloc[0]) else str(row["note"].iloc[0]),
            "stages": [{"key": k, "label": v, "missing": wf.missing_for(k, done)} for k, v in wf.STAGES],
        }

    def set_pipeline_stage(self, ticker: str, stage: str, note: str | None = None) -> None:
        if stage not in wf.STAGE_LABELS:
            raise ValueError(f"Fase sconosciuta: {stage}")
        self.db.set_pipeline_stage(ticker, stage, (note or "").strip() or None)

    def remove_from_pipeline(self, ticker: str) -> None:
        self.db.remove_from_pipeline(ticker)

    def pipeline_board(self) -> dict:
        """Le colonne della pipeline con le schede delle aziende e lo storico degli spostamenti (ultimi 100)."""
        ctx = self.ctx
        pipe = self.db.load_pipeline()
        verdicts = pd.DataFrame()
        known = [t for t in pipe["ticker"] if t in set(ctx.tickers)] if not pipe.empty else []
        if known:
            try:
                verdicts = ctx.verdict_overview(known)
            except Exception:  # noqa: BLE001 - senza giudizi la pipeline resta utile
                verdicts = pd.DataFrame()
        cards = []
        for r in pipe.itertuples(index=False):
            t = r.ticker
            px = ctx.prices(t) if t in set(ctx.tickers) else pd.DataFrame()
            price = float(px["close"].iloc[-1]) if not px.empty else None
            fv = ctx.note(t).get("fair_value")
            fv = None if fv is None or pd.isna(fv) else float(fv)
            has_v = not verdicts.empty and t in verdicts.index
            cards.append({
                "ticker": t, "name": ctx.info(t).get("name") or "", "stage": r.stage,
                "note": None if pd.isna(r.note) else r.note, "price": price, "currency": ctx.currency(t),
                "fair_value": fv, "margin": fv / price - 1 if fv and price and np.isfinite(price) and price > 0 else None,
                "days": wf.days_in_stage(r.updated_at), "missing": wf.missing_for(r.stage, self._completeness(t)),
                "verdict": verdicts.at[t, "verdict"] if has_v else None,
                "category": verdicts.at[t, "category"] if has_v else None,
            })
        log = self.db.load_pipeline_log()
        history = [] if log.empty else [
            {"ticker": x.ticker, "stage": x.stage, "label": wf.STAGE_LABELS.get(x.stage, x.stage),
             "note": None if pd.isna(x.note) else x.note, "at": x.at}
            for x in log.sort_values("at", kind="stable").iloc[::-1].head(100).itertuples(index=False)]
        return {
            "stages": [{"key": k, "label": v, "cards": [c for c in cards if c["stage"] == k]} for k, v in wf.STAGES],
            "count": len(cards), "history": history,
            "requirements": [{"key": k, "label": d, "stages": list(s)} for k, (d, s) in wf.REQUIREMENTS.items()],
        }
