"""
Bilanci di Yahoo come RISERVA, solo dove le fonti primarie (SEC, ESEF, dati a mano) non arrivano:
- aziende senza bilanci primari (le tedesche, che non depositano nell'archivio ESEF; società nate da una scissione
  che hanno solo 10-Q) o con bilanci primari fermi (esercizi più recenti assenti dall'archivio);
- voci mancanti in un esercizio primario (es. utile operativo di GE, interessi di LLY), solo per quelle voci;
- l'ultimo "ultimi 12 mesi" della SEC rimasto a metà perché manca il 10-K di base (CLX, FERG), solo le voci vuote.

Regole:
- fonte propria (`yahoo_fill`, `yahoo_fill_ttm`): si riconosce, si toglie con un DELETE, e in `MarketData` perde SEMPRE
  contro le fonti primarie per lo stesso periodo e voce, anche se depositata dopo;
- `filed` = il giorno in cui il valore è stato scaricato la prima volta (onesto per il backtest: prima non lo avevi);
  resta lo stesso finché il valore non cambia;
- solo voci la cui corrispondenza con i dati primari è stata misurata (`METRICS`, vedi docs/storia_decisioni.md);
- un esercizio che non esiste nelle fonti primarie si scrive intero (mai poche voci isolate: diventerebbe "l'ultimo
  bilancio" con quasi tutto vuoto); attività e passività correnti solo in coppia;
- controllo per azienda: se dove ci sono entrambe le fonti ricavi, utile, attivo o patrimonio di Yahoo differiscono
  molto da quelli primari (unità o valuta diverse, altra società), l'azienda si salta;
- valuta del bilancio convertita in quella del prezzo con il cambio di fine periodo (come per ESEF);
- azioni mai per i titoli con regole proprie (`sec.share_classes`, esclusioni volute come V e ARES).
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from modules import data_audit

logger = logging.getLogger(__name__)

SOURCE, SOURCE_TTM = "yahoo_fill", "yahoo_fill_ttm"
SOURCES = (SOURCE, SOURCE_TTM)
PERIOD_TOLERANCE_DAYS = 10
# voci che si possono prendere da Yahoo (misurate contro SEC/ESEF il 2026-11-22: vedi docs/storia_decisioni.md)
METRICS = [
    "revenue", "gross_profit", "operating_income", "depreciation", "interest_expense", "pretax_income",
    "tax_provision", "net_income", "operating_cash_flow", "capex", "free_cash_flow", "dividends_paid", "buybacks",
    "stock_compensation", "total_assets", "current_assets", "current_liabilities", "total_liabilities", "equity",
    "retained_earnings", "cash", "total_debt", "goodwill", "receivables", "shares", "rnd", "sga",
    "net_interest_income",
]
# banche e finanziarie estere: niente debito, flusso libero, voci correnti e utile lordo (come per ESEF: non hanno senso)
# in un esercizio ESEF si aggiungono solo voci che concordano con ESEF almeno all'80% (misura del 2026-11-22): le altre
# (utile operativo 65%, interessi 42%, debito e cassa 62%, crediti 68%, capex 79%) hanno definizioni IFRS diverse e un
# rapporto tra due fonti (copertura degli interessi, debito/EBITDA) sarebbe un numero sbagliato. In un esercizio
# scritto intero da Yahoo invece valgono tutte (una sola fonte, definizioni coerenti tra loro)
ESEF_GAP_OK = {"revenue", "gross_profit", "depreciation", "pretax_income", "tax_provision", "net_income",
               "operating_cash_flow", "free_cash_flow", "dividends_paid", "buybacks", "total_assets", "current_assets",
               "current_liabilities", "total_liabilities", "equity", "retained_earnings", "goodwill", "shares", "rnd",
               "net_interest_income"}
FINANCIAL_SECTORS = {"Financials", "Banks", "Financial Services", "Insurance"}
FINANCIAL_SKIP = {"total_debt", "free_cash_flow", "capex", "current_assets", "current_liabilities", "gross_profit"}
PAIRS = [("current_assets", "current_liabilities")]
CHECK = ["revenue", "net_income", "total_assets", "equity"]  # voci per il controllo di coerenza per azienda
FLOW_KINDS = {"inc", "cf"}


# ------------------------------------------------------------------ scarico (con copia locale)
def fetch(ticker: str, cache_dir: str | Path, max_age_days: int = 7, now: pd.Timestamp | None = None) -> dict:
    """Prospetti di Yahoo del ticker: annuali (inc, bs, cf), ultimi 12 mesi (ttm_inc, ttm_cf), patrimoniale
    trimestrale (qbs) e valuta del bilancio. Copia in `cache_dir/<ticker>.json`, riscaricata dopo `max_age_days`."""
    now = now or pd.Timestamp.now()
    path = Path(cache_dir) / f"{ticker}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        if (now - pd.Timestamp(raw["fetched"])).days < max_age_days:
            return _decode(raw)
    import yfinance as yf

    t = yf.Ticker(ticker)
    frames = {"inc": t.income_stmt, "bs": t.balance_sheet, "cf": t.cashflow, "qbs": t.quarterly_balance_sheet}
    for k, attr in (("ttm_inc", "ttm_income_stmt"), ("ttm_cf", "ttm_cashflow")):
        try:
            frames[k] = getattr(t, attr)
        except Exception:  # noqa: BLE001 - versioni di yfinance o titoli senza dati trimestrali
            frames[k] = pd.DataFrame()
    if all(frames[k] is None or frames[k].empty for k in ("inc", "bs", "cf")):
        # Yahoo, quando limita le richieste, risponde spesso con tabelle vuote invece di un errore: niente copia
        # (si riprova al prossimo giro) e niente sostituzione dei valori già salvati
        raise ValueError("Yahoo non ha restituito prospetti (limite di richieste o titolo senza dati)")
    try:
        currency = (t.get_info() or {}).get("financialCurrency")
    except Exception:  # noqa: BLE001
        currency = None
    raw = {"fetched": now.isoformat(), "currency": currency,
           "frames": {k: (None if df is None or df.empty else df.to_json(orient="split", date_format="iso"))
                      for k, df in frames.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return _decode(raw)


def _decode(raw: dict) -> dict:
    out = {"currency": raw.get("currency"), "fetched": pd.Timestamp(raw["fetched"])}
    for k, js in raw["frames"].items():
        if js is None:
            out[k] = pd.DataFrame()
            continue
        df = pd.read_json(io.StringIO(js), orient="split")
        df.columns = pd.to_datetime(df.columns).tz_localize(None).normalize()
        out[k] = df
    return out


# ------------------------------------------------------------------ voci
def annual_values(y: dict) -> pd.DataFrame:
    """Esercizi di Yahoo nelle voci del sistema (righe = chiusure, colonne = metriche, segni come SEC)."""
    stmts = {k: y.get(k, pd.DataFrame()) for k in ("inc", "bs", "cf")}
    cols = sorted({c for df in stmts.values() if df is not None and not df.empty for c in df.columns})
    rows = {c: {m: data_audit.yahoo_value(stmts, m, c) for m in METRICS} for c in cols}
    return pd.DataFrame.from_dict(rows, orient="index").reindex(columns=METRICS)


def ttm_values(y: dict) -> pd.DataFrame:
    """Ultimi 12 mesi di Yahoo: flussi dalle colonne TTM, patrimoniale dal trimestre con la stessa chiusura."""
    inc, cf, qbs = (y.get(k, pd.DataFrame()) for k in ("ttm_inc", "ttm_cf", "qbs"))
    if inc is None or inc.empty:
        return pd.DataFrame(columns=METRICS)
    out = {}
    for c in inc.columns:
        bs = qbs[[c]] if qbs is not None and not qbs.empty and c in qbs.columns else pd.DataFrame()
        cfc = cf[[c]] if cf is not None and not cf.empty and c in cf.columns else pd.DataFrame()
        stmts = {"inc": inc[[c]], "cf": cfc, "bs": bs}
        out[c] = {m: data_audit.yahoo_value(stmts, m, c) for m in METRICS}
    return pd.DataFrame.from_dict(out, orient="index").reindex(columns=METRICS)


def _near(periods: pd.DatetimeIndex, day: pd.Timestamp) -> pd.Timestamp | None:
    if len(periods) == 0:
        return None
    d = np.abs((periods - day).days)
    i = int(np.argmin(d))
    return periods[i] if d[i] <= PERIOD_TOLERANCE_DAYS else None


def consistency(primary: pd.DataFrame, yahoo: pd.DataFrame) -> tuple[int, int]:
    """(confronti, grandi differenze) tra voci di controllo dove ci sono entrambe le fonti (stesso esercizio)."""
    n = bad = 0
    for p in yahoo.index:
        q = _near(primary.index, p)
        if q is None:
            continue
        for m in CHECK:
            a = primary.at[q, m] if m in primary.columns else np.nan
            b = yahoo.at[p, m]
            if pd.notna(a) and pd.notna(b) and a != 0:
                n += 1
                bad += data_audit.classify(float(a), float(b))[1] == "grande"
    return n, bad


def _fill(period_values: pd.Series, have: set[str], skip: set[str]) -> dict[str, float]:
    """Voci di Yahoo da aggiungere a un periodo che ha già le voci `have`."""
    out = {m: float(v) for m, v in period_values.items()
           if m not in have and m not in skip and pd.notna(v)}
    for a, b in PAIRS:  # in coppia o niente (un rapporto tra fonti diverse non ha senso)
        if (a in out) != (b in out) or ((a in out) and (a in have or b in have)):
            out.pop(a, None)
            out.pop(b, None)
    if "free_cash_flow" in out and not {"operating_cash_flow", "capex"} <= (set(out) | have):
        out.pop("free_cash_flow")
    return out


def plan(ticker: str, primary_rows: pd.DataFrame, y: dict, fx=None, skip_metrics: set[str] = frozenset(),
         ) -> tuple[pd.DataFrame, str]:
    """Righe da scrivere (ticker, period_end, metric, value, source) e una nota. `primary_rows` = righe di financials
    del ticker da fonti NON Yahoo; `fx(day)` = cambio dalla valuta del bilancio a quella del prezzo (None = 1)."""
    cols = ["ticker", "period_end", "metric", "value", "source"]
    prim = primary_rows[~primary_rows["source"].astype(str).isin(SOURCES)] if not primary_rows.empty else primary_rows
    ttm_mask = prim["source"].astype(str).str.endswith("_ttm") if not prim.empty else pd.Series(dtype=bool)

    esef_periods = set(pd.to_datetime(prim.loc[prim["source"].astype(str) == "esef", "period_end"])) \
        if not prim.empty else set()

    def wide(df):
        if df.empty:
            return pd.DataFrame()
        df = df.sort_values("filed").drop_duplicates(["period_end", "metric"], keep="last")
        w = df.pivot(index="period_end", columns="metric", values="value")
        w.index = pd.to_datetime(w.index)
        return w.sort_index()

    annual_p = wide(prim[~ttm_mask]) if not prim.empty else pd.DataFrame()
    ttm_p = wide(prim[ttm_mask]) if not prim.empty else pd.DataFrame()

    ya = annual_values(y)
    if ya.empty:
        return pd.DataFrame(columns=cols), "nessun bilancio su Yahoo"
    if fx is not None:
        rate = pd.Series({p: fx(p) for p in ya.index})
        money = [m for m in METRICS if m != "shares"]
        ya[money] = ya[money].mul(rate, axis=0)
    n, bad = consistency(annual_p, ya) if not annual_p.empty else (0, 0)
    if n and bad > n / 3:
        return pd.DataFrame(columns=cols), f"saltata: {bad} grandi differenze su {n} confronti con i dati primari"

    rows = []
    for p in ya.index:
        q = _near(annual_p.index, p) if not annual_p.empty else None
        if q is None:  # esercizio assente nelle fonti primarie: intero
            vals = _fill(ya.loc[p], set(), skip_metrics)
            if {"revenue", "net_income", "total_assets"} - set(vals) and len(vals) < 8:
                continue  # troppo poco per essere un bilancio
            rows += [(ticker, p, m, v, SOURCE) for m, v in vals.items()]
        else:
            have = set(annual_p.loc[q].dropna().index)
            skip = skip_metrics | (set(METRICS) - ESEF_GAP_OK if q in esef_periods else set())
            rows += [(ticker, q, m, v, SOURCE) for m, v in _fill(ya.loc[p], have, skip).items()]
    # ultimi 12 mesi: solo l'ultimo periodo primario rimasto a metà (più recente dell'ultimo esercizio)
    if not ttm_p.empty:
        last = ttm_p.index[-1]
        newest_annual = annual_p.index[-1] if not annual_p.empty else pd.Timestamp.min
        yt = ttm_values(y)
        p = _near(yt.index, last) if not yt.empty else None
        if p is not None and last > newest_annual:
            vals = yt.loc[p]
            if fx is not None:
                money = [m for m in METRICS if m != "shares"]
                vals = vals.copy()
                vals[money] = vals[money] * fx(p)
            have = set(ttm_p.loc[last].dropna().index)
            rows += [(ticker, last, m, v, SOURCE_TTM) for m, v in _fill(vals, have, skip_metrics).items()]
    out = pd.DataFrame(rows, columns=cols)
    return out, f"{len(out)} valori" if len(out) else "niente da aggiungere"


def with_filed(new: pd.DataFrame, old: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """`filed` = giorno del primo scarico, mantenuto se il valore non è cambiato (tolleranza 0,1%)."""
    new = new.copy()
    new["filed"] = today.normalize()
    if old is None or old.empty or new.empty:
        return new
    o = old.copy()
    o["period_end"] = pd.to_datetime(o["period_end"])
    o = o.sort_values("filed").drop_duplicates(["period_end", "metric", "source"], keep="last")
    m = new.merge(o[["period_end", "metric", "source", "value", "filed"]], on=["period_end", "metric", "source"],
                  how="left", suffixes=("", "_old"))
    same = m["value_old"].notna() & (np.abs(m["value"] - m["value_old"]) <= 1e-3 * np.abs(m["value_old"]).clip(1))
    m.loc[same, "filed"] = pd.to_datetime(m.loc[same, "filed_old"])
    return m[["ticker", "period_end", "metric", "value", "filed", "source"]]


# ------------------------------------------------------------------ quali aziende
def candidates(fin: pd.DataFrame, tickers: list[str], foreign: set[str], core: list[str],
               stale_days: int, today: pd.Timestamp) -> list[str]:
    """Aziende da completare: estere (sempre: l'archivio ESEF è in ritardo), senza bilanci primari, con l'ultimo
    bilancio primario vecchio o con voci di base mancanti nella vista più recente."""
    prim = fin[~fin["source"].astype(str).isin(SOURCES)] if not fin.empty else fin
    filled = set(fin.loc[fin["source"].astype(str).isin(SOURCES), "ticker"]) if not fin.empty else set()
    out = []
    for t in tickers:
        if t in foreign or t in filled:  # chi ha già valori di riserva si rivede sempre (si tolgono se superati)
            out.append(t)
            continue
        g = prim[prim["ticker"] == t] if not prim.empty else prim
        if g.empty:
            out.append(t)
            continue
        last = pd.to_datetime(g["period_end"]).max()
        latest = g[pd.to_datetime(g["period_end"]) == last]
        if (today - last).days > stale_days or set(core) - set(latest["metric"]):
            out.append(t)
    return out


class YahooFiller:
    """Passo dell'aggiornamento: completa i bilanci con Yahoo dove le fonti primarie mancano."""

    def __init__(self, db, cfg: dict, fetcher=None, fx_rate=None):
        self.db, self.cfg = db, cfg
        c = cfg.get("yahoo_fundamentals", {})
        self.cache_dir = c.get("cache_dir", "data/cache/yahoo_fin")
        self.max_age_days = int(c.get("max_age_days", 7))
        self.stale_days = int(c.get("stale_days", 450))
        self.core = list(c.get("core", ["revenue", "net_income", "operating_income", "total_assets", "equity",
                                         "operating_cash_flow", "interest_expense"]))
        self.no_shares = set(c.get("no_shares", [])) | set((cfg.get("sec", {}).get("share_classes") or {}).keys())
        self.fetcher = fetcher or (lambda t: fetch(t, self.cache_dir, self.max_age_days))
        self.fx_rate = fx_rate

    def _fx(self, src: str | None, dst: str | None):
        if not src or not dst or src == dst:
            return None
        if self.fx_rate is None:
            from modules.europe import _yahoo_fx
            self.fx_rate = _yahoo_fx
        cache: dict = {}

        def rate(day):
            day = pd.Timestamp(day)
            if day not in cache:
                cache[day] = self.fx_rate(src, dst, day)
            return cache[day]
        return rate

    def update(self, tickers: list[str], today: pd.Timestamp | None = None) -> dict:
        """Ritorna i conteggi: aziende provate, completate, saltate (con il motivo), valori scritti."""
        today = (today or pd.Timestamp.now()).normalize()
        fin = self.db.load_financials(tickers)
        fc = self.db.load_foreign()
        foreign = {} if fc.empty else dict(zip(fc["ticker"], fc["currency"].fillna("EUR")))
        financial = set() if fc.empty else set(fc.loc[fc["sector"].isin(FINANCIAL_SECTORS), "ticker"])
        comp = self.db.load_companies() if hasattr(self.db, "load_companies") else pd.DataFrame()
        us_financial = set() if comp.empty or "sector" not in comp else set(
            comp.loc[comp["sector"] == "Financials", "ticker"])
        todo = candidates(fin, tickers, set(foreign), self.core, self.stale_days, today)
        stats = {"provate": len(todo), "completate": 0, "valori": 0, "saltate": {}, "errori": {}}
        for t in todo:
            try:
                y = self.fetcher(t)
                g = fin[fin["ticker"] == t]
                fx = self._fx(y.get("currency"), foreign.get(t, "USD"))
                skip = ({"shares"} if t in self.no_shares else set()) | (FINANCIAL_SKIP if t in financial else set())
                if t not in financial | us_financial:  # per un'industriale Yahoo dà interessi attivi - passivi
                    skip |= {"net_interest_income"}
                rows, note = plan(t, g, y, fx=fx, skip_metrics=skip)
            except Exception as e:  # noqa: BLE001 - un titolo che fallisce non ferma gli altri
                stats["errori"][t] = str(e)[:200]
                logger.warning("Yahoo bilanci %s: %s", t, e)
                continue
            if note.startswith("saltata"):
                stats["saltate"][t] = note
                logger.warning("Yahoo bilanci %s %s", t, note)
            old = g[g["source"].astype(str).isin(SOURCES)]
            rows = with_filed(rows, old, today)
            self.db.replace_financials(t, rows, list(SOURCES))
            if len(rows):
                stats["completate"] += 1
                stats["valori"] += len(rows)
        logger.info("Bilanci di riserva da Yahoo: %d aziende provate, %d completate, %d valori, %d saltate, "
                    "%d errori", stats["provate"], stats["completate"], stats["valori"], len(stats["saltate"]),
                    len(stats["errori"]))
        return stats
