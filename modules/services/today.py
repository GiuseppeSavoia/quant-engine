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
        spark = ctx.sparklines([r["ticker"] for r in rows])
        for r in rows:
            r.pop("_order")
            r["spark"] = spark.get(r["ticker"])
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

    # ------------------------------------------------------------------ minigrafici e striscia di mercato
    def sparklines(self, tickers: list[str], weeks: int = 52) -> dict[str, list]:
        """Chiusure dell'ultimo anno, una a settimana (l'ultima sempre compresa), per i minigrafici delle tabelle."""
        close = self.data.close
        out: dict[str, list] = {}
        for t in dict.fromkeys(tickers):
            if t not in close:
                continue
            s = close[t].dropna()
            if len(s) < 10:
                continue
            s = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
            pts = s.iloc[::5]
            if pts.index[-1] != s.index[-1]:
                pts = pd.concat([pts, s.iloc[-1:]])
            out[t] = [round(float(v), 4) for v in pts.iloc[-(weeks + 1):]]
        return out

    def market_strip(self) -> dict:
        """Striscia di mercato dai soli prezzi salvati (nessuna quotazione dal vivo): S&P 500 (SPY), l'indice a pesi
        uguali dei membri, il tasso USA a 10 anni, quanti titoli sono saliti nell'ultima seduta e nuovi massimi/minimi a
        52 settimane, più Euro Stoxx 50 ed euro/dollaro se scaricati."""
        close = self.data.close
        members = [t for t in self.ctx.index_tickers if t in close]
        items = []

        def series_item(key, label, s, kind="price", rate=False):
            s = s.dropna()
            if len(s) < 3:
                return
            year = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
            chg = float(s.iloc[-1] - s.iloc[-2]) * 100 if rate else float(s.iloc[-1] / s.iloc[-2] - 1)
            items.append({"key": key, "label": label, "value": float(s.iloc[-1]) / (100 if rate else 1), "kind": kind,
                          "change": chg, "change_kind": "bp" if rate else "pct",
                          "change_1y": None if rate or len(year) < 2 else float(year.iloc[-1] / year.iloc[0] - 1),
                          "spark": [round(float(v), 4) for v in year.iloc[::5]] + [round(float(year.iloc[-1]), 4)]})

        if "SPY" in close:
            series_item("SPY", "S&P 500 (SPY)", close["SPY"])
        if members:
            recent = close[members].iloc[-260:]
            ew = (1 + recent.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)).cumprod() * 100
            series_item("EW", "S&P 500 a pesi uguali", ew, kind="num")
            last = close[members].iloc[-2:]
            moved = (last.iloc[-1] / last.iloc[-2] - 1).dropna()
            year = close[members].iloc[-252:]
            highs = int((year.iloc[-1] >= year.max() * 0.9999).sum())
            lows = int((year.iloc[-1] <= year.min() * 1.0001).sum())
            if len(moved):
                items.append({"key": "BREADTH", "label": "Titoli in salita", "value": float((moved > 0).mean()), "kind": "pct",
                              "detail": f"{int((moved > 0).sum())} su {len(moved)}", "highs": highs, "lows": lows,
                              "change": None, "spark": None})
        if "^TNX" in close:
            series_item("TNX", "Tasso USA 10 anni", close["^TNX"], kind="pct", rate=True)
        if "^STOXX50E" in close:
            series_item("SX5E", "Euro Stoxx 50", close["^STOXX50E"], kind="num")
        if "EURUSD=X" in close:
            series_item("EURUSD", "Euro / dollaro", close["EURUSD=X"], kind="num")
        return {"as_of": self.data.last_date, "items": items}
