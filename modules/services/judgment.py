"""Giudizio sull'azienda (modules/verdict.py) e "cosa è cambiato" a ogni bilancio (modules/changes.py), con le ipotesi
dell'utente (nota con fair value, ultima valutazione a leve) lette dal database. Regole invariate: qui solo il
collegamento ai dati del contesto, così la scheda (e in futuro l'API) non chiama più i moduli direttamente."""
from __future__ import annotations

from modules import changes, verdict
from modules.services.base import Service


class JudgmentService(Service):
    def company_verdict(self, ticker: str):
        """Il Verdetto dell'azienda (`verdict.Verdict`), frasi già in italiano."""
        return verdict.assess_ticker(self.ctx, ticker)

    def thesis_inputs(self, ticker: str) -> tuple[dict, dict | None]:
        """(nota con fair value e ipotesi, ultima valutazione a leve salvata); vuote se mancano o se la lettura fallisce."""
        try:
            return changes.thesis_inputs(self.db, ticker)
        except Exception:  # noqa: BLE001 - tabelle assenti in un database vecchio
            return {}, None

    def what_changed(self, ticker: str, sector=None, currency: str = "USD", note: dict | None = None,
                     valuation: dict | None = None) -> dict:
        """Cosa è cambiato con l'ultimo bilancio (istantanee point-in-time), ipotesi dell'utente comprese. `note` e
        `valuation`: quelle di `thesis_inputs` (se mancano si leggono)."""
        if note is None and valuation is None:
            note, valuation = self.ctx.thesis_inputs(ticker)
        return changes.what_changed(self.data, ticker, cfg=self.cfg, sector=sector, currency=currency,
                                    note=note or {}, valuation=valuation) or {}
