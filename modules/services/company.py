"""Scheda azienda: bilanci (annuali, ultimi 12 mesi, trimestri), archivio completo SEC, metriche con i confronti di
settore e indice, multipli nel tempo, concorrenti, dividendi, insider e grandi investitori, confronto tra titoli."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from modules.market_data import adjust_shares_for_splits
from modules.metrics import METRIC_BY_KEY, METRICS, compute_metrics, fundamental_metrics, percentile_ranks
from modules.services.base import Service

# metriche di bilancio nell'ordine in cui le mostra la scheda azienda
FINANCIAL_ROWS = {
    "revenue": "Ricavi", "net_income": "Utile netto", "operating_cash_flow": "Cash flow operativo",
    "capex": "Investimenti (capex)", "free_cash_flow": "Free cash flow", "pretax_income": "Utile ante imposte",
    "tax_provision": "Imposte", "interest_expense": "Interessi passivi", "total_debt": "Debito totale",
    "cash": "Cassa e investimenti a breve", "equity": "Patrimonio netto", "gross_profit": "Margine lordo",
    "total_assets": "Attivo totale", "shares": "Azioni (diluite)",
    "operating_income": "Utile operativo", "depreciation": "Ammortamenti", "dividends_paid": "Dividendi pagati",
    "buybacks": "Riacquisti di azioni", "current_assets": "Attività correnti",
    "current_liabilities": "Passività correnti",
}
RATIO_ROWS = {
    "revenue_growth": "Crescita ricavi", "gross_margin": "Margine lordo %", "operating_margin": "Margine operativo",
    "net_margin": "Margine netto", "fcf_margin": "Margine FCF", "debt_to_equity": "Debito / patrimonio",
}
# multipli seguiti nel tempo nella scheda azienda (chiavi di modules.metrics)
HISTORY_MULTIPLES = ["pe", "ev_ebit", "ev_ebitda", "p_fcf", "ev_sales", "pb", "fcf_yield", "dividend_yield"]


class CompanyService(Service):
    # ---------------------------------------------- funzioni della scheda (stile terminale)
    PEER_COLUMNS = ["market_cap", "pe", "ev_ebit", "fcf_yield", "roic", "operating_margin", "revenue_growth",
                    "net_debt_ebitda", "dividend_yield", "ret_1y"]
    # per banche, assicurazioni e REIT i numeri che contano sono altri
    PEER_COLUMNS_BY_SECTOR = {
        "Financials": ["market_cap", "pe", "ptbv", "rotce", "roe", "efficiency_ratio", "nim", "loss_ratio",
                       "dividend_yield", "ret_1y"],
        "Real Estate": ["market_cap", "p_ffo", "ffo_yield", "dividend_yield", "debt_to_equity", "net_debt_ebitda",
                        "revenue_growth", "ret_1y"],
    }

    # ------------------------------------------------------------------ bilanci
    def financials(self, ticker: str, quarterly: bool = False) -> pd.DataFrame:
        """Bilanci con alcuni rapporti derivati. Righe = date di chiusura, colonne = metriche; `ttm` = True per
        le righe "ultimi 12 mesi" dai trimestrali. Le azioni sono già sulla base dei prezzi.
        - annuale: esercizi completi (ultima versione depositata), più gli ultimi 12 mesi all'ultimo trimestre
          se più recenti;
        - quarterly: gli ultimi 12 mesi a ogni trimestre (gli esercizi completi sono i trimestri di chiusura)."""
        annual = self.data.statements(ticker)
        if quarterly:
            st = self.data.fundamentals(ticker).copy()
            st["ttm"] = ~st.index.isin(annual.index)
        else:
            st = annual.assign(ttm=False)
            ttm = self.data.ttm_statements(ticker)
            if not ttm.empty and (st.empty or ttm.index[-1] > st.index[-1]):
                st = pd.concat([st, ttm.iloc[[-1]].assign(ttm=True)])
        if st.empty:
            return st
        st = st.copy()
        if "revenue" in st:
            # crescita sullo stesso periodo dell'anno prima (per i trimestri non è la riga precedente)
            full = self.data.fundamentals(ticker)
            st["revenue_growth"] = [
                row_rev / self.data.year_ago(full.loc[:day]).get("revenue", np.nan) - 1
                if day in full.index else np.nan for day, row_rev in st["revenue"].items()]
            pos = st["revenue"].where(st["revenue"] > 0)
            for col, src in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income"),
                             ("net_margin", "net_income"), ("fcf_margin", "free_cash_flow")):
                if src in st:
                    st[col] = st[src] / pos
        if "total_debt" in st and "equity" in st:
            st["debt_to_equity"] = st["total_debt"] / st["equity"].where(st["equity"] > 0)
        return st

    def financial_sources(self, ticker: str, quarterly: bool = False) -> pd.DataFrame:
        """Fonte di ogni valore di `financials` (stesse righe; colonne = voci di base): "sec", "esef", "manual",
        "yahoo_fill"... Le righe "ultimi 12 mesi" prendono la fonte dei valori TTM."""
        fin = self.ctx.financials(ticker, quarterly=quarterly)
        if fin.empty:
            return pd.DataFrame()
        annual, ttm = self.data.statement_sources(ticker), self.data.statement_sources(ticker, ttm=True)
        rows = []
        for day, is_ttm in fin["ttm"].items():
            src = ttm if is_ttm else annual
            rows.append(src.loc[day] if day in src.index else pd.Series(dtype=object))
        return pd.DataFrame(rows, index=fin.index)

    def financial_links(self, ticker: str, quarterly: bool = False) -> dict:
        """Per ogni riga di `financials`: (link, testo) del 10-K/10-Q SEC da cui vengono i valori (il deposito con la
        data più frequente tra le voci SEC della riga). Righe senza valori SEC (ESEF, Yahoo, a mano): nessun link."""
        fin = self.ctx.financials(ticker, quarterly=quarterly)
        store = self._facts_store()
        cik = self.ctx.info(ticker).get("cik")
        if fin.empty or store is None or cik is None or pd.isna(cik):
            return {}
        from modules.sec_filings import filing_url

        filings = store.filings(ticker)
        if filings.empty:
            return {}
        filings = filings.assign(day=pd.to_datetime(filings["filed"]).dt.normalize())
        filings = filings[filings["form"].astype(str).str.match(r"10-[KQ]")]
        out = {}
        for ttm in (False, True):
            src = self.data.statement_sources(ticker, ttm=ttm)
            filed = self.data.statement_sources(ticker, ttm=ttm, column="filed")
            for day in fin.index[fin["ttm"] == ttm]:
                if day not in src.index:
                    continue
                sec = filed.loc[day][src.loc[day].astype(str).str.startswith("sec")].dropna()
                if sec.empty:
                    continue
                when = pd.Timestamp(pd.to_datetime(sec).dt.normalize().mode().iloc[0])
                match = filings[filings["day"] == when]
                if match.empty:  # deposito di un altro giorno (es. emendamento): il primo entro 5 giorni dopo
                    match = filings[(filings["day"] >= when) & (filings["day"] <= when + pd.Timedelta(days=5))]
                if match.empty:
                    continue
                r = match.sort_values("form").iloc[0]  # 10-K prima di 10-K/A
                out[day] = (filing_url(cik, r["accn"]), f"{r['form']} depositato il {when:%d/%m/%Y}: apri il "
                                                         "documento alla SEC")
        return out

    def quarterly(self, ticker: str) -> pd.DataFrame:
        """Voci per singolo trimestre (ultima versione depositata), con margini, crescita sullo stesso trimestre
        dell'anno prima e debito/patrimonio. Azioni riportate alla base dei prezzi (split)."""
        q = self.db.load_quarterly([ticker])
        if q.empty:
            return pd.DataFrame()
        q = adjust_shares_for_splits(q, self.db.load_splits([ticker]))
        q = q.sort_values("filed").drop_duplicates(["period_end", "metric"], keep="last")
        st = q.pivot(index="period_end", columns="metric", values="value").sort_index()
        st.columns.name = None
        if "revenue" in st:
            # stesso trimestre dell'anno prima: chiusura tra 355 e 375 giorni prima (esercizi da 52/53 settimane)
            prev = pd.Series([st["revenue"][(st.index <= d - pd.Timedelta(days=355)) &
                                            (st.index >= d - pd.Timedelta(days=375))].iloc[-1:].sum(min_count=1)
                              for d in st.index], index=st.index)
            st["revenue_growth"] = st["revenue"] / prev.where(prev > 0) - 1
            pos = st["revenue"].where(st["revenue"] > 0)
            for col, src in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income"),
                             ("net_margin", "net_income"), ("fcf_margin", "free_cash_flow")):
                if src in st:
                    st[col] = st[src] / pos
        if "total_debt" in st and "equity" in st:
            st["debt_to_equity"] = st["total_debt"] / st["equity"].where(st["equity"] > 0)
        return st.assign(ttm=False)

    # ---------------------------------------------- archivio completo dei valori SEC
    def _facts_store(self):
        from data.db_manager import FactsStore

        path = Path(self.cfg["data"].get("facts_db_path", ""))
        return FactsStore(path) if path.name and path.exists() else None

    def sec_concepts(self, ticker: str) -> pd.DataFrame:
        """Tutte le etichette depositate da `ticker` (archivio completo): id, etichetta, unità, periodi."""
        store = self._facts_store()
        return store.concepts(ticker) if store is not None else pd.DataFrame()

    def sec_quarters(self, ticker: str, concept_ids=None) -> pd.DataFrame:
        """Valori per singolo trimestre (tutte le versioni) delle etichette indicate, o di tutte."""
        from modules.sec_quarterly import quarterly_values

        store = self._facts_store()
        if store is None:
            return pd.DataFrame()
        return quarterly_values(store.load(ticker, concept_ids))

    def sec_quarters_wide(self, ticker: str) -> pd.DataFrame:
        """Tabella da esportare: una riga per etichetta, una colonna per trimestre (ultima versione)."""
        q = self.ctx.sec_quarters(ticker)
        if q.empty:
            return q
        q = q.sort_values("filed").drop_duplicates(["taxonomy", "concept", "unit", "period_end"], keep="last")
        wide = q.pivot_table(index=["taxonomy", "concept", "unit"], columns="period_end", values="value",
                             aggfunc="last")
        wide.columns = [f"{c:%Y-%m-%d}" for c in wide.columns]
        labels = self.ctx.sec_concepts(ticker).set_index(["taxonomy", "concept", "unit"])["label"]
        wide.insert(0, "label", labels.reindex(wide.index).to_numpy())
        return wide.reset_index()

    def company_filings(self, ticker: str) -> pd.DataFrame:
        """10-K e 10-Q (dall'archivio completo) e 8-K dell'azienda, i più recenti prima, con il link alla SEC."""
        from modules.sec_filings import describe_items, filing_url, important

        cik = self.ctx.info(ticker).get("cik")
        rows = []
        store = self._facts_store()
        if store is not None:
            for r in store.filings(ticker).itertuples(index=False):
                what = "Bilancio annuale" if str(r.form).startswith("10-K") else "Bilancio trimestrale"
                rows.append((pd.Timestamp(r.filed), r.form, what, False, filing_url(cik, r.accn)
                             if pd.notna(cik) else None))
        for r in self.db.load_events([ticker]).itertuples(index=False):  # il CIK di chi ha depositato l'8-K
            rows.append((r.accepted if pd.notna(r.accepted) else r.filed, r.form, describe_items(r.items) or "8-K",
                         important(r.items), filing_url(r.cik, r.accession)))
        out = pd.DataFrame(rows, columns=["when", "form", "what", "important", "url"])
        return out.sort_values("when", ascending=False).reset_index(drop=True) if not out.empty else out

    # ---------------------------------------------- concorrenti e dividendi
    def peers(self, ticker: str, n: int = 12, min_group: int = 5) -> tuple[pd.DataFrame, str, list[str]]:
        """Le aziende più grandi dello stesso sotto-settore (se ne ha almeno `min_group`, altrimenti il settore),
        `ticker` compreso, con i numeri principali del settore; il nome del gruppo e le colonne mostrate."""
        m = self.ctx.metrics()
        info = self.ctx.info(ticker)
        comp = self.ctx.companies
        group, label = None, ""
        for col in ("sub_industry", "sector"):
            val = info.get(col)
            if isinstance(val, str) and val and col in comp:
                members = [t for t in comp.index[comp[col] == val] if t in m.index]
                if len(members) >= min_group or col == "sector":
                    group, label = members, val
                    break
        if not group:
            return pd.DataFrame(), "", []
        cols = self.PEER_COLUMNS_BY_SECTOR.get(info.get("sector"), self.PEER_COLUMNS)
        cols = [c for c in cols if c in m and m.loc[group, c].notna().any()]
        table = m.loc[group, cols].sort_values("market_cap", ascending=False)
        top = list(table.index[:n])
        if ticker in table.index and ticker not in top:
            top.append(ticker)
        out = table.loc[top].copy()
        out.insert(0, "name", [self.ctx.info(t).get("name") for t in out.index])
        out.index.name = "ticker"
        return out.reset_index(), label, cols

    def dividend_history(self, ticker: str, years: int = 12) -> pd.DataFrame:
        """Per esercizio: dividendi, riacquisti, utile, FCF, azioni, dividendo per azione, quota dell'utile
        distribuita, rendimento per gli azionisti al prezzo di fine esercizio."""
        st = self.data.statements(ticker).tail(years)
        if st.empty:
            return pd.DataFrame()
        col = lambda k: st[k] if k in st else pd.Series(np.nan, index=st.index)  # noqa: E731
        div, buy, ni, fcf, shares = (col(k) for k in ("dividends_paid", "buybacks", "net_income", "free_cash_flow",
                                                          "shares"))
        px = self.data.prices(ticker).reindex(st.index, method="ffill") if not self.data.prices(ticker).empty \
            else pd.Series(np.nan, index=st.index)
        mcap = px * shares
        out = pd.DataFrame({
            "dividends": div, "buybacks": buy, "net_income": ni, "free_cash_flow": fcf, "shares": shares,
            "dps": div / shares.where(shares > 0), "payout": div / ni.where(ni > 0),
            "fcf_payout": (div.fillna(0) + buy.fillna(0)) / fcf.where(fcf > 0),
            "dividend_yield": div / mcap.where(mcap > 0),
            "shareholder_yield": (div.fillna(0) + buy.fillna(0)) / mcap.where(mcap > 0),
        }, index=st.index)
        out.index.name = "period_end"
        return out.reset_index()

    # ------------------------------------------------------------------ insider e grandi investitori
    def insider_transactions(self, ticker: str, years: int = 3) -> pd.DataFrame:
        """Tutte le operazioni degli insider di `ticker` (anche compensi e opzioni), dalle più recenti."""
        from modules.insiders import CODES

        tx = self.db.load_insiders([ticker])
        if tx.empty:
            return tx
        tx = tx[tx["trans_date"] >= pd.Timestamp.now() - pd.DateOffset(years=years)]
        return tx.assign(kind=tx["code"].map(CODES).fillna(tx["code"])).sort_values(["filed", "trans_date"],
                                                                                     ascending=False)

    def insider_summary(self, ticker: str) -> dict:
        """Acquisti e vendite sul mercato negli ultimi 90 giorni e 12 mesi (vendite programmate a parte)."""
        tx = self.db.load_insiders([ticker], codes=["P", "S"])
        tx = tx[~tx["relationship"].fillna("").str.fullmatch("TenPercentOwner")] if not tx.empty else tx
        as_of = self.data.last_date
        out = {}
        for label, days in (("90", 90), ("365", 365)):
            w = tx[tx["filed"] > as_of - pd.Timedelta(days=days)] if not tx.empty else tx
            buys = w[w["code"].eq("P")] if not w.empty else w
            sells = w[w["code"].eq("S")] if not w.empty else w
            out[label] = {"buy_value": float(buys["value"].sum()) if not buys.empty else 0.0,
                          "buyers": int(buys["owner_cik"].nunique()) if not buys.empty else 0,
                          "sell_value": float(sells.loc[~sells["planned"], "value"].sum()) if not sells.empty else 0.0,
                          "planned_sell_value": float(sells.loc[sells["planned"], "value"].sum()) if not sells.empty else 0.0}
        return out

    def fund_positions(self) -> pd.DataFrame:
        from modules.superinvestors import fund_positions

        return fund_positions(self.data.holdings) if not self.data.holdings.empty else pd.DataFrame()

    def fund_holders(self, ticker: str) -> pd.DataFrame:
        pos = self.ctx.fund_positions()
        return pos[pos["ticker"] == ticker].sort_values("weight", ascending=False) if not pos.empty else pos

    # ------------------------------------------------------------------ metriche della scheda
    def company_metrics(self, ticker: str) -> pd.DataFrame:
        """Ogni metrica del titolo con le mediane del settore e dell'S&P 500 e la posizione (0 = peggiore,
        1 = migliore) nel settore e nell'indice. Una riga per metrica, nell'ordine del catalogo."""
        m = self.ctx.metrics()
        if ticker not in m.index:
            m = pd.concat([m, compute_metrics(self.data, [ticker])])
        sectors = self.ctx.sectors()
        sector = sectors.get(ticker)
        # confronti con le aziende dello stesso gruppo (S&P 500 con S&P 500, europee con europee)
        foreign = self.ctx.foreign
        same = set(self.ctx.universe_tickers("S&P 500")) if ticker in set(self.ctx.index_tickers) else \
            (set(foreign["ticker"]) if not foreign.empty and ticker in set(foreign["ticker"]) else set(m.index))
        peers = [t for t in m.index if sectors.get(t) == sector and t in same] if sector else []
        index_rows = [t for t in m.index if t in set(self.ctx.index_tickers)]
        r_all, r_sec = percentile_ranks(m), percentile_ranks(m, sectors)
        rows = []
        for metric in METRICS:
            k = metric.key
            rows.append({"group": metric.group, "key": k, "label": metric.label, "help": metric.help,
                         "value": m.at[ticker, k], "sector_median": m.loc[peers, k].median() if peers else np.nan,
                         "index_median": m.loc[index_rows, k].median(),
                         "rank_sector": r_sec.at[ticker, k] if k in r_sec else np.nan,
                         "rank_index": r_all.at[ticker, k] if k in r_all else np.nan})
        return pd.DataFrame(rows)

    def historical_multiples(self, ticker: str, step_days: int = 21, years: int = 10) -> pd.DataFrame:
        """Multipli del titolo nel tempo, ogni `step_days` giorni di borsa, ciascuno con i soli bilanci già
        depositati a quella data (come li si vedeva allora). Righe = date, colonne = HISTORY_MULTIPLES + price."""
        sub = self.ctx._single(ticker)
        px = sub.prices(ticker)
        if px.empty:
            return pd.DataFrame()
        px = px[px.index >= px.index[-1] - pd.DateOffset(years=years)]
        dates = list(px.index[::step_days])
        if dates[-1] != px.index[-1]:
            dates.append(px.index[-1])
        max_age = int(self.cfg.get("factors", {}).get("max_statement_age_days", 550))
        rows = []
        for day in dates:
            fm = fundamental_metrics(sub.as_of(day), ticker, float(px.loc[day]), max_age)
            rows.append({"date": day, "price": float(px.loc[day]), **{k: fm.get(k, np.nan) for k in HISTORY_MULTIPLES}})
        return pd.DataFrame(rows).set_index("date")

    def operating_margin_change(self, ticker: str) -> tuple[float, float]:
        """Margine operativo degli ultimi 12 mesi e di un anno prima."""
        st = self.data.fundamentals(ticker)
        if st.empty or "operating_income" not in st or "revenue" not in st:
            return np.nan, np.nan
        prev = self.data.year_ago(st)
        margin = lambda row: (row.get("operating_income", np.nan) / row.get("revenue", np.nan)  # noqa: E731
                              if row.get("revenue", 0) > 0 else np.nan)
        return float(margin(st.iloc[-1])), float(margin(prev)) if len(prev) else np.nan

    # ------------------------------------------------------------------ confronta
    def compare_table(self, tickers: list[str]) -> pd.DataFrame:
        """Metriche del catalogo affiancate: una riga per metrica (group, key, label, better), una colonna per
        titolo, e `best` = il titolo migliore della riga (vuoto se la metrica non ha un verso migliore)."""
        m = self.ctx.metrics()
        missing = [t for t in tickers if t not in m.index]
        if missing:
            m = pd.concat([m, compute_metrics(self.data, missing)])
        rows = []
        for metric in METRICS:
            vals = m.loc[tickers, metric.key].astype(float)
            best = None
            if metric.better and vals.notna().sum() >= 2:
                best = vals.idxmax() if metric.better == "high" else vals.idxmin()
            rows.append({"group": metric.group, "key": metric.key, "label": metric.label, "better": metric.better,
                         **vals.to_dict(), "best": best})
        return pd.DataFrame(rows)

    def compare_prices(self, tickers: list[str], years: int) -> pd.DataFrame:
        """Prezzi degli ultimi `years` anni, tutti a 1 nel primo giorno in cui c'è il prezzo di ognuno."""
        close = self.data.close.reindex(columns=tickers)
        close = close[close.index >= close.index[-1] - pd.DateOffset(years=years)].dropna(how="all")
        common = close.dropna()
        if common.empty:
            return pd.DataFrame()
        close = close.loc[common.index[0]:]
        return close / close.iloc[0]

    def compare_series(self, tickers: list[str], column: str, index_to_first: bool = False) -> pd.DataFrame:
        """Una voce dei bilanci (ultimi 12 mesi a ogni trimestre, rapporti compresi) per più titoli.
        Con `index_to_first` ogni serie parte da 100 alla prima data comune (es. per confrontare i ricavi)."""
        cols = {}
        for t in tickers:
            fin = self.ctx.financials(t, quarterly=True)
            if column in fin:
                cols[t] = fin[column]
        df = pd.DataFrame(cols).sort_index()
        df = df[df.index >= df.index.max() - pd.DateOffset(years=10)] if not df.empty else df
        if index_to_first and not df.empty:
            common = df.dropna()
            if common.empty:
                return pd.DataFrame()
            df = df.loc[common.index[0]:]
            df = df / df.iloc[0] * 100
        return df

    # ------------------------------------------------------------------ testata della scheda
    HEADER_KEYS = {"bank": ["pe", "ptbv", "rotce"], "insurer": ["pe", "ptbv", "rotce"],
                   "reit": ["p_ffo", "ffo_yield", "pb"], "general": ["pe", "ev_ebit", "fcf_yield"]}
    HEADER_LABELS = {"pe": "P/E", "ev_ebit": "EV/EBIT", "fcf_yield": "Rend. FCF", "ptbv": "P/TBV", "rotce": "ROTCE",
                     "p_ffo": "P/FFO", "ffo_yield": "Rend. FFO", "pb": "P/B"}

    def company_header(self, ticker: str, profile: str = "general") -> dict:
        """Testata della scheda (stessi numeri di ui/pages/azienda/scheda._head), valori grezzi con il loro tipo:
        identità, prezzo e variazione, data dei bilanci, capitalizzazione e tre multipli scelti per il tipo di
        azienda (`profile` del giudizio: general, bank, insurer, reit...), rendimento a un anno, minimo e massimo a 52
        settimane, fair value dell'utente e sue soglie di prezzo."""
        ctx = self.ctx
        info = ctx.info(ticker)
        cur = ctx.currency(ticker)
        group = ctx.universe_of(ticker)
        m = ctx.metrics()
        value = lambda k: (float(m.at[ticker, k]) if ticker in m.index and k in m.columns  # noqa: E731
                           and pd.notna(m.at[ticker, k]) else None)
        px = ctx.prices(ticker)
        last = change = price_date = lo = hi = None
        if not px.empty:
            close = px["close"]
            last = float(close.iloc[-1])
            change = last / float(close.iloc[-2]) - 1 if len(close) > 1 else None
            price_date = close.index[-1]
            year = px.tail(252)
            lo = float(year["low"].min() if "low" in year and year["low"].notna().any() else year["close"].min())
            hi = float(year["high"].max() if "high" in year and year["high"].notna().any() else year["close"].max())
        stmt = m.at[ticker, "statement_date"] if ticker in m.index and "statement_date" in m.columns else None
        fv = ctx.note(ticker).get("fair_value")
        fv = float(fv) if fv is not None and pd.notna(fv) else None
        keys = self.HEADER_KEYS.get(profile, self.HEADER_KEYS["general"])
        kind = lambda k: METRIC_BY_KEY[k].fmt if k in METRIC_BY_KEY else "num"  # noqa: E731
        return {
            "ticker": ticker, "name": info.get("name") or "", "sector": info.get("sector"),
            "sub_industry": info.get("sub_industry"), "universe": group, "currency": cur, "profile": profile,
            "price": last, "change": change, "price_date": price_date,
            "statement_date": stmt if stmt is not None and pd.notna(stmt) else None,
            "statement_kind": "esercizio" if group not in ("S&P 500", "Aggiunta da te") else "bilanci",
            "market_cap": {"value": value("market_cap"), "kind": "money"},
            "key_metrics": [{"key": k, "label": self.HEADER_LABELS[k], "value": value(k), "kind": kind(k)} for k in keys],
            "return_1y": {"value": value("ret_1y"), "kind": "pct"},
            "range_52w": {"low": lo, "high": hi},
            "fair_value": fv, "fair_value_margin": fv / last - 1 if fv is not None and last else None,
            "price_target": ctx.price_target(ticker), "followed": ticker in set(ctx.watchlist()),
        }

    # ------------------------------------------------------------------ concorrenti e documenti (funzioni RV e CF)
    def company_peers(self, ticker: str) -> dict:
        """Concorrenti pronti da mostrare: le aziende più grandi del gruppo (sotto-settore o settore) con i numeri
        principali, `ticker` compreso, e l'azienda contro le mediane del gruppo. {"empty": True} senza settore."""
        peers, group, cols = self.ctx.peers(ticker)
        if peers.empty:
            return {"ticker": ticker, "empty": True, "why": "Per questa azienda manca il settore."}
        median = peers[cols].median()
        own = peers.set_index("ticker")
        columns = [{"key": c, "label": METRIC_BY_KEY[c].label if c in METRIC_BY_KEY else c,
                    "fmt": METRIC_BY_KEY[c].fmt if c in METRIC_BY_KEY else "num",
                    "better": METRIC_BY_KEY[c].better if c in METRIC_BY_KEY else None} for c in cols]
        rows = [{"ticker": r["ticker"], "name": r.get("name"), "me": r["ticker"] == ticker,
                 "values": {c: r.get(c) for c in cols}} for _, r in peers.iterrows()]
        versus = [{"key": c, "own": own.at[ticker, c] if ticker in own.index else None, "median": median[c]}
                  for c in cols]
        return {"ticker": ticker, "empty": False, "group": group, "currency": self.ctx.currency(ticker),
                "columns": columns, "rows": rows, "versus": versus}

    def company_documents(self, ticker: str, news_limit: int = 10, filings_limit: int = 150) -> dict:
        """Ultime notizie che citano l'azienda (dalla copia salvata dei feed: niente rete) e documenti depositati alla
        SEC (10-K, 10-Q dall'archivio, 8-K con le voci e il segno ⚠ per quelle da guardare subito)."""
        from modules import news as news_mod

        ctx = self.ctx
        f = ctx.company_filings(ticker)
        sec_filer = ticker not in set(ctx.foreign["ticker"]) | set(ctx.custom_companies["ticker"])
        n = self.cfg.get("news") or {}
        news = pd.DataFrame(columns=news_mod.COLUMNS)
        if n.get("enabled", True):
            cache = Path(n.get("cache_dir", "data/cache/news"))
            if not cache.is_absolute():
                from modules.config import PROJECT_ROOT

                cache = PROJECT_ROOT / cache
            try:
                store = news_mod.NewsStore(cache / "notizie.json", keep_days=float(n.get("keep_days", 3)))
                news = news_mod.latest(store.frame(), [ticker], news_limit)
            except Exception:  # noqa: BLE001 - le notizie sono un di più: i documenti restano
                news = pd.DataFrame(columns=news_mod.COLUMNS)
        return {
            "ticker": ticker, "sec_filer": sec_filer, "news_enabled": bool(n.get("enabled", True)),
            "news": [{"published": r["published"], "source": r["source"], "title": r["title"], "link": r["link"]}
                     for _, r in news.iterrows()],
            "filings": [{"when": r["when"], "form": r["form"], "what": r["what"], "important": bool(r["important"]),
                         "url": r["url"]} for _, r in f.head(filings_limit).iterrows()] if not f.empty else [],
            "filings_total": int(len(f)),
            "why_no_filings": ("L'archivio dei documenti SEC si riempie con l'aggiornamento dei dati." if sec_filer
                               else "Le aziende europee e quelle aggiunte da te non depositano alla SEC."),
        }
