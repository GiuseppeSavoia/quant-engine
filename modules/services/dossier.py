"""Funzioni "Altro" della scheda per l'interfaccia nuova: tutte le metriche con le mediane (DES), dividendi e
riacquisti (DVD), insider e grandi investitori (HDS), la mia ricerca con il diario delle valutazioni (NOTE), la
strategia automatica originale (AUTO). Solo dati salvati: valori grezzi, la formattazione la fa il client."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.services.base import Service


def _f(v):
    """Numero finito o None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


class DossierService(Service):
    # ------------------------------------------------------------------ DES
    def company_metrics_view(self, ticker: str) -> dict:
        """Tutte le metriche del catalogo per gruppo, con la mediana del settore e dell'S&P 500 e la posizione nel
        settore (quota di aziende che fanno peggio)."""
        from modules.metrics import GROUPS, METRIC_BY_KEY

        cm = self.ctx.company_metrics(ticker)
        groups = []
        for g in GROUPS:
            rows = cm[cm["group"] == g]
            if g == "Dimensione" or not rows["value"].notna().any():
                continue
            groups.append({"group": g, "rows": [{
                "key": r["key"], "label": r["label"], "help": r.get("help") or "",
                "fmt": METRIC_BY_KEY[r["key"]].fmt if r["key"] in METRIC_BY_KEY else "num",
                "value": _f(r["value"]), "sector_median": _f(r["sector_median"]), "rank_sector": _f(r["rank_sector"]),
                "index_median": _f(r["index_median"])} for _, r in rows.iterrows()]})
        return {"ticker": ticker, "sector": self.ctx.info(ticker).get("sector"), "currency": self.ctx.currency(ticker),
                "groups": groups}

    # ------------------------------------------------------------------ DVD
    def company_dividends(self, ticker: str) -> dict:
        h = self.ctx.dividend_history(ticker)
        if h.empty or (h["dividends"].fillna(0).sum() == 0 and h["buybacks"].fillna(0).sum() == 0):
            return {"ticker": ticker, "empty": True, "years": []}
        cols = ["dividends", "buybacks", "net_income", "free_cash_flow", "shares", "dps", "payout", "fcf_payout",
                "dividend_yield", "shareholder_yield"]
        years = [{"year": int(pd.Timestamp(r["period_end"]).year), **{c: _f(r.get(c)) for c in cols}}
                 for _, r in h.iterrows()]
        return {"ticker": ticker, "empty": False, "currency": self.ctx.currency(ticker), "years": years}

    # ------------------------------------------------------------------ HDS
    def company_ownership(self, ticker: str, everything: bool = False, limit: int = 300) -> dict:
        """Insider (riepilogo a 90 giorni e 12 mesi, operazioni: di norma solo acquisti e vendite sul mercato) e
        posizioni dei grandi investitori seguiti (ultimo 13F)."""
        ctx = self.ctx
        tx = ctx.insider_transactions(ticker)
        no_data = tx.empty and self.data.holdings.empty
        summary = ctx.insider_summary(ticker) if not no_data else None
        view = tx if everything or tx.empty else tx[tx["code"].isin(["P", "S"])]
        cols = ["filed", "trans_date", "owner", "title", "relationship", "kind", "code", "shares", "price", "value",
                "planned", "owned_after"]
        rows = [] if view.empty else [
            {c: (r.get(c) if c not in ("shares", "price", "value", "owned_after") else _f(r.get(c))) for c in cols}
            for _, r in view.head(limit).iterrows()]
        for r in rows:
            r["planned"] = bool(r.get("planned")) if r.get("planned") is not None and not pd.isna(r.get("planned")) else False
            r["title"] = "" if r.get("title") is None or pd.isna(r.get("title")) else r["title"]
        events = []
        if not tx.empty:
            for label, mask in (("buy", tx["code"].eq("P")),
                                ("sell", tx["code"].eq("S") & ~tx["planned"].fillna(False).astype(bool))):
                ev = tx[mask].groupby("trans_date", as_index=False)["value"].sum()
                events += [{"date": r["trans_date"], "kind": label, "value": _f(r["value"])} for _, r in ev.iterrows()]
        holders = ctx.fund_holders(ticker)
        funds = [] if holders.empty else [
            {"fund": r["fund_name"], "report_date": r["report_date"], "change": r["change"], "shares": _f(r["shares"]),
             "shares_change": _f(r["shares_change"]), "value": _f(r["value"]), "weight": _f(r["weight"])}
            for _, r in holders.iterrows()]
        return {"ticker": ticker, "no_data": bool(no_data), "currency": ctx.currency(ticker),
                "has_funds_data": not self.data.holdings.empty, "summary": summary, "transactions": rows,
                "transactions_total": int(len(view)), "all_total": int(len(tx)), "events": events, "funds": funds}

    # ------------------------------------------------------------------ NOTE
    def company_research(self, ticker: str) -> dict:
        """La mia ricerca: tesi, storia, catalizzatori, rischi, cosa farebbe cambiare idea, revisione, voti della
        checklist (con il voto medio), appunti, fair value con le ipotesi, diario delle valutazioni con l'esito."""
        from modules import method

        ctx = self.ctx
        r = ctx.research(ticker)
        note = ctx.note(ticker)
        items = ctx.checklist_items()
        current = ctx.checklist(ticker)
        score = method.checklist_score({k: v for k, v in current.items() if v is not None}, items) \
            if not items.empty else None
        diary = ctx.valuation_diary(ticker)
        entries, chart = [], None
        if not diary.empty:
            view = diary.sort_values("created_at", ascending=False)
            entries = [{"created_at": x["created_at"], "price": _f(x["price"]), "expected_value": _f(x["expected_value"]),
                        "margin_then": _f(x["margin_then"]), "return_since": _f(x["return_since"]),
                        "toward_value": bool(x["toward_value"]), "note": x.get("note")} for _, x in view.iterrows()]
            px = self.data.prices(ticker)
            if not px.empty:
                close = px["close"] if isinstance(px, pd.DataFrame) else px
                close = close[close.index >= diary["created_at"].min() - pd.Timedelta(days=90)]
                mine = diary.set_index("created_at")["expected_value"].sort_index()
                mine.index = pd.to_datetime(mine.index).normalize()
                mine = mine[~mine.index.duplicated(keep="last")]
                yours = mine.reindex(close.index.union(mine.index)).ffill().reindex(close.index)
                step = max(1, int(np.ceil(len(close) / 400)))
                keep = list(range(0, len(close), step)) + ([len(close) - 1] if (len(close) - 1) % step else [])
                chart = {"dates": [d.strftime("%Y-%m-%d") for d in close.index[keep]],
                         "price": [_f(v) for v in close.iloc[keep]], "yours": [_f(v) for v in yours.iloc[keep]]}
        when = [x for x in (r.get("updated_at"), note.get("updated_at")) if x]
        return {
            "ticker": ticker, "currency": ctx.currency(ticker),
            "fields": {k: r.get(k) or "" for k in ("one_liner", "story", "catalysts", "risks", "change_mind",
                                                     "review_date")},
            "thesis": note.get("thesis") or "", "fair_value": _f(note.get("fair_value")),
            "assumptions": note.get("assumptions") or {},
            "checklist": [] if items.empty else [
                {"id": int(i["id"]), "question": i["question"], "help": i.get("help") or "", "weight": _f(i.get("weight")),
                 "score": _f(current.get(int(i["id"])))} for _, i in items.iterrows()],
            "score": _f(score), "updated_at": max(pd.Timestamp(x) for x in when) if when else None,
            "diary": entries, "diary_chart": chart,
            "toward": {"yes": int(sum(e["toward_value"] for e in entries)), "total": len(entries)},
        }

    def save_company_research(self, ticker: str, fields: dict, scores: dict[int, float | None] | None = None,
                              thesis: str | None = None) -> None:
        """Salva insieme tesi e campi, voti della checklist (1-5 o None) e appunti; ValueError in italiano."""
        allowed = {"one_liner", "story", "catalysts", "risks", "change_mind", "review_date"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Campi sconosciuti: {', '.join(sorted(bad))}")
        clean = {k: (str(v).strip() if v is not None else "") for k, v in fields.items()}
        if clean.get("review_date"):
            try:
                clean["review_date"] = str(pd.Timestamp(clean["review_date"]).date())
            except ValueError as exc:
                raise ValueError("Data della revisione non valida.") from exc
        self.ctx.save_research(ticker, clean)
        if scores is not None:
            ids = set(self.ctx.checklist_items()["id"].astype(int)) if not self.ctx.checklist_items().empty else set()
            for k, v in scores.items():
                if int(k) not in ids:
                    raise ValueError(f"Domanda della checklist sconosciuta: {k}")
                if v is not None and not 1 <= float(v) <= 5:
                    raise ValueError("I voti vanno da 1 a 5.")
            self.ctx.save_checklist(ticker, {int(k): (None if v is None else float(v)) for k, v in scores.items()})
        if thesis is not None and thesis != (self.ctx.note(ticker).get("thesis") or ""):
            self.ctx.save_note(ticker, thesis=thesis)

    # ------------------------------------------------------------------ AUTO
    def company_auto(self, ticker: str, versions_limit: int = 500) -> dict:
        """La strategia automatica originale su un'azienda (sperimentale, NON è il giudizio): avvisi sui dati, perché
        BUY/SKIP con cosa mancava, DCF e fattori della strategia, storico delle decisioni, versioni dei bilanci."""
        from modules.formats import it_text

        ctx = self.ctx
        q = ctx.quality_for(ticker)
        quality = [] if q.empty else [{"check": r.check, "detail": it_text(str(r.detail)), "severity": r.severity}
                                      for r in q.itertuples()]
        why = ctx.confidence_breakdown(ticker)
        if why is not None:
            why = {k: (_f(v) if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool) else v)
                   for k, v in why.items()}
            if why.get("reasoning"):
                why["reasoning"] = it_text(str(why["reasoning"]))
        try:
            res = ctx.valuation(ticker)
            val, fs = res["valuation"], res["factors"]
            dcf = {"fair_value": _f(val.fair_value), "margin": _f(val.margin), "multi_factor": _f(fs.total),
                   "growth": _f(val.growth), "notes": [it_text(n) for n in val.notes]}
        except Exception as exc:  # noqa: BLE001 - valutazione della strategia non disponibile
            dcf = {"error": str(exc)}
        hist = ctx.decision_history(ticker)
        decisions = [] if hist.empty else [
            {"as_of": r.get("as_of"), "action": r.get("action"), "confidence": _f(r.get("confidence")),
             "threshold": _f(r.get("threshold")), "regime": r.get("regime"), "price": _f(r.get("price")),
             "fair_value": _f(r.get("fair_value")), "margin_of_safety": _f(r.get("margin_of_safety")),
             "reasoning": it_text(str(r.get("reasoning") or ""))} for _, r in hist.iterrows()]
        v = ctx.financial_versions(ticker)
        versions = [] if v.empty else [
            {c: (_f(r[c]) if c == "value" else r[c]) for c in ("period_end", "metric", "value", "filed", "form",
                                                              "source", "accn") if c in v}
            for _, r in v.head(versions_limit).iterrows()]
        return {"ticker": ticker, "currency": ctx.currency(ticker), "quality": quality, "why": why, "dcf": dcf,
                "decisions": decisions, "versions": versions, "versions_total": int(len(v))}
