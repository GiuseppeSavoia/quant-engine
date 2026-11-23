"""Portafoglio reale: posizioni, vendite, dividendi, tasse stimate e andamento contro il benchmark; esposizione vera
guardando dentro gli ETF; diario delle decisioni con il senno di poi."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from modules.services.base import Service


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
