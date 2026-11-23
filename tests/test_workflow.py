"""Pipeline delle aziende: fasi, cosa manca per ogni fase, storico degli spostamenti (nessuna rete)."""
import pandas as pd

from modules import workflow as wf


def test_cosa_manca_per_fase():
    nothing = wf.completeness({}, None, False)
    assert wf.missing_for("idea", nothing) == [] and wf.missing_for("studio", nothing) == []
    assert wf.missing_for("pronta", nothing) == ["tesi in una frase", "rischi", "cosa mi farebbe cambiare idea",
                                                 "fair value", "valutazione salvata nel diario"]
    done = wf.completeness({"one_liner": "x", "risks": "y", "change_mind": "z"}, 120.0, True)
    assert wf.missing_for("pronta", done) == []
    assert wf.missing_for("portafoglio", done) == ["data della prossima revisione"]
    assert wf.completeness({"one_liner": "  "}, float("nan"), False)["one_liner"] is False


def test_db_fasi_e_storico(demo_db):
    demo_db.set_pipeline_stage("AAPL", "idea", "da 13F", at="2026-11-01T10:00:00")
    demo_db.set_pipeline_stage("AAPL", "studio", at="2026-11-05T10:00:00")
    demo_db.set_pipeline_stage("MSFT", "scartata", "troppo cara")
    p = demo_db.load_pipeline().set_index("ticker")
    assert p.at["AAPL", "stage"] == "studio" and p.at["AAPL", "note"] == "da 13F"  # la nota resta
    assert wf.days_in_stage(p.at["AAPL", "updated_at"], "2026-11-08") == 3
    demo_db.remove_from_pipeline("MSFT")
    log = demo_db.load_pipeline_log()
    assert list(log[log["ticker"] == "AAPL"]["stage"]) == ["idea", "studio"]
    assert list(log[log["ticker"] == "MSFT"]["stage"]) == ["scartata", "tolta"]
    assert "MSFT" not in set(demo_db.load_pipeline()["ticker"])


def test_scheda_della_pipeline():
    from ui.pages.pipeline import card_html
    out = card_html({"ticker": "AAPL", "name": "Apple <Inc>", "stage": "pronta", "price": 200.0, "margin": 0.12,
                     "days": 4, "missing": ["fair value"], "note": "idea", "category": "candidata",
                     "verdict": "Candidata all'acquisto"}, "USD")
    assert "manca: fair value" in out and "Apple &lt;Inc&gt;" in out and "4 g" in out
    assert "pronta per la fase" in card_html({"ticker": "X", "stage": "pronta", "price": float("nan"),
                                              "margin": float("nan"), "missing": []})
    assert isinstance(pd.Timestamp.now(), pd.Timestamp)
