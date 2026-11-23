"""Pagina "Oggi": è successo qualcosa alle MIE aziende? Tutto dal database e dalle copie salvate (nessuna quotazione
dal vivo, nessun download): le mie aziende con prezzo, variazione, margine sul fair value, soglia e giudizio; avvisi
attivi (i non visti in cima, i movimenti di prezzo a parte); notizie; documenti arrivati (10-Q/10-K e 8-K);
movimenti dell'indice; acquisti degli insider; stato delle borse."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from modules.services.base import Service

NOT_JUDGED = "non_valutato"  # titoli fuori dall'anagrafica (ETF, aziende solo nel portafoglio)


class TodayService(Service):
    def _held(self) -> set[str]:
        try:
            tx = self.db.load_transactions()
        except Exception:  # noqa: BLE001 - senza tabella del portafoglio: nessuna posizione
            return set()
        if tx.empty:
            return set()
        q = tx[tx["kind"].isin(["BUY", "SELL"])]
        signed = np.where(q["kind"] == "BUY", 1.0, -1.0) * q["quantity"].fillna(0.0)
        held = pd.Series(signed, index=q.index).groupby(q["ticker"]).sum()
        return {t for t, n in held.items() if n > 1e-9}

    def _quotes(self, tickers: list[str]) -> pd.DataFrame:
        """Ultime due chiusure del database: last, prev, change, change_pct, date."""
        from modules.quotes import quote_table

        cols = [t for t in tickers if t in self.data.close]
        return quote_table(self.data.recent_close(cols, 6)) if cols else pd.DataFrame()

    def _alerts(self, limit: int = 200) -> tuple[list[dict], list[dict]]:
        from modules.alerts import ALERT_PRIORITY, PRICE_MOVES, alert_message_it, alert_tone

        try:
            al = self.db.load_alerts(active=True, limit=limit)
        except Exception:  # noqa: BLE001
            return [], []
        if al.empty:
            return [], []
        code = al["code"].astype(str)
        prio = code.map({c: i for i, c in enumerate(ALERT_PRIORITY)}).fillna(len(ALERT_PRIORITY))
        al = al.assign(_new=al["seen_at"].isna(), _prio=prio).sort_values(["_new", "_prio", "id"],
                                                                         ascending=[False, True, False])
        rows = [{"id": int(r["id"]), "ticker": r["ticker"], "code": r["code"],
                 "tone": alert_tone(str(r["code"]), r["data"] if isinstance(r["data"], dict) else {}),
                 "message": alert_message_it(r["code"], r["message"]), "created_at": r["created_at"],
                 "unseen": bool(r["_new"]), "move": r["code"] in PRICE_MOVES} for _, r in al.iterrows()]
        return [r for r in rows if not r["move"]], [r for r in rows if r["move"]]

    def _news(self, mine: list[str], limit: int = 10) -> tuple[list[dict], bool]:
        """Ultime notizie dalla copia salvata dei feed: delle mie aziende se ce ne sono, sennò tutte."""
        from modules import news as news_mod

        n = self.cfg.get("news") or {}
        if not n.get("enabled", True):
            return [], False
        cache = Path(n.get("cache_dir", "data/cache/news"))
        if not cache.is_absolute():
            from modules.config import PROJECT_ROOT

            cache = PROJECT_ROOT / cache
        try:
            table = news_mod.NewsStore(cache / "notizie.json", keep_days=float(n.get("keep_days", 3))).frame()
        except Exception:  # noqa: BLE001 - le notizie sono un di più
            return [], False
        own = news_mod.latest(table, mine, limit) if mine else pd.DataFrame()
        use_mine = not own.empty
        rows = own if use_mine else news_mod.latest(table, None, limit)
        return [{"published": r["published"], "source": r["source"], "title": r["title"], "link": r["link"],
                 "tickers": str(r["tickers"]).split()} for _, r in rows.iterrows()], use_mine

    def _documents(self, mine: list[str], days: int, only_mine: bool, limit: int = 12) -> list[dict]:
        ctx = self.ctx
        frames = []
        f = ctx.recent_filings(max(days, 7))
        if not f.empty:
            frames.append(pd.DataFrame({"when": pd.to_datetime(f["filed"]), "ticker": f["ticker"], "form": f["form"],
                                        "what": f.get("status", pd.Series("", index=f.index)).fillna(""),
                                        "important": False, "url": None}))
        ev = ctx.news(days, mine if (only_mine and mine) else None)
        if not ev.empty:
            frames.append(pd.DataFrame({"when": pd.to_datetime(ev["when"]), "ticker": ev["ticker"], "form": ev["form"],
                                        "what": ev["what"], "important": ev["important"], "url": ev["url"]}))
        frames = [x for x in frames if not x.empty]
        if not frames:
            return []
        docs = pd.concat(frames, ignore_index=True)
        if only_mine and mine:
            docs = docs[docs["ticker"].isin(mine)]
        docs = docs.assign(_mine=docs["ticker"].isin(mine)).sort_values(["_mine", "when"], ascending=[False, False])
        return [{"when": r["when"], "ticker": r["ticker"], "name": ctx.info(r["ticker"]).get("name") or "",
                 "form": r["form"], "what": r["what"], "important": bool(r["important"]),
                 "url": r["url"] if isinstance(r["url"], str) else None, "mine": bool(r["_mine"])}
                for _, r in docs.head(limit).iterrows()]

    def _insiders(self, days: int = 7) -> list[dict]:
        tx = self.data.insiders
        if tx is None or tx.empty:
            return []
        buys = tx[(tx["code"] == "P") & (tx["filed"] >= pd.Timestamp.now() - pd.Timedelta(days=days))]
        if buys.empty:
            return []
        top = (buys.groupby("ticker").agg(value=("value", "sum"), buyers=("owner", "nunique"))
               .sort_values("value", ascending=False).head(10).reset_index())
        return [{"ticker": r["ticker"], "name": self.ctx.info(r["ticker"]).get("name") or "", "value": r["value"],
                 "buyers": int(r["buyers"])} for _, r in top.iterrows()]

    def _exchanges(self) -> list[dict]:
        from modules import exchanges

        conf = self.cfg.get("monitor", {}).get("exchanges") or {c: [None, ""] for c in exchanges.DEFAULT}
        out = []
        for b in exchanges.board(list(conf)):
            out.append({"code": b["code"], "name": b["name"], "local": f"{b['local']:%H:%M}", "state": b["state"],
                        "open": bool(b["open"]), "holiday": b.get("holiday"), "hours": b["hours"],
                        "until": exchanges.until_text(b["until"]), "index": (conf.get(b["code"]) or [None, ""])[1],
                        "holidays_complete": bool(b.get("holidays_complete", True))})
        return out

    def today(self, only_mine_documents: bool = False) -> dict:
        from modules import alerts as alert_rules
        from modules.verdict import ORDER

        ctx = self.ctx
        try:
            mine = alert_rules.followed_tickers(self.db)
        except Exception:  # noqa: BLE001 - tabelle del portafoglio o delle soglie mancanti: restano le seguite
            mine = list(ctx.watchlist())
        held = self._held()
        notes = self.db.load_notes()
        fair = (pd.to_numeric(notes.set_index("ticker")["fair_value"], errors="coerce").dropna().to_dict()
                if not notes.empty and "fair_value" in notes else {})
        buy = {k: v["buy_below"] for k, v in ((t, ctx.price_target(t)) for t in mine) if v.get("buy_below")}
        known = set(ctx.tickers)
        verdicts = ctx.verdict_overview([t for t in mine if t in known]) if mine else pd.DataFrame()
        main_alerts, moves = self._alerts()
        unseen = pd.Series([a["ticker"] for a in main_alerts + moves if a["unseen"]]).value_counts().to_dict()
        q = self._quotes(list(dict.fromkeys(list(ctx.index_tickers) + mine)))
        rows = []
        for t in mine:
            last = float(q.at[t, "last"]) if t in q.index else np.nan
            chg = float(q.at[t, "change_pct"]) if t in q.index else np.nan
            ret_1y = np.nan
            if t in self.data.close:
                s = self.data.close[t].dropna()
                past = s[s.index <= s.index[-1] - pd.Timedelta(days=365)] if len(s) > 200 else s.iloc[:0]
                if len(past) and past.iloc[-1] > 0:
                    ret_1y = float(s.iloc[-1] / past.iloc[-1] - 1)
            fv, bb = fair.get(t), buy.get(t)
            cat = (NOT_JUDGED if t not in known else
                   verdicts.at[t, "category"] if not verdicts.empty and t in verdicts.index else "dati_insufficienti")
            rows.append({"ticker": t, "name": ctx.info(t).get("name") or "", "last": last, "change_pct": chg,
                         "ret_1y": ret_1y, "margin": fv / last - 1 if fv and np.isfinite(last) and last > 0 else None,
                         "buy_below": bb, "buy_gap": last / bb - 1 if bb and np.isfinite(last) else None,
                         "category": cat,
                         "verdict": verdicts.at[t, "verdict"] if not verdicts.empty and t in verdicts.index else "",
                         "rule": verdicts.at[t, "rule"] if not verdicts.empty and t in verdicts.index and "rule" in verdicts else "",
                         "held": t in held, "unseen": int(unseen.get(t, 0)),
                         "_order": ORDER.index(cat) if cat in ORDER else len(ORDER)})
        rows.sort(key=lambda r: (-r["unseen"], r["_order"], r["ticker"]))
        for r in rows:
            r.pop("_order")
        idx = q[q.index.isin(ctx.index_tickers)].dropna(subset=["change_pct"]).sort_values("change_pct") \
            if not q.empty else q
        mover = lambda part: [{"ticker": t, "name": ctx.info(t).get("name") or "", "last": r["last"],  # noqa: E731
                               "change_pct": r["change_pct"]} for t, r in part.iterrows()]
        news, news_mine = self._news(mine)
        days = int(self.cfg.get("monitor", {}).get("news_days", 3))
        price_date = q["date"].max() if not q.empty else self.data.last_date
        return {
            "as_of": price_date, "source": f"chiusure del {pd.Timestamp(price_date):%d/%m/%Y}" if price_date is not None else "",
            "mine": rows, "alerts": main_alerts, "moves": moves, "unseen": len([a for a in main_alerts + moves if a["unseen"]]),
            "news": news, "news_mine": news_mine, "documents": self._documents(mine, days, only_mine_documents),
            "documents_days": days,
            "movers": {"up": mover(idx[idx["change_pct"] > 0].iloc[::-1].head(8)) if not idx.empty else [],
                       "down": mover(idx[idx["change_pct"] < 0].head(8)) if not idx.empty else []},
            "insiders": self._insiders(), "insiders_loaded": self.data.insiders is not None and not self.data.insiders.empty,
            "exchanges": self._exchanges(),
        }
