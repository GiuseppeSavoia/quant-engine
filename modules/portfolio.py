"""
Il portafoglio reale, inserito a mano: operazioni (acquisti, vendite, dividendi) e conti in euro.

- Costo medio ponderato (come le banche italiane): ogni acquisto ricalcola il prezzo medio di carico, comprese le
  commissioni; una vendita realizza (prezzo di vendita - costo medio) x quantità, meno le commissioni.
- Tutto in euro: cambio del giorno dell'operazione (quello che scrivi o, se manca, la chiusura di Yahoo) per il costo,
  cambio di oggi per il valore. Così il risultato comprende l'effetto del cambio, come nell'estratto del broker.
- Tasse STIMATE (non è consulenza fiscale; regime, ETF, compensazioni e date cambiano i conti): 26% sulle plusvalenze
  realizzate al netto delle minusvalenze dello stesso anno e di quelle dei 4 anni prima (riporto); dividendi USA con
  il 15% trattenuto negli USA (W-8BEN) e il 26% in Italia sul netto; sugli ETF le plusvalenze sono redditi di capitale
  e non compensano le minusvalenze (le perdite degli ETF sì, come quelle delle azioni).
- Confronto con un indice: gli stessi euro investiti nel benchmark negli stessi giorni (e tolti con le vendite).

Funzioni pure su tabelle: le operazioni stanno nel database (tabella portfolio_transactions), i prezzi e i cambi
nei prezzi giornalieri (i titoli del portafoglio e i cambi entrano nell'aggiornamento: vedi `needed_symbols`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CAPITAL_GAINS_TAX = 0.26
US_WITHHOLDING = 0.15
KINDS = {"BUY": "Acquisto", "SELL": "Vendita", "DIV": "Dividendo"}
TX_COLUMNS = ["id", "ticker", "kind", "date", "quantity", "price", "currency", "fx", "fees", "note"]


def fx_symbol(currency: str) -> str | None:
    """Simbolo Yahoo del cambio: quante unità di `currency` vale un euro (EURUSD=X)."""
    return None if not currency or currency == "EUR" else f"EUR{currency}=X"


def needed_symbols(tx: pd.DataFrame) -> list[str]:
    """Titoli e cambi da scaricare ogni giorno per il portafoglio."""
    if tx is None or tx.empty:
        return []
    fx = {fx_symbol(c) for c in tx["currency"].dropna().unique()} - {None}
    return sorted(set(tx["ticker"]) | fx)


def _rate(fx_close: pd.DataFrame, currency: str, day) -> float:
    """Unità di `currency` per un euro al giorno `day` (ultima chiusura nota), 1 per l'euro."""
    sym = fx_symbol(currency)
    if sym is None:
        return 1.0
    if fx_close is None or sym not in fx_close:
        return np.nan
    s = fx_close[sym].dropna()
    s = s[s.index <= pd.Timestamp(day)]
    return float(s.iloc[-1]) if not s.empty else np.nan


def with_eur(tx: pd.DataFrame, fx_close: pd.DataFrame) -> pd.DataFrame:
    """Operazioni con il cambio usato (scritto o di Yahoo) e gli importi in euro."""
    tx = tx.copy()
    tx["date"] = pd.to_datetime(tx["date"])
    rates = [r if pd.notna(r) and r > 0 else _rate(fx_close, c, d) for r, c, d in zip(tx["fx"], tx["currency"], tx["date"])]
    tx["fx_used"] = rates
    tx["gross"] = tx["quantity"] * tx["price"]
    tx["gross_eur"] = tx["gross"] / tx["fx_used"]
    tx["fees_eur"] = tx["fees"].fillna(0.0) / tx["fx_used"]
    return tx.sort_values(["date", "id"]).reset_index(drop=True)


def positions(tx: pd.DataFrame, fx_close: pd.DataFrame, prices: dict[str, float],
              today_fx: dict[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(posizioni aperte, vendite realizzate). Costo medio ponderato in euro, commissioni comprese.
    `prices`: ultimo prezzo per ticker (nella valuta del titolo); `today_fx`: unità per euro oggi per valuta."""
    t = with_eur(tx[tx["kind"].isin(["BUY", "SELL"])], fx_close)
    state: dict[str, dict] = {}
    realized = []
    for r in t.itertuples(index=False):
        s = state.setdefault(r.ticker, {"qty": 0.0, "cost_eur": 0.0, "cost_ccy": 0.0, "currency": r.currency,
                                        "first": r.date})
        if r.kind == "BUY":
            s["qty"] += r.quantity
            s["cost_eur"] += r.gross_eur + r.fees_eur
            s["cost_ccy"] += r.gross + (r.fees if pd.notna(r.fees) else 0.0)
        else:
            if s["qty"] <= 0:
                continue
            q = min(r.quantity, s["qty"])
            avg_eur = s["cost_eur"] / s["qty"]
            avg_ccy = s["cost_ccy"] / s["qty"]
            proceeds = q * r.price / r.fx_used - r.fees_eur
            realized.append({"ticker": r.ticker, "date": r.date, "quantity": q, "proceeds_eur": proceeds,
                             "cost_eur": avg_eur * q, "gain_eur": proceeds - avg_eur * q})
            s["qty"] -= q
            s["cost_eur"] -= avg_eur * q
            s["cost_ccy"] -= avg_ccy * q
    rows = []
    for ticker, s in state.items():
        if s["qty"] <= 1e-9:
            continue
        price = prices.get(ticker, np.nan)
        rate = (today_fx or {}).get(s["currency"], _rate(fx_close, s["currency"], pd.Timestamp.today()))
        value = s["qty"] * price / rate if np.isfinite(price) and np.isfinite(rate) else np.nan
        rows.append({"ticker": ticker, "currency": s["currency"], "quantity": s["qty"],
                     "avg_cost": s["cost_ccy"] / s["qty"], "price": price, "cost_eur": s["cost_eur"],
                     "value_eur": value, "gain_eur": value - s["cost_eur"],
                     "gain_pct": value / s["cost_eur"] - 1 if s["cost_eur"] > 0 else np.nan, "since": s["first"]})
    pos = pd.DataFrame(rows, columns=["ticker", "currency", "quantity", "avg_cost", "price", "cost_eur", "value_eur",
                                      "gain_eur", "gain_pct", "since"])
    if not pos.empty:  # pesi solo se tutti i valori sono noti: con un valore mancante il 100% di uno sarebbe falso
        pos["weight"] = (pos["value_eur"] / pos["value_eur"].sum() if pos["value_eur"].notna().all()
                         else np.nan)
    return pos, pd.DataFrame(realized, columns=["ticker", "date", "quantity", "proceeds_eur", "cost_eur", "gain_eur"])


def oversold(tx: pd.DataFrame) -> list[dict]:
    """Vendite oltre le azioni possedute in quel momento (in ordine di data e di inserimento): con una modifica o una
    vendita nel passato un acquisto può non coprire più le vendite successive. [{ticker, date, held, sold}]."""
    if tx is None or tx.empty:
        return []
    t = tx[tx["kind"].isin(["BUY", "SELL"])].copy()
    t["date"] = pd.to_datetime(t["date"])
    t = t.sort_values(["date", "id"] if "id" in t else ["date"], kind="stable")
    held: dict[str, float] = {}
    out = []
    for r in t.itertuples(index=False):
        q = float(r.quantity) if pd.notna(r.quantity) else 0.0
        h = held.get(r.ticker, 0.0)
        if r.kind == "SELL" and q > h + 1e-9:
            out.append({"ticker": r.ticker, "date": r.date, "held": h, "sold": q})
        held[r.ticker] = h + (q if r.kind == "BUY" else -min(q, h))
    return out


def missing_fx(tx: pd.DataFrame, fx_close: pd.DataFrame) -> pd.DataFrame:
    """Operazioni in valuta senza cambio (né scritto né nelle chiusure del database alla loro data): il loro importo
    in euro resta vuoto. Colonne ticker, kind, date, currency."""
    cols = ["ticker", "kind", "date", "currency"]
    if tx is None or tx.empty:
        return pd.DataFrame(columns=cols)
    t = with_eur(tx, fx_close)
    bad = t[t["fx_used"].isna() | ~np.isfinite(pd.to_numeric(t["fx_used"], errors="coerce"))]
    return bad[cols].reset_index(drop=True)


def dividends(tx: pd.DataFrame, fx_close: pd.DataFrame, us_tickers: set[str]) -> pd.DataFrame:
    """Dividendi incassati in euro: lordo, ritenuta USA (15%) per i titoli USA, 26% in Italia, netto stimato.
    Per i dividendi l'importo è quantity x price (azioni x dividendo per azione) o, con quantity 1, il totale."""
    d = tx[tx["kind"] == "DIV"]
    if d.empty:
        return pd.DataFrame(columns=["ticker", "date", "gross_eur", "us_tax", "it_tax", "net_eur"])
    d = with_eur(d, fx_close)
    us = d["ticker"].isin(us_tickers)
    us_tax = np.where(us, d["gross_eur"] * US_WITHHOLDING, 0.0)
    it_tax = (d["gross_eur"] - us_tax) * CAPITAL_GAINS_TAX
    return pd.DataFrame({"ticker": d["ticker"], "date": d["date"], "gross_eur": d["gross_eur"], "us_tax": us_tax,
                         "it_tax": it_tax, "net_eur": d["gross_eur"] - us_tax - it_tax})


LOSS_CARRY_YEARS = 4  # minusvalenze compensabili fino al quarto anno successivo a quello in cui sono realizzate


def _split_fund_gains(gains: pd.Series, is_fund: pd.Series) -> tuple[float, float]:
    """(redditi di capitale = guadagni dei fondi/ETF, redditi diversi = tutto il resto: azioni e perdite degli ETF)."""
    fund_gain = float(gains[is_fund & (gains > 0)].sum())
    other = float(gains[~(is_fund & (gains > 0))].sum())
    return fund_gain, other


def _use_losses(pool: list[list], amount: float) -> float:
    """Usa le minusvalenze riportate (le più vecchie prima) per compensare `amount` > 0; restituisce quanto usato e
    toglie dal `pool` ([[anno di origine, importo residuo], ...])."""
    used = 0.0
    for item in sorted(pool, key=lambda x: x[0]):
        if amount - used <= 0:
            break
        take = min(item[1], amount - used)
        item[1] -= take
        used += take
    pool[:] = [x for x in pool if x[1] > 1e-9]
    return used


def tax_estimate(realized: pd.DataFrame, pos: pd.DataFrame, today=None, funds: set[str] | None = None) -> dict:
    """Stima (non è consulenza fiscale): imposta sulle plusvalenze realizzate anno per anno e quella in PIÙ che si
    pagherebbe vendendo oggi tutte le posizioni.

    - Redditi diversi (guadagni e perdite delle azioni, perdite dei fondi/ETF): si compensano nell'anno; un saldo
      positivo si riduce con le minusvalenze riportate degli anni prima (le più vecchie prima, entro
      `LOSS_CARRY_YEARS` anni: quelle del 2022 valgono fino al 2026), un saldo negativo diventa una minusvalenza da
      riportare.
    - Redditi di capitale (guadagni dei fondi/ETF in `funds`): tassati per intero, non compensano nulla e non sono
      compensati da nulla.
    Vendendo oggi, guadagni e perdite delle posizioni si sommano al realizzato dell'anno e alle minusvalenze ancora
    utilizzabili. Prima (collaudo 2026-11-16) si sommavano solo le posizioni in guadagno e non c'era il riporto.
    `incomplete` = qualche posizione senza guadagno noto (cambio o prezzo mancante): stima su quelle note.
    `carry` = minusvalenze ancora utilizzabili dopo il realizzato di quest'anno: [{year, amount, expires}]."""
    funds = set(funds or ())
    year_now = int(pd.Timestamp(today or pd.Timestamp.today()).year)
    by_year: dict[int, dict] = {}
    pool: list[list] = []  # [anno di origine, minusvalenza residua]
    done, start_pool = 0.0, 0.0  # redditi diversi realizzati quest'anno, minusvalenze utilizzabili a inizio anno
    years = sorted(set(realized["date"].dt.year.astype(int))) if not realized.empty else []
    for year in sorted(set(years) | {year_now}):
        if year > year_now:
            break
        pool[:] = [x for x in pool if x[0] >= year - LOSS_CARRY_YEARS]  # scadute
        if year == year_now:
            start_pool = sum(x[1] for x in pool)
        if year not in years:
            continue
        g = realized[realized["date"].dt.year == year]
        gains = pd.to_numeric(g["gain_eur"], errors="coerce").fillna(0.0)
        fund_gain, other = _split_fund_gains(gains, g["ticker"].isin(funds))
        used = _use_losses(pool, other) if other > 0 else 0.0
        if other < 0:
            pool.append([year, -other])
        if year == year_now:
            done = other
        by_year[year] = {"net_gain": float(gains.sum()), "fund_gain": fund_gain, "other_net": other,
                         "loss_used": used, "taxable": max(other, 0.0) - used + fund_gain,
                         "tax": (max(other, 0.0) - used + fund_gain) * CAPITAL_GAINS_TAX,
                         "loss_carry": -other if other < 0 else 0.0}
    gains = pd.to_numeric(pos["gain_eur"], errors="coerce") if not pos.empty else pd.Series(dtype=float)
    is_fund = pos["ticker"].isin(funds) if not pos.empty and "ticker" in pos else pd.Series(False, index=gains.index)
    fund_gain, other = _split_fund_gains(gains, is_fund)

    def taxed(diversi: float) -> float:  # imposta sui redditi diversi dell'anno con le minusvalenze di inizio anno
        return max(diversi - start_pool, 0.0) * CAPITAL_GAINS_TAX

    tax_now = taxed(done + other) - taxed(done)
    return {"by_year": by_year, "unrealized_gain": fund_gain + other,
            # negativa = credito: vendere in perdita riduce l'imposta sul realizzato dell'anno
            "tax_if_sold": tax_now + fund_gain * CAPITAL_GAINS_TAX,
            "loss_carry_start": start_pool,
            "carry": [{"year": int(y), "amount": float(a), "expires": int(y) + LOSS_CARRY_YEARS}
                      for y, a in sorted(pool)],
            "incomplete": bool(gains.isna().any())}


def value_history(tx: pd.DataFrame, close: pd.DataFrame, fx_close: pd.DataFrame,
                  benchmark: str | None = None, bench_currency: str = "USD") -> pd.DataFrame:
    """Valore giornaliero in euro del portafoglio, soldi investiti netti e, con `benchmark`, il valore se gli stessi
    euro fossero andati nel benchmark (stesse date di acquisto e vendita, convertiti al cambio di quel giorno)."""
    t = with_eur(tx[tx["kind"].isin(["BUY", "SELL"])], fx_close)
    if t.empty:
        return pd.DataFrame()
    days = close.index[close.index >= t["date"].min()]
    out = pd.DataFrame(index=days)
    qty = pd.DataFrame(0.0, index=days, columns=sorted(t["ticker"].unique()))
    invested = pd.Series(0.0, index=days)
    bench_units = pd.Series(0.0, index=days)
    bench = close[benchmark].reindex(days).ffill() if benchmark and benchmark in close else None
    bsym = fx_symbol(bench_currency)
    bench_fx = (fx_close[bsym].reindex(days).ffill() if bsym and fx_close is not None and bsym in fx_close
                else pd.Series(1.0, index=days))
    for r in t.itertuples(index=False):
        sign = 1 if r.kind == "BUY" else -1
        qty.loc[qty.index >= r.date, r.ticker] += sign * r.quantity
        amount = sign * (r.gross_eur + (r.fees_eur if r.kind == "BUY" else -r.fees_eur))
        invested[invested.index >= r.date] += amount
        if bench is not None:
            b = bench[bench.index >= r.date]
            rate = bench_fx[bench_fx.index >= r.date]
            if not b.empty and b.iloc[0] > 0 and not rate.empty:
                # euro -> valuta del benchmark al cambio del giorno, poi quote del benchmark
                bench_units[bench_units.index >= r.date] += amount * rate.iloc[0] / b.iloc[0]
    # un titolo appena inserito può non avere ancora prezzi nel database: colonna vuota invece di un errore
    px = close.reindex(index=days, columns=qty.columns).ffill()
    cur = t.drop_duplicates("ticker").set_index("ticker")["currency"]
    rates = pd.DataFrame({tk: (fx_close[fx_symbol(c)].reindex(days).ffill() if fx_symbol(c) and fx_symbol(c) in fx_close
                               else pd.Series(1.0, index=days)) for tk, c in cur.items()})
    out["value"] = (qty * px / rates[qty.columns]).sum(axis=1, min_count=1)
    out["invested"] = invested
    if bench is not None:
        out["benchmark"] = bench_units * bench / bench_fx  # di nuovo in euro al cambio di ogni giorno
    return out
