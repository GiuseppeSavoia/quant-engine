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

    # ------------------------------------------------------------------ soglie di prezzo e avvisi dell'utente
    def price_target(self, ticker: str) -> dict:
        """Soglie dell'utente per il titolo: {buy_below, sell_above, note}; vuoto se non ci sono (o la tabella manca)."""
        try:
            t = self.db.load_price_targets()
        except Exception:  # noqa: BLE001 - tabella non ancora creata in un database vecchio
            return {}
        if t.empty or ticker not in set(t["ticker"]):
            return {}
        row = t[t["ticker"] == ticker].iloc[-1]
        num = lambda v: None if v is None or pd.isna(v) else float(v)  # noqa: E731
        return {"buy_below": num(row.get("buy_below")), "sell_above": num(row.get("sell_above")),
                "note": row.get("note") or ""}

    def set_price_target(self, ticker: str, buy_below: float | None = None, sell_above: float | None = None,
                         note: str | None = None) -> None:
        """Salva le soglie; ValueError (messaggio in italiano, da mostrare) se mancano entrambe o sono invertite."""
        if buy_below is None and sell_above is None:
            raise ValueError("Scrivi almeno una delle due soglie (oppure usa \"Togli le soglie\").")
        if buy_below is not None and sell_above is not None and buy_below >= sell_above:
            raise ValueError("La soglia di acquisto deve essere più bassa di quella di vendita.")
        self.db.set_price_target(ticker, buy_below=buy_below, sell_above=sell_above, note=note or None)

    def delete_price_target(self, ticker: str) -> None:
        self.db.delete_price_target(ticker)

    def active_alerts(self, ticker: str) -> pd.DataFrame:
        """Avvisi ancora attivi dell'azienda (vuoto se la tabella manca)."""
        try:
            return self.db.load_alerts(ticker=ticker, active=True)
        except Exception:  # noqa: BLE001 - tabella assente in un database vecchio
            return pd.DataFrame()

    def mark_alerts_seen(self, ids) -> None:
        ids = [int(i) for i in ids]
        if ids:
            self.db.mark_alerts_seen(ids)

    def user_data_version(self) -> str:
        """Versione dei dati scritti dall'utente (chiave di cache); "-" se il database non la sa dire."""
        try:
            return str(self.db.user_data_version())
        except Exception:  # noqa: BLE001 - una chiave di cache non deve rompere nulla
            return "-"

    # ------------------------------------------------------------------ giudizi in tabella e pagina "Le mie aziende"
    def verdict_overview(self, tickers=None) -> pd.DataFrame:
        """Giudizi (indice = ticker: name, sector, verdict, category, profile, pilastri, coverage, overall, ...) in
        ordine di giudizio. Letti dallo storico salvato ogni sera quando valgono ancora, sennò calcolati
        (`verdict_history.load_or_assess`); tenuti in memoria sul contesto finché non cambiano i fair value
        dell'utente (entrano nel pilastro Prezzo). Stessa tabella di ui/rules.verdict_table."""
        import hashlib

        from modules import verdict_history

        ctx = self.ctx
        notes = self.db.load_notes()
        fair = ""
        if not notes.empty and "fair_value" in notes:
            fair = notes[["ticker", "fair_value"]].dropna().sort_values("ticker").to_csv(index=False)
        key = f"{self.data.last_date}|{hashlib.sha1(fair.encode()).hexdigest()[:12]}"
        cache = ctx.__dict__.setdefault("_verdict_cache", {})
        want = list(dict.fromkeys(ctx.tickers if tickers is None else tickers))
        have = cache.get(key)
        missing = [t for t in want if have is None or t not in have.index]
        if missing:
            more = verdict_history.load_or_assess(ctx, missing)
            have = more if have is None else pd.concat([have, more])
            cache.clear()
            cache[key] = have
        table = have[have.index.isin(want)] if have is not None and not have.empty else pd.DataFrame()
        if table.empty:
            return table
        from modules.verdict import ORDER

        rank = table["category"].map({c: i for i, c in enumerate(ORDER)}).fillna(99)
        return table.assign(_rank=rank).sort_values(["_rank", "overall"], ascending=[True, False],
                                                    na_position="last").drop(columns="_rank")

    def _alerts_to_watch(self, table: pd.DataFrame) -> list[dict]:
        """Avvisi da guardare: quelli attivi registrati (visti o no) + quelli calcolati adesso dalle regole della home
        che non sono già registrati; nell'ordine delle aziende in tabella."""
        from modules.alerts import alert_message_it, alert_tone

        watch = list(table["ticker"])
        try:
            active = self.db.load_alerts(active=True)
        except Exception:  # noqa: BLE001 - tabella assente in un database vecchio
            active = pd.DataFrame()
        active = active[active["ticker"].isin(watch)] if not active.empty else active
        rows = active.to_dict("records") if not active.empty else []
        have = {(r["ticker"], r["code"]) for r in rows}
        for t, alerts in zip(table["ticker"], table["alerts"]):
            for code, text in alerts:
                if (t, code) not in have:
                    rows.append({"ticker": t, "code": code, "message": text, "data": {}, "created_at": None,
                                 "seen_at": None, "computed": True})
        order = {t: i for i, t in enumerate(watch)}
        rows.sort(key=lambda r: (order.get(r["ticker"], 999), str(r["code"])))
        out = []
        for r in rows:
            data = r.get("data") if isinstance(r.get("data"), dict) else {}
            seen = r.get("seen_at")
            out.append({"id": None if r.get("computed") else int(r["id"]), "ticker": r["ticker"], "code": r["code"],
                        "tone": alert_tone(str(r["code"]), data), "message": alert_message_it(r["code"], r["message"]),
                        "created_at": r.get("created_at"), "computed": bool(r.get("computed")),
                        "unseen": not r.get("computed") and (seen is None or (isinstance(seen, float) and np.isnan(seen)))})
        return out

    def my_companies(self) -> dict:
        """La pagina "Le mie aziende" pronta: una riga per azienda seguita (numeri, giudizio con i pilastri, tuo fair
        value e soglie con i margini, avvisi), gli avvisi da guardare e, se non segui nulla, le prime «Candidate»."""
        ctx = self.ctx
        watch = ctx.watchlist()
        out = {"as_of": self.data.last_date, "count": len(watch)}
        if not watch:
            v = ctx.verdict_overview(list(ctx.index_tickers))
            top = v[v["category"] == "candidata"].head(6) if not v.empty else v
            return {**out, "rows": [], "alerts": [],
                    "suggestions": [{"ticker": t, "name": r.get("name") or ctx.info(t).get("name") or ""}
                                    for t, r in top.iterrows()]}
        table = ctx.watchlist_table()
        v = ctx.verdict_overview(watch).reindex(table["ticker"])
        for c in ["category", "verdict", "coverage", "overall"]:
            table[c] = v[c].to_numpy() if c in v else None
        for p in ["quality", "solidity", "price", "flags", "insiders"]:  # "pillar_price" ≠ "price" (il prezzo)
            table[f"pillar_{p}"] = pd.to_numeric(v[p], errors="coerce").to_numpy(dtype=float) if p in v else np.nan
        try:
            targets = self.db.load_price_targets()
        except Exception:  # noqa: BLE001
            targets = pd.DataFrame()
        targets = targets.set_index("ticker") if not targets.empty else pd.DataFrame(columns=["buy_below", "sell_above"])
        table["buy_below"] = pd.to_numeric(table["ticker"].map(targets["buy_below"]), errors="coerce")
        table["sell_above"] = pd.to_numeric(table["ticker"].map(targets["sell_above"]), errors="coerce")
        price = table["price"].where(table["price"] > 0)
        table["buy_margin"] = table["buy_below"] / price - 1
        table["currency"] = [ctx.currency(t) for t in table["ticker"]]
        from modules.verdict import ORDER

        table["_rank"] = table["category"].map({c: i for i, c in enumerate(ORDER)}).fillna(99)
        table = table.sort_values(["_rank", "overall"], ascending=[True, False], na_position="last").drop(columns="_rank")
        alerts = self._alerts_to_watch(table)
        rows = table.drop(columns=["alerts"]).assign(alert_count=table["alerts"].map(len)).to_dict("records")
        spark = ctx.sparklines([r["ticker"] for r in rows])
        for r in rows:
            r["spark"] = spark.get(r["ticker"])
        return {**out, "rows": rows, "alerts": alerts, "suggestions": []}

    def alert_history(self, state: str = "attivi", limit: int = 500) -> list[dict]:
        """Storico degli avvisi delle aziende seguite: "attivi", "non_visti", "chiusi" o "tutti" (dal più recente)."""
        from modules.alerts import alert_message_it, alert_tone

        if state not in ("attivi", "non_visti", "chiusi", "tutti"):
            raise ValueError(f"Stato sconosciuto: {state}")
        try:
            hist = self.db.load_alerts(limit=2000)
        except Exception:  # noqa: BLE001
            return []
        if hist.empty:
            return []
        hist = hist[hist["ticker"].isin(self.ctx.watchlist())]
        if state == "attivi":
            hist = hist[hist["cleared_at"].isna()]
        elif state == "non_visti":
            hist = hist[hist["seen_at"].isna()]
        elif state == "chiusi":
            hist = hist[hist["cleared_at"].notna()]
        return [{"id": int(r["id"]), "ticker": r["ticker"], "code": r["code"],
                 "tone": alert_tone(str(r["code"]), r["data"] if isinstance(r["data"], dict) else {}),
                 "message": alert_message_it(r["code"], r["message"]), "created_at": r["created_at"],
                 "cleared_at": r["cleared_at"], "seen_at": r["seen_at"]} for _, r in hist.head(limit).iterrows()]
