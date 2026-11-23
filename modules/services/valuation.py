"""Valutazione: costo del capitale (Damodaran: beta di settore, premio implicito, rating sintetico), valutazione a
leve con la plausibilità contro il settore, diario delle valutazioni, DCF regolabile dell'utente, DCF e punteggi
della strategia, storia del fair value."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.services.base import Service


class ValuationService(Service):
    # ------------------------------------------------------------------ costo del capitale (Damodaran)
    def _capital_structure(self, ticker: str, leases: bool = True) -> dict:
        st = self.data.fundamentals(ticker)
        last = st.ffill().iloc[-1] if not st.empty else pd.Series(dtype=float)
        g = lambda k: float(np.nan_to_num(last.get(k, np.nan)))  # noqa: E731
        lease = g("operating_leases") if leases else 0.0
        return {"debt": g("total_debt") + lease, "lease": lease, "cash": g("cash")}

    def industry_table(self) -> pd.DataFrame:
        """Beta di regressione, struttura del capitale e beta di settore di tutte le aziende (calcolato una volta)."""
        ctx = self.ctx
        if ctx._industry is None:
            from modules.cost_of_capital import industry_betas, regression_betas

            v = self.cfg["valuation"]
            m = ctx.metrics()
            betas = regression_betas(self.data.close[[c for c in self.data.close.columns
                                                      if c in m.index or c == self.data.benchmark]],
                                     self.data.benchmark, int(v.get("beta_years", 2)))
            comp = ctx.companies.reindex(m.index)
            rows = []
            for t in m.index:
                cs = self._capital_structure(t)
                rows.append({"ticker": t, "beta": betas.get(t, np.nan), "equity": m.at[t, "market_cap"], **cs,
                             "sector": comp.at[t, "sector"] if "sector" in comp else None,
                             "sub_industry": comp.at[t, "sub_industry"] if "sub_industry" in comp else None})
            table = pd.DataFrame(rows).set_index("ticker")
            table["beta_benchmark"] = self.data.benchmark
            tax, min_peers = float(v.get("marginal_tax_rate", 0.25)), int(v.get("min_industry_peers", 5))
            euro = self._european_betas(table, int(v.get("beta_years", 2)))
            foreign = ctx.foreign
            if not euro.empty:  # europee: beta contro l'indice europeo, non contro l'SPY
                table.loc[euro.index, "beta"] = euro
                table.loc[euro.index, "beta_benchmark"] = self.cfg["europe"]["benchmark"]
            if not foreign.empty:  # settori ICB delle europee con i nomi GICS, solo per i gruppi
                from modules.cost_of_capital import ICB_TO_GICS

                eu_rows = table.index.isin(list(foreign["ticker"]))
                table.loc[eu_rows, "sector"] = table.loc[eu_rows, "sector"].map(lambda x: ICB_TO_GICS.get(x, x))
            ok = table.dropna(subset=["beta", "equity"])
            ok = ok[ok["equity"] > 0]
            # mediane solo sull'S&P 500: il beta delle europee contro l'SPY (altro mercato, chiusure sfasate) esce
            # basso e abbasserebbe il costo dell'equity dei settori USA
            out = table.join(industry_betas(ok, tax, min_peers, reference=set(ctx.index_tickers)))
            if not euro.empty:
                # europee: settori ICB tradotti in GICS (solo qui) e mediana del gruppo europeo (contro l'Euro Stoxx 50)
                # se ha almeno `min_peers` aziende; per le finanziarie niente correzione per la cassa (la "cassa" ESEF
                # delle banche comprende i depositi in banca centrale e supera equity + debito -> beta negativo); negli
                # altri gruppi la correzione deve restare sensata (cassa sotto metà di equity + debito). Altrimenti
                # resta la mediana USA del settore (ora con lo stesso nome GICS)
                eu = industry_betas(ok, tax, min_peers, reference=set(euro.index), no_cash_groups={"Financials"})
                eu = eu[eu.index.isin(euro.index) & (eu["n"] >= min_peers) & (eu["beta_u_ops"] > 0)
                        & (eu["beta_u_ops"] <= 2 * eu["beta_u"])]
                out.loc[eu.index, ["n", "beta_u", "beta_u_ops"]] = eu[["n", "beta_u", "beta_u_ops"]]
                out.loc[eu.index, "group"] = "Europa · " + eu["group"].astype(str)
            ctx._industry = out
        return ctx._industry

    def _european_betas(self, table: pd.DataFrame, years: int) -> pd.Series:
        """Beta di regressione delle aziende europee contro `europe.benchmark` (Euro Stoxx 50), dai prezzi salvati.
        Vuota se l'indice non è nel database (allora restano le mediane USA del settore)."""
        from modules.cost_of_capital import regression_betas

        bench = (self.cfg.get("europe") or {}).get("benchmark")
        foreign = self.ctx.foreign
        euro = [t for t in (foreign["ticker"] if not foreign.empty else []) if t in table.index
                and t in self.data.close.columns]
        if not bench or not euro:
            return pd.Series(dtype=float)
        try:
            px = self.db.load_prices([bench])
        except Exception as exc:  # noqa: BLE001 - senza l'indice europeo restano le mediane USA, non si blocca
            import logging
            logging.getLogger(__name__).warning("Beta europee non calcolate (%s): mediane USA del settore", exc)
            return pd.Series(dtype=float)
        if px.empty:
            return pd.Series(dtype=float)
        idx = px.assign(date=pd.to_datetime(px["date"])).set_index("date")["close"].rename(bench).sort_index()
        close = self.data.close[euro].join(idx, how="outer")
        close = close[close.index <= self.data.last_date] if self.data.last_date is not None else close
        return regression_betas(close, bench, years).dropna()

    def implied_erp(self) -> dict:
        """Premio per il rischio implicito dell'S&P 500 oggi, dai dividendi e riacquisti degli ultimi 12 mesi."""
        ctx = self.ctx
        if ctx._erp is None:
            from modules.cost_of_capital import implied_erp

            v = self.cfg["valuation"]
            m = ctx.metrics().loc[[t for t in ctx.index_tickers if t in ctx.metrics().index]]
            ok = m.dropna(subset=["shareholder_yield", "market_cap"])
            cash_yield = float((ok["shareholder_yield"] * ok["market_cap"]).sum() / ok["market_cap"].sum()) \
                if not ok.empty else np.nan
            rf = self.data.risk_free(v["risk_free_fallback"])
            erp = implied_erp(cash_yield, rf, float(v.get("erp_growth", 0.05)))
            ctx._erp = {"erp": erp if np.isfinite(erp) else v["equity_risk_premium"], "computed": bool(np.isfinite(erp)),
                        "cash_yield": cash_yield, "coverage": len(ok) / max(len(m), 1), "risk_free": rf,
                        "growth": float(v.get("erp_growth", 0.05)), "fallback": v["equity_risk_premium"]}
        return ctx._erp

    def cost_of_capital(self, ticker: str, leases: bool = True) -> dict:
        """Il costo del capitale di `ticker` passo per passo (vedi modules/cost_of_capital.py)."""
        from modules.cost_of_capital import relevered, synthetic_rating, wacc

        ctx = self.ctx
        v = self.cfg["valuation"]
        tax = float(v.get("marginal_tax_rate", 0.25))
        ind = ctx.industry_table()
        erp = ctx.implied_erp()
        rf = erp["risk_free"]
        cs = self._capital_structure(ticker, leases)
        equity = float(ctx.metrics().at[ticker, "market_cap"]) if ticker in ctx.metrics().index else np.nan
        row = ind.loc[ticker] if ticker in ind.index else pd.Series(dtype=float)
        beta_u = row.get("beta_u_ops", np.nan)
        if not np.isfinite(beta_u):  # gruppo ignoto: mediana di tutti
            beta_u = float(ind["beta_u_ops"].median())
        beta = relevered(beta_u, cs["debt"], equity, tax) if np.isfinite(equity) else beta_u
        st = self.data.fundamentals(ticker)
        cur = st.iloc[-1] if not st.empty else pd.Series(dtype=float)
        ebit, interest = float(cur.get("operating_income", np.nan)), abs(float(cur.get("interest_expense", np.nan)))
        if leases and cs["lease"] > 0:  # con il leasing come debito, anche i suoi interessi impliciti
            interest = np.nan_to_num(interest) + cs["lease"] * (rf + 0.02)
        coverage = ebit / interest if np.isfinite(ebit) and np.isfinite(interest) and interest > 0 else \
            (np.inf if np.isfinite(ebit) and ebit > 0 and cs["debt"] <= 0 else np.nan)
        rating, spread = synthetic_rating(coverage, v.get("synthetic_ratings"))
        w = wacc(rf, beta, erp["erp"], spread, cs["debt"], equity, tax)
        mature = wacc(rf, 1.0, erp["erp"], spread, cs["debt"], equity, tax)["wacc"]
        return {"risk_free": rf, "erp": erp["erp"], "erp_computed": erp["computed"], "group": row.get("group"),
                "peers": int(row["n"]) if pd.notna(row.get("n", np.nan)) else 0,
                "beta_regression": row.get("beta", np.nan), "beta_benchmark": row.get("beta_benchmark"),
                "beta_unlevered": beta_u,
                "beta": beta, "debt": cs["debt"], "lease": cs["lease"], "equity": equity, "coverage": coverage,
                "rating": rating, "spread": spread, "marginal_tax": tax, "mature_wacc": mature, **w}

    # ------------------------------------------------------------------ valutazione a leve (Damodaran)
    def research_asset(self, ticker: str, current_rnd: float, last_end) -> dict | None:
        """R&S come investimento (Damodaran): la spesa degli ultimi `vita` anni, ammortizzata in linea retta.
        Attività = quota non ancora ammortizzata; ammortamento dell'anno = 1/vita della spesa di ogni anno passato."""
        v = self.cfg["valuation"]
        lives = v.get("rnd_life_years") or {"default": 5}
        life = int(lives.get(self.ctx.info(ticker).get("sector"), lives.get("default", 5)))
        annual = self.data.statements(ticker)
        past = annual["rnd"].dropna() if "rnd" in annual else pd.Series(dtype=float)
        past = past[past.index <= pd.Timestamp(last_end) - pd.Timedelta(days=300)].sort_index(ascending=False)
        past = list(past.iloc[:life])
        if len(past) < 2:
            return None
        asset = current_rnd + sum(x * (life - i) / life for i, x in enumerate(past[:life - 1], start=1))
        amortization = sum(x / life for x in past)
        return {"life": life, "asset": asset, "amortization": amortization, "years": len(past)}

    def valuation_setup(self, ticker: str, use_saved: bool = True, rnd: bool = True,
                        leases: bool = True) -> dict | None:
        """Punto di partenza della valutazione: dati di oggi (Base), scenari proposti e probabilità. Se c'è una
        valutazione nel diario si riparte dalle sue ipotesi. Costo del capitale alla Damodaran; con `rnd` la R&S è
        un investimento, con `leases` i leasing operativi sono debito. None se mancano ricavi o azioni."""
        from modules.valuation_model import Base, Levers, default_scenarios

        st = self.data.fundamentals(ticker)
        if st.empty or "revenue" not in st:
            return None
        cur, latest = st.iloc[-1], st.ffill().iloc[-1]
        g = lambda row, k: float(row.get(k, np.nan))  # noqa: E731
        revenue, shares = g(cur, "revenue"), self.data.last_shares(st)
        px = self.data.prices(ticker)
        if not (np.isfinite(revenue) and revenue > 0 and np.isfinite(shares) and shares > 0) or px.empty:
            return None
        notes = []
        ebit = g(cur, "operating_income")
        if not np.isfinite(ebit):  # senza utile operativo: utile ante imposte + interessi
            ebit = g(cur, "pretax_income") + abs(np.nan_to_num(g(cur, "interest_expense")))
            notes.append("utile operativo non dichiarato: stimato come utile ante imposte + interessi")
        debt, cash, equity = (np.nan_to_num(g(latest, k)) for k in ("total_debt", "cash", "equity"))
        book_capital = equity + debt - cash  # capitale investito contabile, prima delle correzioni
        v = self.cfg["valuation"]
        coc = self.ctx.cost_of_capital(ticker, leases)
        rf = coc["risk_free"]
        adjustments, extra_capital = [], 0.0
        if leases and coc["lease"] > 0 and np.isfinite(ebit):
            debt += coc["lease"]
            extra_capital += coc["lease"]
            lease_interest = coc["lease"] * coc["cost_of_debt"]
            ebit += lease_interest
            adjustments.append(f"leasing operativi come debito: +{coc['lease'] / 1e9:,.2f} mld di debito e di "
                               f"capitale investito; utile operativo +{lease_interest / 1e9:,.2f} mld (la quota "
                               "interessi della rata, che non è un costo operativo)")
        current_rnd = g(cur, "rnd")
        if rnd and np.isfinite(current_rnd) and np.isfinite(ebit) and current_rnd / revenue >= v.get("rnd_min_intensity", 0.03):
            ra = self.ctx.research_asset(ticker, current_rnd, st.index[-1])
            if ra:
                ebit += current_rnd - ra["amortization"]
                extra_capital += ra["asset"]
                adjustments.append(f"R&S come investimento (vita {ra['life']} anni, {ra['years']} esercizi di storia): "
                                   f"utile operativo {(current_rnd - ra['amortization']) / 1e9:+,.2f} mld, capitale "
                                   f"investito +{ra['asset'] / 1e9:,.2f} mld")
        margin = ebit / revenue if np.isfinite(ebit) else 0.10
        tax = g(cur, "tax_provision") / g(cur, "pretax_income") if g(cur, "pretax_income") > 0 else np.nan
        tax = float(np.clip(tax, 0, 0.35)) if np.isfinite(tax) else v["default_tax_rate"]
        invested = book_capital + extra_capital
        if not np.isfinite(invested) or invested < 0.1 * revenue:  # patrimonio piccolo o negativo (riacquisti)
            invested = revenue / 1.5
            notes.append("capitale investito contabile troppo piccolo (riacquisti di azioni): stimato dai ricavi")
        s2c = float(np.clip(revenue / invested, 0.2, 5.0))
        m = self.ctx.metrics()
        growths = [m.at[ticker, k] for k in ("revenue_growth", "revenue_cagr_3y") if ticker in m.index]
        growths = [x for x in growths if np.isfinite(x)]
        growth = float(np.clip(np.mean(growths), -0.05, 0.30)) if growths else 0.05
        wacc = coc["wacc"] if np.isfinite(coc["wacc"]) else rf + coc["erp"]
        base = Base(revenue=revenue, margin=float(margin), invested_capital=float(invested), cash=cash, debt=debt,
                    shares=shares, price=float(px.iloc[-1]), risk_free=rf)
        scenarios = default_scenarios(base, growth, float(wacc), tax, v["terminal_growth"], s2c, coc["mature_wacc"])
        probabilities = {"Pessimista": 0.25, "Base": 0.5, "Ottimista": 0.25}
        diary = self.db.load_valuations(ticker) if use_saved else pd.DataFrame()
        saved = not diary.empty
        if saved:  # si riparte dall'ultima valutazione (le leve; il punto di partenza è quello di oggi)
            last = diary.iloc[-1]
            scenarios = {n: Levers(**lv) for n, lv in last["scenarios"].items()} or scenarios
            probabilities = last["probabilities"] or probabilities
        return {"base": base, "scenarios": scenarios, "probabilities": probabilities, "notes": notes,
                "saved": saved, "system_growth": growth, "system_wacc": float(wacc), "cost_of_capital": coc,
                "adjustments": adjustments}

    def sector_distribution(self, ticker: str) -> dict[str, pd.Series]:
        """Valori delle aziende dello stesso settore per le leve confrontabili, più la storia dell'azienda."""
        sectors = self.ctx.sectors()
        sector = sectors.get(ticker)
        m = self.ctx.metrics()
        peers = [t for t in m.index if sectors.get(t) == sector and t != ticker]
        s2c = {}
        for t in peers:
            f = self.data.fundamentals(t)
            if f.empty:
                continue
            last = f.ffill().iloc[-1]
            ic = np.nan_to_num(last.get("equity", np.nan)) + np.nan_to_num(last.get("total_debt", np.nan)) \
                - np.nan_to_num(last.get("cash", np.nan))
            rev = last.get("revenue", np.nan)
            if ic > 0 and np.isfinite(rev) and rev > 0:
                s2c[t] = rev / ic
        annual = self.data.statements(ticker)
        own_margin = (annual["operating_income"] / annual["revenue"].where(annual["revenue"] > 0)).dropna() \
            if {"operating_income", "revenue"} <= set(annual.columns) else pd.Series(dtype=float)
        return {"growth": m.loc[peers, "revenue_cagr_3y"].dropna(), "target_margin": m.loc[peers, "operating_margin"].dropna(),
                "sales_to_capital": pd.Series(s2c, dtype=float), "terminal_roic": m.loc[peers, "roic"].dropna(),
                "own_margin": own_margin}

    def plausibility(self, ticker: str, levers) -> pd.DataFrame:
        """Ogni leva confrontata con il settore: percentili 10/50/90, posizione e giudizio (possibile, plausibile,
        probabile nel senso di Damodaran: qui solo quanto è comune tra le aziende simili)."""
        dist = self.ctx.sector_distribution(ticker)
        rows = []
        for key, label in (("growth", "Crescita dei ricavi (vs crescita annua a 3 anni del settore)"),
                           ("target_margin", "Margine operativo obiettivo"),
                           ("sales_to_capital", "Ricavi per unità di capitale investito"),
                           ("terminal_roic", "ROIC nella fase stabile (vs ROIC di oggi del settore)")):
            values = dist[key]
            x = float(getattr(levers, key))
            if len(values) < 5:
                rows.append({"lever": label, "value": x, "p10": np.nan, "p50": np.nan, "p90": np.nan,
                             "percentile": np.nan, "verdict": "settore troppo piccolo per un confronto"})
                continue
            pct = float((values < x).mean())
            verdict = ("comune nel settore" if 0.1 <= pct <= 0.9 else
                       f"raro: solo il {1 - pct:.0%} delle aziende del settore fa di più" if pct > 0.9 else
                       f"raro: solo il {pct:.0%} delle aziende del settore fa di meno")
            rows.append({"lever": label, "value": x, "p10": values.quantile(0.1), "p50": values.median(),
                         "p90": values.quantile(0.9), "percentile": pct, "verdict": verdict})
        own = dist["own_margin"]
        if len(own) >= 3 and levers.target_margin > own.max():
            rows.append({"lever": "Margine obiettivo vs storia dell'azienda", "value": float(levers.target_margin),
                         "p10": own.min(), "p50": own.median(), "p90": own.max(), "percentile": 1.0,
                         "verdict": f"sopra il massimo degli ultimi {len(own)} esercizi ({own.max():.1%})"})
        return pd.DataFrame(rows)

    def save_valuation(self, ticker: str, setup: dict, scenarios: dict, probabilities: dict, story: str = "",
                       note: str = "", simulation: dict | None = None) -> dict:
        """Aggiunge una valutazione al diario e aggiorna il tuo fair value (home, avvisi, Esplora)."""
        from modules.valuation_model import scenario_values

        res = scenario_values(setup["base"], scenarios, probabilities)
        entry = {"ticker": ticker, "data_as_of": str(pd.Timestamp(self.data.last_date).date()),
                 "price": setup["base"].price, "expected_value": res["expected"], "per_scenario": res["per_scenario"],
                 "probabilities": probabilities, "scenarios": {n: lv.to_dict() for n, lv in scenarios.items()},
                 "simulation": {k: v for k, v in (simulation or {}).items() if k in ("p10", "p50", "p90",
                                                                                     "prob_above_price")},
                 "story": story, "note": note}
        entry["id"] = self.db.save_valuation(entry)
        if np.isfinite(res["expected"]):
            self.db.save_note(ticker, fair_value=float(res["expected"]))
        return entry

    def valuation_diary(self, ticker: str | None = None) -> pd.DataFrame:
        """Il diario con il senno di poi: prezzo di allora, tuo valore, margine allora, rendimento da allora e se
        il prezzo si è mosso verso il tuo valore."""
        diary = self.db.load_valuations(ticker)
        if diary.empty:
            return diary
        now = diary["ticker"].map(lambda t: float(self.data.prices(t).iloc[-1]) if not self.data.prices(t).empty else np.nan)
        diary["price_now"] = now
        diary["margin_then"] = diary["expected_value"] / diary["price"] - 1
        diary["return_since"] = now / diary["price"] - 1
        diary["toward_value"] = np.sign(diary["return_since"]) == np.sign(diary["margin_then"])
        return diary

    # ------------------------------------------------------------------ DCF dell'utente
    def dcf_inputs(self, ticker: str) -> dict:
        """Ipotesi di partenza del DCF regolabile: quelle salvate dall'utente se ci sono, altrimenti quelle del
        sistema. `system` contiene sempre quelle del sistema (per il pulsante "ripristina")."""
        val = self.ctx.fundamental.value(ticker, self.data)
        v = self.cfg["valuation"]
        system = {"fcf": val.fcf_base, "growth": val.growth if val.growth is not None else v["default_growth"],
                  "wacc": val.wacc, "terminal_growth": v["terminal_growth"], "years": int(v["projection_years"])}
        saved = (self.ctx.note(ticker).get("assumptions") or {})
        return {"system": system, "current": {**system, **{k: x for k, x in saved.items() if k in system}},
                "price": val.price, "net_debt": val.net_debt, "shares": val.shares, "saved": bool(saved)}

    def user_dcf(self, ticker: str, fcf: float, growth: float, wacc: float, terminal_growth: float, years: int,
                 net_debt: float, shares: float, price: float) -> dict:
        """Fair value con le ipotesi dell'utente, crescita implicita nel prezzo (stesse ipotesi) e una griglia
        di sensibilità (WACC x crescita)."""
        fa = self.ctx.fundamental
        out = {"fair_value": None, "margin": None, "implied_growth": None, "error": None, "grid": pd.DataFrame()}
        try:
            fair = fa.dcf_per_share(fcf, growth, wacc, net_debt, shares, terminal_growth, years)
        except ValueError as exc:
            out["error"] = str(exc)
            return out
        out.update(fair_value=fair, margin=fair / price - 1 if price and price > 0 else None,
                   implied_growth=fa.implied_growth(price, fcf, wacc, net_debt, shares,
                                                    terminal_growth=terminal_growth, years=years))
        waccs = [wacc + d for d in (-0.02, -0.01, 0.0, 0.01, 0.02)]
        growths = [growth + d for d in (-0.04, -0.02, 0.0, 0.02, 0.04)]
        grid = {}
        for g in growths:
            col = {}
            for w in waccs:
                try:
                    col[w] = fa.dcf_per_share(fcf, g, w, net_debt, shares, terminal_growth, years)
                except ValueError:
                    col[w] = np.nan
            grid[g] = col
        out["grid"] = pd.DataFrame(grid)  # righe = WACC, colonne = crescita
        return out

    # ------------------------------------------------------------------ DCF della strategia
    def valuation(self, ticker: str) -> dict:
        """DCF e punteggi multi-factor calcolati ora, sugli stessi dati e con gli stessi moduli della strategia."""
        snap = self.data
        val = self.ctx.fundamental.value(ticker, snap)
        fs = self.ctx.multi_factor.score(ticker, snap)
        sector = self.ctx.info(ticker).get("sector")
        skipped = sector in set(self.cfg["valuation"].get("skip_sectors", []))
        return {"valuation": val, "factors": fs, "dcf_skipped_sector": skipped}

    def fair_value_history(self, ticker: str, step_days: int = 21, warmup_days: int = 252) -> pd.DataFrame:
        """DCF ricalcolato ogni `step_days` giorni di borsa con i soli dati disponibili a quella data
        (prezzi passati e bilanci già depositati): lo stesso calcolo che la strategia faceva allora."""
        sub = self.ctx._single(ticker)
        px = sub.prices(ticker)
        if len(px) <= warmup_days:
            return pd.DataFrame()
        dates = list(px.index[warmup_days::step_days])
        if dates[-1] != px.index[-1]:
            dates.append(px.index[-1])
        rows = []
        for day in dates:
            v = self.ctx.fundamental.value(ticker, sub.as_of(day))
            rows.append({"date": day, "price": v.price, "fair_value": v.fair_value, "margin": v.margin,
                         "growth": v.growth, "implied_growth": v.implied_growth, "wacc": v.wacc,
                         "note": v.notes[-1] if v.fair_value is None and v.notes else None})
        return pd.DataFrame(rows).set_index("date")
