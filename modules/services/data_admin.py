"""Pagine di contorno per l'interfaccia nuova: grandi investitori (13F), qualità dei dati (depositi SEC recenti e
controlli dell'ultima analisi), dati inseriti a mano (voci di bilancio) e aziende aggiunte fuori dall'indice."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import manual_data
from modules.services.base import Service

CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "SEK", "DKK", "NOK", "HKD", "AUD"]


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


class DataAdminService(Service):
    # ------------------------------------------------------------------ grandi investitori (13F)
    def funds_page(self, fund_cik: str | None = None) -> dict:
        """Le aziende più presenti nei portafogli dei fondi seguiti, l'elenco dei fondi e il portafoglio di uno."""
        pos = self.ctx.fund_positions()
        if pos.empty:
            return {"empty": True, "top": [], "funds": [], "fund": None, "positions": []}
        live = pos[pos["change"] != "uscita"]
        top = (live.dropna(subset=["ticker"]).groupby("ticker")
               .agg(funds=("fund_cik", "nunique"), bought=("change", lambda c: int(c.isin(["nuova", "aumentata"]).sum())),
                    value=("value", "sum")).sort_values(["funds", "value"], ascending=False).head(25).reset_index())
        funds = (live.groupby(["fund_cik", "fund_name"]).agg(report=("report_date", "max"), value=("value", "sum"))
                 .reset_index().sort_values("value", ascending=False))
        cik = str(fund_cik) if fund_cik is not None and str(fund_cik) in set(funds["fund_cik"].astype(str)) else \
            (str(funds["fund_cik"].iloc[0]) if not funds.empty else None)
        port = pos[pos["fund_cik"].astype(str) == cik].sort_values("weight", ascending=False) if cik else pos.iloc[0:0]
        return {
            "empty": False,
            "top": [{"ticker": r["ticker"], "name": self.ctx.info(r["ticker"]).get("name") or "", "funds": int(r["funds"]),
                     "bought": int(r["bought"]), "value": _f(r["value"])} for _, r in top.iterrows()],
            "funds": [{"cik": str(r["fund_cik"]), "name": r["fund_name"], "report_date": r["report"], "value": _f(r["value"])}
                      for _, r in funds.iterrows()],
            "fund": cik,
            "positions": [{"issuer": r["issuer"], "ticker": r["ticker"] if isinstance(r["ticker"], str) else None,
                           "change": r["change"], "weight": _f(r["weight"]), "value": _f(r["value"]),
                           "shares": _f(r["shares"]), "shares_change": _f(r["shares_change"])} for _, r in port.iterrows()],
        }

    # ------------------------------------------------------------------ qualità dei dati
    def quality_page(self, days: int = 14) -> dict:
        """10-Q/10-K depositati di recente con lo stato del caricamento e i controlli dell'ultima analisi."""
        from modules.formats import it_text

        ctx = self.ctx
        f = ctx.recent_filings(days)
        filings = [] if f.empty else [
            {"filed": r["filed"], "ticker": r["ticker"], "name": ctx.info(r["ticker"]).get("name") or "",
             "form": r["form"], "status": r["status"], "loaded_at": r.get("loaded_at"), "accession": r.get("accession")}
            for _, r in f.iterrows()]
        q = ctx.quality_issues()
        summary = [] if q.empty else [
            {"severity": r["severity"], "check": r["check"], "n": int(r["n"])}
            for _, r in q.groupby(["severity", "check"])["ticker"].nunique().rename("n").reset_index()
            .sort_values(["severity", "n"], ascending=[False, False]).iterrows()]
        issues = [] if q.empty else [
            {"ticker": r["ticker"], "check": r["check"], "severity": r["severity"], "detail": it_text(str(r["detail"]))}
            for _, r in q.sort_values(["severity", "check", "ticker"]).iterrows()]
        counts = f["status"].value_counts().to_dict() if not f.empty else {}
        return {"days": days, "filings": filings, "counts": {k: int(v) for k, v in counts.items()},
                "has_run": bool(ctx.run_id), "summary": summary, "issues": issues}

    # ------------------------------------------------------------------ dati inseriti a mano
    def manual_page(self, ticker: str, kind: str = "annual", unit: str = "milioni", share_unit: str = "milioni") -> dict:
        """Tabella voci x periodi già compilata con i dati attuali (nelle unità scelte) e i valori inseriti a mano."""
        if kind not in manual_data.KINDS:
            raise ValueError(f"Periodo sconosciuto: {kind}")
        if unit not in manual_data.UNITS or share_unit not in manual_data.UNITS:
            raise ValueError("Unità sconosciuta.")
        base = self.ctx.manual_table(ticker, kind, manual_data.UNITS[unit], manual_data.UNITS[share_unit])
        manual = self.db.load_manual_financials(ticker)
        return {
            "ticker": ticker, "kind": kind, "unit": unit, "share_unit": share_unit, "currency": self.ctx.currency(ticker),
            "units": list(manual_data.UNITS), "periods": [str(c) for c in base.columns],
            "items": [{"key": k, "label": manual_data.ITEM_LABELS[k],
                       "values": [_f(base.at[k, c]) for c in base.columns]} for k in base.index],
            "manual": [] if manual.empty else [
                {"period_end": r["period_end"], "kind": "esercizio" if r["source"] == "manual" else "12 mesi",
                 "metric": r["metric"], "label": manual_data.ITEM_LABELS.get(r["metric"], "Free cash flow (calcolato)"),
                 "value": _f(r["value"]), "filed": r["filed"]} for _, r in manual.iterrows()],
        }

    def save_manual_values(self, ticker: str, changes: dict, kind: str = "annual", unit: str = "milioni",
                           share_unit: str = "milioni", days_after_close: int | None = None) -> int:
        """Salva le celle cambiate {periodo: {voce: valore}} (nelle unità scelte); restituisce i valori scritti."""
        if kind not in manual_data.KINDS or unit not in manual_data.UNITS or share_unit not in manual_data.UNITS:
            raise ValueError("Periodo o unità sconosciuti.")
        clean: dict = {}
        for period, items in (changes or {}).items():
            try:
                p = str(pd.Timestamp(period).date())
            except ValueError as exc:
                raise ValueError(f"Data del periodo non valida: {period}") from exc
            if pd.Timestamp(p) > pd.Timestamp.now():
                raise ValueError("Il periodo non può essere nel futuro.")
            for k, v in items.items():
                if k not in manual_data.ITEM_LABELS:
                    raise ValueError(f"Voce sconosciuta: {k}")
                if v is None or not np.isfinite(float(v)):
                    continue
                clean.setdefault(p, {})[k] = float(v)
        if not clean:
            raise ValueError("Nessun valore da salvare.")
        return self.ctx.save_manual(ticker, clean, kind, manual_data.UNITS[unit], manual_data.UNITS[share_unit],
                                    days_after_close)

    def delete_manual_values(self, ticker: str, rows: list[dict]) -> int:
        """Toglie valori inseriti a mano ({period_end, metric, filed}); dove c'era torna a valere il dato SEC."""
        tuples = [(ticker, r["period_end"], r["metric"], r["filed"]) for r in rows]
        self.db.delete_manual_financials(tuples)
        return len(tuples)

    # ------------------------------------------------------------------ aziende fuori indice
    def custom_companies_view(self) -> dict:
        cc = self.ctx.custom_companies
        return {"currencies": CURRENCIES,
                "sectors": sorted({s for s in self.ctx.sectors().values() if isinstance(s, str) and s}),
                "companies": [] if cc.empty else [
                    {"ticker": r.ticker, "name": r.name or "", "sector": r.sector, "currency": r.currency,
                     "has_prices": not self.data.prices(r.ticker).empty} for r in cc.itertuples()]}

    def add_custom_company(self, symbol: str, name: str = "", sector: str | None = None,
                           currency: str | None = "USD") -> str:
        sym = (symbol or "").strip().upper()
        if not sym or " " in sym or len(sym) > 20:
            raise ValueError("Scrivi il simbolo Yahoo, senza spazi (es. ENI.MI).")
        if currency not in CURRENCIES:
            raise ValueError(f"Valuta non prevista: {currency}")
        self.db.add_custom_company(sym, (name or "").strip() or sym, sector or None, currency)
        return sym

    def remove_custom_company(self, ticker: str) -> None:
        self.db.remove_custom_company(ticker)
