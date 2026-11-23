"""
Dati di bilancio inseriti a mano dall'utente: per correggere un valore SEC sbagliato o per aziende che la SEC
non copre (es. europee). Si inseriscono solo le voci di base: tutto il resto (free cash flow, margini, multipli,
ROIC, punteggi...) lo calcola il codice, come per i dati SEC.

I valori finiscono in `financials` con fonte "manual" (esercizio completo) o "manual_ttm" (ultimi 12 mesi a un
trimestre) e `filed` = la data da cui valgono: "da oggi" è la scelta onesta per il backtest (allora non lo
sapevi); per correggere un errore storico si può dare la data in cui il dato giusto era pubblico.
Per lo stesso periodo e voce un valore a mano prevale su quello SEC (vedi MarketData).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# (chiave, etichetta, sezione) delle voci che si possono inserire
ITEMS: list[tuple[str, str, str]] = [
    ("revenue", "Ricavi", "Conto economico"),
    ("gross_profit", "Utile lordo (ricavi - costo del venduto)", "Conto economico"),
    ("operating_income", "Utile operativo (EBIT)", "Conto economico"),
    ("depreciation", "Ammortamenti", "Conto economico"),
    ("interest_expense", "Interessi passivi", "Conto economico"),
    ("pretax_income", "Utile ante imposte", "Conto economico"),
    ("tax_provision", "Imposte", "Conto economico"),
    ("net_income", "Utile netto", "Conto economico"),
    ("operating_cash_flow", "Cash flow operativo", "Flussi di cassa"),
    ("capex", "Investimenti (capex)", "Flussi di cassa"),
    ("dividends_paid", "Dividendi pagati", "Flussi di cassa"),
    ("buybacks", "Riacquisti di azioni", "Flussi di cassa"),
    ("total_assets", "Attivo totale", "Stato patrimoniale"),
    ("current_assets", "Attività correnti", "Stato patrimoniale"),
    ("current_liabilities", "Passività correnti", "Stato patrimoniale"),
    ("total_liabilities", "Passività totali", "Stato patrimoniale"),
    ("equity", "Patrimonio netto", "Stato patrimoniale"),
    ("retained_earnings", "Utili accumulati", "Stato patrimoniale"),
    ("cash", "Cassa e investimenti a breve", "Stato patrimoniale"),
    ("total_debt", "Debito finanziario totale", "Stato patrimoniale"),
    ("shares", "Azioni (diluite)", "Azioni"),
]
ITEM_LABELS = {k: label for k, label, _ in ITEMS}
UNITS = {"unità": 1.0, "migliaia": 1e3, "milioni": 1e6, "miliardi": 1e9}
# uscite di cassa: si salvano sempre con lo stesso segno dei dati SEC, comunque le scriva l'utente
NEGATIVE = {"capex"}
POSITIVE = {"dividends_paid", "buybacks", "interest_expense"}
KINDS = {"annual": "manual", "ttm": "manual_ttm"}


def normalize(values: dict[str, float]) -> dict[str, float]:
    """Segni come nei dati SEC e free cash flow ricavato (cash flow operativo - capex) se ci sono entrambi."""
    out = {k: float(v) for k, v in values.items() if v is not None and np.isfinite(float(v))}
    for k in NEGATIVE & set(out):
        out[k] = -abs(out[k])
    for k in POSITIVE & set(out):
        out[k] = abs(out[k])
    if "operating_cash_flow" in out and "capex" in out:
        out["free_cash_flow"] = out["operating_cash_flow"] + out["capex"]
    return out


def build_rows(ticker: str, period_end, values: dict[str, float], kind: str = "annual", filed=None,
               amount_unit: float = 1.0, share_unit: float = 1.0) -> pd.DataFrame:
    """Righe per la tabella financials. `values` nelle unità scelte (es. milioni); `filed` = da quando vale
    (default oggi). Errore se la chiusura è nel futuro o se `filed` precede la chiusura."""
    if kind not in KINDS:
        raise ValueError("tipo di periodo: annual o ttm")
    period_end = pd.Timestamp(period_end).normalize()
    filed = pd.Timestamp(filed).normalize() if filed is not None else pd.Timestamp.now().normalize()
    if period_end > pd.Timestamp.now().normalize():
        raise ValueError(f"La chiusura {period_end.date()} è nel futuro.")
    if filed < period_end:
        raise ValueError("Un dato non può essere noto prima della chiusura del periodo.")
    unknown = set(values) - set(ITEM_LABELS)
    if unknown:
        raise ValueError(f"voci sconosciute: {sorted(unknown)}")
    scaled = {k: float(v) * (share_unit if k == "shares" else amount_unit) for k, v in values.items()
              if v is not None and pd.notna(v)}
    rows = [(ticker, period_end, k, v, filed, KINDS[kind]) for k, v in normalize(scaled).items()]
    return pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])


def changed_cells(before: pd.DataFrame, after: pd.DataFrame, tol: float = 1e-9) -> dict:
    """Celle modificate in una tabella voci x periodi: {periodo: {voce: valore}}. Una cella svuotata non conta
    (per togliere un valore inserito a mano c'è l'elenco dei dati inseriti)."""
    out: dict = {}
    for col in after.columns:
        for key in after.index:
            new = after.at[key, col]
            if new is None or pd.isna(new):
                continue
            old = before.at[key, col] if col in before.columns and key in before.index else np.nan
            if pd.isna(old) or abs(float(new) - float(old)) > tol * max(1.0, abs(float(old))):
                out.setdefault(col, {})[key] = float(new)
    return out
