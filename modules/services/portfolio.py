"""Portafoglio reale: posizioni, vendite, dividendi, tasse stimate e andamento contro il benchmark; esposizione vera
guardando dentro gli ETF; diario delle decisioni con il senno di poi."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from modules.formats import fmt
from modules.services.base import Service



# valute delle operazioni e valuta proposta dal suffisso del simbolo Yahoo (anche la pagina Streamlit le usa)
SUFFIX_CCY = {"MI": "EUR", "DE": "EUR", "F": "EUR", "PA": "EUR", "AS": "EUR", "MC": "EUR", "BR": "EUR", "LS": "EUR",
              "VI": "EUR", "HE": "EUR", "IR": "EUR", "SW": "CHF", "L": "GBP", "CO": "DKK", "ST": "SEK", "OL": "NOK",
              "TO": "CAD", "T": "JPY", "HK": "HKD"}
CURRENCIES = ["EUR", "USD", "GBP", "CHF", "DKK", "SEK", "NOK", "CAD", "JPY", "HKD"]


def guess_currency(symbol: str) -> str:
    """Valuta proposta dal suffisso del simbolo Yahoo (.MI -> EUR); nessun suffisso = borsa americana (USD)."""
    s = (symbol or "").strip().upper()
    return SUFFIX_CCY.get(s.rsplit(".", 1)[1], "EUR") if "." in s else "USD"


def held_quantity(tx: pd.DataFrame, ticker: str, day) -> float:
    """Azioni di `ticker` possedute alla data `day` (acquisti meno vendite fino a quel giorno compreso)."""
    if tx is None or tx.empty:
        return 0.0
    t = tx[(tx["ticker"] == ticker) & tx["kind"].isin(["BUY", "SELL"])
           & (pd.to_datetime(tx["date"]) <= pd.Timestamp(day))]
    sign = np.where(t["kind"] == "BUY", 1.0, -1.0)
    return float((sign * pd.to_numeric(t["quantity"], errors="coerce").fillna(0.0)).sum())


class PortfolioService(Service):
    def _fx_close(self) -> pd.DataFrame:
        cols = [c for c in self.data.close.columns if str(c).startswith("EUR") and str(c).endswith("=X")]
        return self.data.close[cols]

    def portfolio_view(self, live_prices: dict[str, float] | None = None) -> dict:
        """Posizioni, vendite realizzate, dividendi, tasse stimate e andamento contro il benchmark (config: sezione
        portfolio). `live_prices`: prezzi per i titoli non ancora nel database (scaricati al momento)."""
        from modules import portfolio as pf

        tx = self.db.load_transactions()
        if tx.empty:
            return {"tx": tx}
        close, fx = self.data.close, self._fx_close()
        prices = {t: float(close[t].dropna().iloc[-1]) for t in tx["ticker"].unique()
                  if t in close and close[t].notna().any()}
        prices.update({k: v for k, v in (live_prices or {}).items() if k not in prices})
        today_fx = {c: (float(fx[pf.fx_symbol(c)].dropna().iloc[-1])
                        if pf.fx_symbol(c) in fx and fx[pf.fx_symbol(c)].notna().any()
                        else float((live_prices or {}).get(pf.fx_symbol(c), np.nan)))  # cambio appena scaricato
                    for c in tx["currency"].dropna().unique() if c != "EUR"}
        today_fx["EUR"] = 1.0
        pos, realized = pf.positions(tx, fx, prices, today_fx)
        if not pos.empty:
            pos.insert(1, "name", [self.ctx.info(t).get("name") or "" for t in pos["ticker"]])
            pos.insert(2, "sector", [self.ctx.info(t).get("sector") or "Altro" for t in pos["ticker"]])
        cfg = self.cfg.get("my_portfolio", {})
        bench = cfg.get("benchmark", self.data.benchmark)
        return {"tx": tx, "positions": pos, "realized": realized,
                "dividends": pf.dividends(tx, fx, set(self.ctx.index_tickers)),
                "taxes": pf.tax_estimate(realized, pos, funds=self._known_funds(  # anche i fondi già venduti
                    list(dict.fromkeys(list(pos.get("ticker", [])) + list(realized.get("ticker", [])))))),
                "benchmark": bench,
                "history": pf.value_history(tx, close, fx, bench, cfg.get("benchmark_currency", "USD")),
                "missing_prices": sorted(set(pos["ticker"]) - set(prices)) if not pos.empty else [],
                "missing_fx": pf.missing_fx(tx, fx)}

    def _known_funds(self, tickers: list[str]) -> set[str]:
        """Titoli che la copia delle composizioni (Esposizione vera) riconosce come fondi/ETF. Un titolo mai
        scaricato conta come azione: senza un'anagrafica non si può distinguere ENI.MI da VWCE.DE."""
        from modules import etf

        try:
            cache = etf.JsonCache(self.ctx.etf_cache_path())
            return {t for t in tickers if cache.get(t) and etf.EtfComposition.from_dict(cache.get(t)).is_fund}
        except Exception:  # noqa: BLE001 - copia illeggibile: nessun fondo noto
            return set()

    def etf_cache_path(self) -> Path:
        """File della cache delle composizioni degli ETF: uno per database (la demo non tocca quella vera)."""
        from modules import etf

        stem = Path(self.db.db_path).stem
        name = etf.CACHE_FILE if stem == "finance_lake" else f"composizioni_{stem}.json"
        return etf.settings(self.cfg)["cache_dir"] / name

    def portfolio_look_through(self, positions: pd.DataFrame | None = None, refresh: bool = False, source=None,
                               today=None) -> dict:
        """Esposizione vera del portafoglio guardando dentro gli ETF (modules/etf.py, config: sezione etf).

        Candidati ETF = titoli del portafoglio senza settore nell'anagrafica (le aziende note non si chiedono a
        Yahoo). Le composizioni si scaricano SOLO con `refresh=True` (pulsante) o se la copia in cache è scaduta;
        un simbolo mai scaricato resta "da scaricare" e conta tutto come non dettagliato. `source`: sorgente finta
        nei test. Restituisce status ("no_positions" | "no_etf" | "empty" | "ok"), look (look_through),
        concentration, overlaps (coppie di ETF: intervallo e sovrapposizione dei settori), compositions,
        to_download, failed (scarico fallito: si usa la copia vecchia), fetched (ETF -> data dello scarico)."""
        from modules import etf

        ctx = self.ctx
        out = {"status": "no_positions", "look": None, "concentration": None, "overlaps": [], "compositions": {},
               "to_download": [], "failed": [], "fetched": {}, "etfs": [], "cache_path": str(ctx.etf_cache_path())}
        if positions is None:
            positions = ctx.portfolio_view().get("positions", pd.DataFrame())
        if positions is None or positions.empty:
            return out
        s = etf.settings(self.cfg)
        cache = etf.JsonCache(ctx.etf_cache_path())
        ing = etf.ingestor_from_config(self.cfg, source=source, cache=cache)
        today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
        tickers = sorted(set(positions["ticker"]))
        candidates = [t for t in tickers if not ctx.info(t).get("sector")]
        stale = [t for t in candidates if cache.get(t) and not ing._fresh(cache.get(t), today)]
        to_fetch = candidates if refresh else stale
        if to_fetch:
            ing.get_many(to_fetch, today=today, refresh=refresh)
            out["failed"] = [t for t in to_fetch
                             if not cache.get(t) or pd.Timestamp(cache.get(t)["fetched"]) < today]
        comps = {t: etf.EtfComposition.from_dict(cache.get(t)) for t in candidates if cache.get(t)}
        out["compositions"] = comps
        out["to_download"] = [t for t in candidates if t not in comps]
        out["etfs"] = sorted([t for t, c in comps.items() if c.is_fund] + out["to_download"])
        out["fetched"] = {t: c.fetched for t, c in comps.items() if c.is_fund}

        holdings, sectors, asset_classes = etf.split_compositions(comps)
        company_sector = {**{k: v for k, v in ctx.sectors().items() if isinstance(v, str) and v},
                          **{t: ctx.info(t).get("sector") for t in tickers if ctx.info(t).get("sector")}}
        pos = positions[["ticker", "value_eur"]].copy()
        names = positions["name"] if "name" in positions else pd.Series("", index=positions.index)
        pos["name"] = [n if isinstance(n, str) and n else (comps[t].name if t in comps else "")
                       for t, n in zip(positions["ticker"], names)]
        look = etf.look_through(pos, holdings, sectors, company_sector, asset_classes,
                                etfs=out["to_download"], aliases=s["aliases"])
        out["look"] = look
        out["concentration"] = etf.concentration(look["by_company"], n=s["top_n"],
                                                 total=look["summary"]["total_eur"])
        funds = sorted(holdings)
        for i, a in enumerate(funds):
            for b in funds[i + 1:]:
                ov = etf.overlap(comps[a], comps[b], aliases=s["aliases"])
                ov.update({"a": a, "b": b, "sector_overlap": etf.sector_overlap(comps[a].sectors, comps[b].sectors)})
                out["overlaps"].append(ov)
        if not out["etfs"]:
            out["status"] = "no_etf"
        elif not any(c.is_fund for c in comps.values()):
            out["status"] = "empty"
        else:
            out["status"] = "ok"
        return out

    def journal_view(self) -> pd.DataFrame:
        from modules import journal

        j = self.db.load_decisions_journal()
        out = journal.outcomes(j, self.data.close, self.cfg.get("my_portfolio", {}).get("benchmark", self.data.benchmark))
        if not out.empty:
            out.insert(2, "name", [self.ctx.info(t).get("name") or "" for t in out["ticker"]])
        return out

    # ------------------------------------------------------------------ diario delle decisioni (pagina Diario)
    def journal_benchmark(self) -> str:
        return self.cfg.get("my_portfolio", {}).get("benchmark") or self.data.benchmark or "indice"

    def journal_page(self) -> dict:
        """Il diario pronto: riepilogo (decisioni, giudicabili, quante con ragione, revisioni scadute), le decisioni
        da rivedere e tutte le decisioni con rendimento, indice, differenza ed esito (dopo 3 mesi)."""
        from modules import journal

        out = self.ctx.journal_view()
        h = journal.hit_rate(out) if not out.empty else {"judged": 0, "right": 0, "rate": None, "by_action": {}}
        rows = []
        for _, r in (out.iterrows() if not out.empty else iter(())):
            right = r.get("right")
            rows.append({"id": int(r["id"]), "date": r["date"], "ticker": r["ticker"], "name": r.get("name") or "",
                         "action": r["action"], "action_label": journal.ACTIONS.get(r["action"], r["action"]),
                         "price": r.get("price"), "currency": self.ctx.currency(r["ticker"]),
                         "ret": r.get("ret"), "bench_ret": r.get("bench_ret"), "excess": r.get("excess"),
                         "right": None if right is None or (isinstance(right, float) and np.isnan(right)) else bool(right),
                         "days": int(r["days"]) if pd.notna(r.get("days")) else None,
                         "reason": r.get("reason"), "change_mind": r.get("change_mind"),
                         "review_date": r.get("review_date"), "review_due": bool(r.get("review_due")),
                         "verdict": r.get("verdict") if isinstance(r.get("verdict"), str) else None,
                         "review_note": r.get("review_note") if isinstance(r.get("review_note"), str) else None})
        return {"benchmark": self.journal_benchmark(), "actions": [{"value": k, "label": v} for k, v in journal.ACTIONS.items()],
                "verdicts": journal.VERDICTS, "min_days": journal.MIN_DAYS_FOR_VERDICT,
                "summary": {"decisions": len(rows), "judged": h["judged"], "right": h["right"], "rate": h["rate"],
                            "due": sum(1 for r in rows if r["review_due"])},
                "entries": rows}

    def add_journal_decision(self, ticker: str, action: str, reason: str, change_mind: str | None = None,
                             price: float | None = None, review_date=None) -> int:
        """Annota una decisione; prezzo vuoto = ultima chiusura, revisione vuota = fra 6 mesi. ValueError con un
        messaggio da mostrare se mancano l'azienda o il perché o la decisione non è tra quelle previste."""
        from modules import journal

        if ticker not in set(self.ctx.tickers):
            raise ValueError(f"Azienda sconosciuta: {ticker}")
        if action not in journal.ACTIONS:
            raise ValueError(f"Decisione non prevista: {action} (ammesse: {', '.join(journal.ACTIONS)})")
        if not (reason or "").strip():
            raise ValueError("Scrivi il perché: la tesi in due righe.")
        if price is None:
            px = self.data.close[ticker].dropna() if ticker in self.data.close else pd.Series(dtype=float)
            price = float(px.iloc[-1]) if not px.empty else None
        elif not (price > 0):
            raise ValueError("Il prezzo deve essere positivo.")
        review = pd.Timestamp(review_date) if review_date else pd.Timestamp.today().normalize() + pd.DateOffset(months=6)
        return self.db.add_decision({"ticker": ticker, "date": pd.Timestamp.today().normalize(), "action": action,
                                     "price": price, "reason": reason.strip(),
                                     "change_mind": (change_mind or "").strip() or None, "review_date": review})

    def review_journal_decision(self, dec_id: int, verdict: str, note: str = "") -> None:
        from modules import journal

        if verdict not in journal.VERDICTS:
            raise ValueError("Scegli un giudizio: avevo ragione, avevo torto o troppo presto.")
        ids = set(self.db.load_decisions_journal()["id"].astype(int))
        if int(dec_id) not in ids:
            raise ValueError(f"Decisione sconosciuta: {dec_id}")
        self.db.review_decision(int(dec_id), (note or "").strip(), verdict)

    def delete_journal_decision(self, dec_id: int) -> None:
        self.db.delete_decision(int(dec_id))

    # ------------------------------------------------------------------ pagina Portafoglio (interfaccia web)
    def portfolio_page(self) -> dict:
        """Il portafoglio pronto da mostrare, solo con i dati salvati (prezzi e cambi del database; un titolo appena
        inserito ha il prezzo dal prossimo aggiornamento): riepilogo in euro, avvisi, posizioni con il giudizio,
        composizione, andamento contro il benchmark, operazioni, dividendi e tasse stimate."""
        from modules import portfolio as pf

        ctx = self.ctx
        view = ctx.portfolio_view(None)
        tx = view["tx"]
        base = {"kinds": [{"value": k, "label": v} for k, v in pf.KINDS.items()], "currencies": CURRENCIES,
                "tax_note": "stima, non è consulenza fiscale"}
        if tx.empty:
            return {**base, "empty": True}
        pos = view.get("positions", pd.DataFrame())
        notes = []
        summary = None
        if not pos.empty:
            value, cost = pos["value_eur"].sum(min_count=1), pos["cost_eur"].sum(min_count=1)
            complete = bool(pos["value_eur"].notna().all())
            gain = value - cost if complete else np.nan
            realized = view["realized"]["gain_eur"].sum() if not view["realized"].empty else 0.0
            div = view["dividends"]
            h = view.get("history", pd.DataFrame())
            vs = np.nan
            if complete and not h.empty and "benchmark" in h:
                last = h.dropna(subset=["invested", "benchmark"]).tail(1)
                if not last.empty and last["invested"].iloc[0] > 0:
                    vs = (value - last["benchmark"].iloc[0]) / last["invested"].iloc[0]
            if not pos["cost_eur"].notna().all():  # costo in euro mancante: niente totali parziali
                cost, gain, vs = np.nan, np.nan, np.nan
            summary = {"value": value if complete else None, "invested": cost, "gain": gain,
                       "gain_pct": gain / cost if cost and np.isfinite(cost) and cost else None, "vs_benchmark": vs,
                       "realized": realized, "dividends_net": float(div["net_eur"].sum()) if not div.empty else 0.0}
            days = [self.data.close[t].last_valid_index() for t in pos["ticker"] if t in self.data.close]
            days = [x for x in days if x is not None]
            summary["prices_at"] = min(days) if days else None
            if view.get("missing_prices"):
                notes.append({"tone": "warn", "title": "Prezzi mancanti:", "text": f"Senza prezzo: {', '.join(view['missing_prices'])}. "
                              "Il simbolo Yahoo è giusto? Il prezzo arriva con il prossimo aggiornamento dei dati; fino "
                              "ad allora valore e guadagno totali restano vuoti."})
            mfx = view.get("missing_fx")
            if mfx is not None and not mfx.empty:
                which = ", ".join(f"{r.ticker} del {fmt(r.date, 'date')}" for r in mfx.itertuples(index=False))
                notes.append({"tone": "warn", "title": "Cambio dell'operazione mancante:",
                              "text": f"{which}: manca il cambio di quel giorno ({', '.join(sorted(set(mfx['currency'])))} "
                                      "per 1 €), quindi il costo in euro e i totali restano vuoti. Arriva con il prossimo "
                                      "aggiornamento dei dati, oppure reinserisci l'operazione con il cambio dell'estratto "
                                      "del broker."})
            no_fx = sorted(pos.loc[pos["value_eur"].isna() & pos["price"].notna(), "ticker"])
            if no_fx:
                notes.append({"tone": "warn", "title": "Cambio mancante:",
                              "text": f"{', '.join(no_fx)}: manca il cambio di oggi nel database, quindi il valore in euro "
                                      "resta vuoto. Arriva con il prossimo aggiornamento dei dati."})
        known = set(ctx.tickers)
        stocks = [t for t in pos.get("ticker", []) if t in known]
        verdicts = ctx.verdict_overview(stocks) if stocks else pd.DataFrame()
        positions = []
        for _, r in (pos.sort_values("value_eur", ascending=False, na_position="last").iterrows() if not pos.empty else iter(())):
            t = r["ticker"]
            positions.append({**{k: r.get(k) for k in ("ticker", "name", "sector", "quantity", "avg_cost", "price",
                                                       "currency", "value_eur", "cost_eur", "weight", "gain_eur",
                                                       "gain_pct")},
                              "known": t in known,
                              "category": verdicts.at[t, "category"] if not verdicts.empty and t in verdicts.index else None,
                              "verdict": verdicts.at[t, "verdict"] if not verdicts.empty and t in verdicts.index else None})
        composition = []
        if not pos.empty and pos["value_eur"].notna().any() and pos["value_eur"].sum() > 0:
            total = pos["value_eur"].sum()
            for kind, col in (("Settore", "sector"), ("Valuta", "currency")):
                for k, v in pos.groupby(col)["value_eur"].sum().sort_values(ascending=False).items():
                    composition.append({"kind": kind, "name": k, "share": float(v / total)})
        h = view.get("history", pd.DataFrame())
        no_hist = sorted(t for t in pos.get("ticker", []) if t not in self.data.close or not self.data.close[t].notna().any())
        history = None
        if not no_hist and not h.empty and h["value"].notna().any():
            if len(h) > 600:  # al massimo ~600 punti: settimane
                h = h.resample("W-FRI").last().dropna(how="all")
            history = {"dates": list(h.index), "value": h["value"].tolist(), "invested": h["invested"].tolist(),
                       "benchmark": h["benchmark"].tolist() if "benchmark" in h else None}
        txv = tx.sort_values(["date", "id"], ascending=False)
        transactions = [{"id": int(r["id"]), "date": r["date"], "kind": r["kind"], "kind_label": pf.KINDS.get(r["kind"], r["kind"]),
                         "ticker": r["ticker"], "quantity": r["quantity"], "price": r["price"], "currency": r["currency"],
                         "fx": r["fx"] if r["currency"] != "EUR" else None, "fees": r["fees"], "note": r["note"]}
                        for _, r in txv.iterrows()]
        div = view.get("dividends", pd.DataFrame())
        taxes = view.get("taxes") or {}
        return {**base, "empty": False, "benchmark": view["benchmark"], "summary": summary, "notes": notes,
                "positions": positions, "composition": composition, "history": history, "history_missing": no_hist,
                "transactions": transactions,
                "dividends": div.sort_values("date", ascending=False).to_dict("records") if div is not None and not div.empty else [],
                "taxes": {"unrealized_gain": taxes.get("unrealized_gain"), "tax_if_sold": taxes.get("tax_if_sold"),
                          "carry": taxes.get("carry") or [], "incomplete": bool(taxes.get("incomplete")),
                          "by_year": [{"year": str(y), "net_gain": v["net_gain"], "loss_used": v.get("loss_used", 0.0),
                                       "tax": v["tax"]} for y, v in sorted((taxes.get("by_year") or {}).items())]}
                if taxes else None}

    def add_transaction(self, ticker: str, kind: str, day, quantity: float, price: float, currency: str | None = None,
                        fx: float | None = None, fees: float | None = None, note: str | None = None,
                        reason: str | None = None) -> int:
        """Aggiunge un'operazione (acquisto, vendita, dividendo) con gli stessi controlli della pagina Streamlit:
        campi obbligatori, simbolo senza spazi, niente vendite oltre le azioni possedute (anche nelle vendite
        successive). Il perché, se c'è, va nel diario delle decisioni. ValueError con un messaggio da mostrare."""
        from modules import portfolio as pf

        ticker = (ticker or "").strip().upper()
        missing = [n for n, ok in (("simbolo", bool(ticker)), ("quantità", bool(quantity) and quantity > 0),
                                   ("prezzo", bool(price) and price > 0)) if not ok]
        if missing:
            raise ValueError("Mancano: " + ", ".join(missing) + ".")
        if " " in ticker:
            raise ValueError("Il simbolo non può contenere spazi.")
        if kind not in pf.KINDS:
            raise ValueError(f"Tipo di operazione non previsto: {kind}")
        currency = currency or guess_currency(ticker)
        if currency not in CURRENCIES:
            raise ValueError(f"Valuta non prevista: {currency}")
        day = pd.Timestamp(day).normalize()
        if day > pd.Timestamp.today().normalize():
            raise ValueError("La data non può essere nel futuro.")
        tx_all = self.db.load_transactions()
        if kind == "SELL":
            held = held_quantity(tx_all, ticker, day)
            if quantity > held + 1e-9:
                raise ValueError(f"Alla data scelta possiedi {fmt(held, 'num', digits=0)} {ticker}: non puoi venderne "
                                 f"{fmt(quantity, 'num', digits=0)}." if held > 0 else
                                 f"Alla data scelta non possiedi {ticker}: inserisci prima l'acquisto.")
            row = {"id": int(tx_all["id"].max()) + 1 if not tx_all.empty else 1, "ticker": ticker, "kind": kind,
                   "date": day, "quantity": quantity}
            bad = [b for b in pf.oversold(pd.concat([tx_all, pd.DataFrame([row])], ignore_index=True))
                   if b not in pf.oversold(tx_all)]
            if bad:
                b = bad[0]
                raise ValueError(f"Con questa vendita la vendita del {fmt(b['date'], 'date')} di "
                                 f"{fmt(b['sold'], 'num', digits=0)} {b['ticker']} supererebbe le azioni possedute "
                                 f"({fmt(b['held'], 'num', digits=0)}).")
        tx_id = self.db.add_transaction({"ticker": ticker, "kind": kind, "date": day, "quantity": float(quantity),
                                         "price": float(price), "currency": currency, "fx": fx or None,
                                         "fees": fees or 0.0, "note": (note or "").strip() or None})
        if (reason or "").strip() and kind in ("BUY", "SELL"):
            self.db.add_decision({"ticker": ticker, "date": day, "action": kind, "price": float(price),
                                  "reason": reason.strip(), "tx_id": tx_id,
                                  "review_date": day + pd.DateOffset(months=6)})
        return tx_id

    def delete_transaction(self, tx_id: int) -> None:
        self.db.delete_transaction(int(tx_id))
