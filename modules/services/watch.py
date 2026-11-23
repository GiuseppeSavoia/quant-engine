"""Le mie aziende: aziende seguite, tabella della home con gli avvisi, tabella di Esplora con il tuo fair value,
note e scheda di ricerca."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.metrics import compute_metrics
from modules.services.base import Service


class WatchService(Service):
    def watchlist(self) -> list[str]:
        return self.db.load_watchlist()

    def follow(self, tickers, on: bool = True) -> None:
        tickers = [tickers] if isinstance(tickers, str) else list(tickers)
        (self.db.add_to_watchlist if on else self.db.remove_from_watchlist)(tickers)

    def watchlist_table(self) -> pd.DataFrame:
        """Una riga per azienda seguita: numeri principali, il tuo fair value, ultimo deposito e avvisi
        (`alerts`: lista di (icona, testo), nell'ordine di importanza)."""
        ctx = self.ctx
        tickers = ctx.watchlist()
        if not tickers:
            return pd.DataFrame()
        m = ctx.metrics()
        missing = [t for t in tickers if t not in m.index]
        if missing:  # es. un ex membro dell'indice: si calcola a parte
            m = pd.concat([m, compute_metrics(self.data, missing)])
        notes = self.db.load_notes().set_index("ticker")
        filings = ctx.latest_filings()
        research = self.db.load_research()
        reviews = dict(zip(research["ticker"], research["review_date"])) if not research.empty else {}
        ins_cfg = self.cfg.get("insiders", {})
        tx = self.data.insiders
        ins_recent = tx[tx["code"].eq("P") & (tx["filed"] > self.data.last_date - pd.Timedelta(
            days=int(ins_cfg.get("alert_days", 30))))] if not tx.empty else tx
        w = self.cfg.get("watchlist", {})
        as_of = self.data.last_date
        rows = []
        for t in tickers:
            r = m.loc[t] if t in m.index else pd.Series(dtype=float)
            price = r.get("price", np.nan)
            fv = notes["fair_value"].get(t) if t in notes.index else None
            fv = float(fv) if fv is not None and pd.notna(fv) else None
            my_margin = fv / price - 1 if fv and np.isfinite(price) and price > 0 else np.nan
            filed = filings["filed"].get(t) if t in filings.index else None
            form = filings["form"].get(t) if t in filings.index else None
            # regole condivise con il motore di avvisi (modules/alerts.py): una sola definizione per avviso
            from modules import alerts as rules

            mine = ins_recent[ins_recent["ticker"] == t] if not ins_recent.empty else ins_recent
            now, before = ctx.operating_margin_change(t)
            move = r.get("ret_1m", np.nan)
            alerts = [a for a in (
                rules.fair_value_alert(price, fv, w.get("near_fair_value", 0.05)),
                rules.filing_alert(filed, form, as_of, w.get("new_filing_days", 14)),
                rules.move_alert(move, w.get("big_move_1m", 0.15)),
                rules.drawdown_alert(r.get("drawdown_52w", np.nan), w.get("drawdown_alert", -0.25)),
                rules.insider_alert(mine, ins_cfg.get("alert_min_value", 100000), ins_cfg.get("alert_days", 30)),
                rules.review_alert(reviews.get(t), as_of),
                rules.margin_alert(now, before, w.get("margin_drop", 0.03)),
            ) if a is not None]
            info = ctx.info(t)
            rows.append({
                "ticker": t, "name": info.get("name"), "sector": info.get("sector"), "price": price,
                "ret_1m": move, "ret_1y": r.get("ret_1y", np.nan), "pe": r.get("pe", np.nan),
                "ev_ebit": r.get("ev_ebit", np.nan), "fcf_yield": r.get("fcf_yield", np.nan),
                "roic": r.get("roic", np.nan), "revenue_growth": r.get("revenue_growth", np.nan),
                "net_debt_ebitda": r.get("net_debt_ebitda", np.nan), "dividend_yield": r.get("dividend_yield", np.nan),
                "my_fair_value": fv, "my_margin": my_margin, "last_filing": filed, "last_form": form,
                "has_thesis": bool(t in notes.index and str(notes["thesis"].get(t) or "").strip()),
                "alerts": alerts,
            })
        out = pd.DataFrame(rows)
        numeric = ["price", "ret_1m", "ret_1y", "pe", "ev_ebit", "fcf_yield", "roic", "revenue_growth",
                   "net_debt_ebitda", "dividend_yield", "my_fair_value", "my_margin"]
        out[numeric] = out[numeric].apply(pd.to_numeric, errors="coerce")  # None -> NaN: celle vuote, non "None"
        return out

    def explore_table(self) -> pd.DataFrame:
        """Tutte le aziende dell'indice con le metriche del catalogo, nome, settore, il tuo fair value (e il
        margine) e se la segui. Indice = ticker."""
        ctx = self.ctx
        m = ctx.metrics().copy()
        info = ctx.companies.reindex(m.index) if not ctx.companies.empty else pd.DataFrame(index=m.index)
        m.insert(0, "name", info["name"] if "name" in info else None)
        m.insert(1, "sector", info["sector"] if "sector" in info else None)
        m.insert(2, "universe", [ctx.universe_of(t) for t in m.index])
        notes = self.db.load_notes().set_index("ticker")
        fv = pd.to_numeric(notes["fair_value"], errors="coerce").reindex(m.index) if not notes.empty \
            else pd.Series(np.nan, index=m.index)
        m["my_fair_value"] = fv
        m["my_margin"] = fv / m["price"] - 1
        m["followed"] = m.index.isin(ctx.watchlist())
        return m

    # ------------------------------------------------------------------ note e scheda di ricerca
    def note(self, ticker: str) -> dict:
        return self.db.load_note(ticker)

    def save_note(self, ticker: str, **fields) -> None:
        self.db.save_note(ticker, **fields)

    def research(self, ticker: str) -> dict:
        return self.db.load_research(ticker)

    def save_research(self, ticker: str, fields: dict) -> None:
        self.db.save_research(ticker, fields)
