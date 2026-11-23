"""
Il percorso di lavoro su un'azienda, dall'idea alla decisione: Idee -> In studio -> Pronta -> In portafoglio, oppure
Scartata. Ogni fase ha un "fatto quando" esplicito, così è chiaro cosa manca prima di decidere:

- In studio: si sta leggendo e analizzando (nessun requisito).
- Pronta (per decidere): tesi in una frase, rischi, cosa farebbe cambiare idea, un fair value e una valutazione
  salvata nel diario (il modello con le ipotesi scritte). È la disciplina di un memo d'investimento: senza questi
  pezzi la decisione non è difendibile.
- In portafoglio: come "Pronta", più la data della prossima revisione.
- Scartata: il motivo (nota) resta nello storico, per non ristudiarla da capo fra sei mesi.

Solo logica: lo stato è nel database (tabelle `pipeline` e `pipeline_log`), la pagina è ui/pages/pipeline.py.
"""
from __future__ import annotations

import pandas as pd

STAGES: list[tuple[str, str]] = [("idea", "Idee"), ("studio", "In studio"), ("pronta", "Pronta per decidere"),
                                 ("portafoglio", "In portafoglio"), ("scartata", "Scartata")]
STAGE_LABELS = dict(STAGES)

# requisito -> (descrizione breve, fasi in cui serve)
REQUIREMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "one_liner": ("tesi in una frase", ("pronta", "portafoglio")),
    "risks": ("rischi", ("pronta", "portafoglio")),
    "change_mind": ("cosa mi farebbe cambiare idea", ("pronta", "portafoglio")),
    "fair_value": ("fair value", ("pronta", "portafoglio")),
    "valuation": ("valutazione salvata nel diario", ("pronta", "portafoglio")),
    "review_date": ("data della prossima revisione", ("portafoglio",)),
}


def completeness(research: dict | None, fair_value, has_valuation: bool) -> dict[str, bool]:
    """Quali pezzi del lavoro ci sono già (chiavi di REQUIREMENTS)."""
    r = research or {}
    filled = lambda k: bool(str(r.get(k) or "").strip())  # noqa: E731
    return {"one_liner": filled("one_liner"), "risks": filled("risks"), "change_mind": filled("change_mind"),
            "fair_value": fair_value is not None and not pd.isna(fair_value), "valuation": bool(has_valuation),
            "review_date": filled("review_date")}


def missing_for(stage: str, done: dict[str, bool]) -> list[str]:
    """Cosa manca perché l'azienda stia a ragione nella fase `stage` (descrizioni brevi, in ordine)."""
    return [desc for key, (desc, stages) in REQUIREMENTS.items() if stage in stages and not done.get(key, False)]


def days_in_stage(updated_at, today=None) -> int | None:
    if updated_at is None or pd.isna(updated_at):
        return None
    today = pd.Timestamp(today or pd.Timestamp.now()).normalize()
    return int((today - pd.Timestamp(updated_at).normalize()).days)
