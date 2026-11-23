"""Notizie dalla SEC: 8-K recenti (tutte le aziende dell'app o alcune) e 10-Q/10-K appena visti nell'indice SEC.
I feed RSS sono in modules/news.py (letti dalla pagina Notizie)."""
from __future__ import annotations

import pandas as pd

from modules.services.base import Service


class NewsService(Service):
    def news(self, days: int = 3, tickers=None) -> pd.DataFrame:
        """8-K degli ultimi `days` giorni (tutte le aziende o `tickers`): quando, chi, cosa, se da guardare."""
        from modules.sec_filings import describe_items, filing_url, important

        since = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        ev = self.db.load_events(tickers if tickers is not None else self.ctx.tickers, since=since)  # niente ex membri
        if ev.empty:
            return ev
        ev["when"] = ev["accepted"].fillna(ev["filed"])
        ev["name"] = [self.ctx.info(t).get("name") or "" for t in ev["ticker"]]
        ev["what"] = [describe_items(i) if isinstance(i, str) else "(voci in arrivo)" for i in ev["items"]]
        ev["important"] = [important(i) for i in ev["items"]]
        ev["url"] = [filing_url(c, a) for c, a in zip(ev["cik"], ev["accession"])]
        return ev.sort_values("when", ascending=False).reset_index(drop=True)

    def recent_filings(self, days: int = 14) -> pd.DataFrame:
        """10-Q/10-K delle aziende seguite visti nell'indice SEC negli ultimi `days` giorni, con lo stato."""
        since = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        return self.db.load_filings(since=since)
