"""Tabella dei bilanci: celle di riserva segnate e colonne collegate al deposito SEC."""
import pandas as pd

from ui.pages.azienda.bilanci import SOURCE_MARKS, financial_table_html


def test_link_al_deposito_e_segni_delle_fonti():
    shown = pd.DataFrame({"2024": ["1.000", "–"], "2025": ["1.100", "50"]}, index=["Ricavi", "Debito"])
    marks = pd.DataFrame({"2024": ["", ""], "2025": ["", "yahoo_fill"]}, index=shown.index)
    out = financial_table_html(shown, marks=marks,
                               links={"2025": ("https://www.sec.gov/x-index.htm", "10-K depositato il 01/02/2026")})
    assert 'href="https://www.sec.gov/x-index.htm"' in out and 'target="_blank"' in out
    assert out.count("<a ") == 1  # 2024 senza link
    assert SOURCE_MARKS["yahoo_fill"][1] in out and "qe-src" in out
