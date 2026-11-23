"""
Controlli di qualità dei dati, eseguiti a ogni analisi prima di fidarsi dei segnali.

Ogni problema è una riga (ticker, controllo, gravità, dettaglio). Gravità:
  errore      il titolo viene valutato su dati sbagliati o mancanti (es. nessun prezzo)
  avviso      dato sospetto da guardare (es. salto di prezzo enorme, bilancio vecchio)

Controlli sui prezzi: titolo senza prezzi, ultimo prezzo vecchio, giorni mancanti rispetto al
calendario di borsa, prezzi non positivi o massimo < minimo, salti giornalieri enormi non spiegati
da uno split noto (tipico di uno split non rettificato o di un dato errato).
Controlli sui bilanci: bilanci assenti, metriche chiave mancanti, ultimo bilancio troppo vecchio,
numero di azioni che cambia di colpo tra due esercizi (split non riconosciuto).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KEY_METRICS = ["free_cash_flow", "shares", "revenue", "net_income", "equity"]
COLUMNS = ["ticker", "check", "severity", "detail"]


class DataQualityChecker:
    def __init__(self, cfg_quality: dict, max_stale_days: int):
        self.cfg = cfg_quality
        self.max_stale = pd.Timedelta(days=max_stale_days)

    def run(self, data, tickers: list[str], splits: pd.DataFrame | None = None) -> pd.DataFrame:
        issues: list[tuple] = []
        as_of = data.last_date
        calendar = data.calendar
        recent = calendar[calendar > as_of - pd.Timedelta(days=365)] if as_of is not None else calendar
        known_splits = set()
        if splits is not None and not splits.empty:
            known_splits = {(t, pd.Timestamp(d)) for t, d in zip(splits["ticker"], splits["date"])}
        for t in tickers:
            issues += self._prices(t, data, as_of, recent, known_splits)
            issues += self._financials(t, data, as_of)
        return pd.DataFrame(issues, columns=COLUMNS)

    # ------------------------------------------------------------- prezzi
    def _prices(self, t, data, as_of, recent, known_splits) -> list[tuple]:
        px = data.prices(t)
        if px.empty:
            return [(t, "prezzi assenti", "errore", "nessun prezzo nel database")]
        out = []
        if as_of is not None and as_of - px.index[-1] > self.max_stale:
            out.append((t, "prezzo vecchio", "errore", f"ultimo prezzo il {px.index[-1].date()}"))
        listed = recent[recent >= px.index[0]]  # un titolo quotato da poco non ha "buchi" prima della quotazione
        missing = len(listed.difference(px.index))
        if missing > self.cfg["max_missing_days"]:
            out.append((t, "giorni mancanti", "avviso", f"{missing} giorni di borsa senza prezzo nell'ultimo anno"))
        if (px <= 0).any():
            out.append((t, "prezzo non positivo", "errore", f"{int((px <= 0).sum())} giorni"))
        if t in data.high and t in data.low:
            bad = int((data.high[t] < data.low[t]).sum())
            if bad:
                out.append((t, "massimo < minimo", "avviso", f"{bad} giorni"))
        rets = data.returns(t)
        jumps = rets[rets.abs() > np.log(1 + self.cfg["max_daily_jump"])]
        unexplained = [d for d in jumps.index if (t, d) not in known_splits]
        if unexplained:
            worst = jumps.loc[unexplained].abs().idxmax()
            out.append((t, "salto di prezzo", "avviso",
                        f"{len(unexplained)} salti oltre il {self.cfg['max_daily_jump']:.0%}, il maggiore il "
                        f"{worst.date()} ({np.expm1(jumps[worst]):+.0%})"))
        return out

    # ------------------------------------------------------------- bilanci
    def _financials(self, t, data, as_of) -> list[tuple]:
        st = data.statements(t)
        if st.empty:
            return [(t, "bilanci assenti", "errore", "nessun bilancio: niente DCF né quality")]
        out = []
        missing = [m for m in KEY_METRICS if m not in st or st[m].dropna().empty]
        if missing:
            out.append((t, "metriche mancanti", "avviso", ", ".join(missing)))
        fin = data.financials
        filed = fin.loc[fin["ticker"] == t, "filed"]
        if as_of is not None and not filed.empty:
            age = (as_of - filed.max()).days
            if age > self.cfg["max_filing_age_days"]:
                out.append((t, "bilancio vecchio", "avviso", f"ultimo deposito {age} giorni fa"))
        if "shares" in st:
            sh = st["shares"].dropna()
            sh = sh[sh > 0]
            if len(sh) > 1:
                change = np.exp(np.abs(np.diff(np.log(sh.to_numpy()))))
                if change.max() > self.cfg["max_shares_change"]:
                    i = int(change.argmax())
                    out.append((t, "azioni discontinue", "avviso",
                                f"numero di azioni x{sh.iloc[i + 1] / sh.iloc[i]:.2f} tra {sh.index[i].date()} e "
                                f"{sh.index[i + 1].date()}: split non registrato?"))
        out += self._consistency(t, data)
        out += self._sparse_period(t, data)
        return out

    def _sparse_period(self, t, data) -> list[tuple]:
        """Ultimo periodo "a metà": molte meno voci dai depositi ufficiali (SEC/ESEF/a mano) del periodo prima (es. CLX
        e FERG: 12 mesi dai 10-Q senza il 10-K di base). Conta solo le fonti primarie: se la riserva di Yahoo ha
        coperto il buco lo si dice, perché quei numeri non vengono da un deposito."""
        fund = data.fundamentals(t)
        if len(fund) < 2 or not hasattr(data, "statement_sources"):
            return []
        annual = data.statements(t).index
        days = fund.index[-2:]
        counts, filled = [], 0
        for i, day in enumerate(days):
            src = data.statement_sources(t, ttm=day not in annual)
            row = src.loc[day].dropna().astype(str) if day in src.index else pd.Series(dtype=str)
            fb = row.str.startswith("yahoo_fill")
            counts.append(int((~fb).sum()))
            filled = int(fb.sum()) if i == 1 else filled
        prev, last = counts
        share = float(self.cfg.get("min_period_items_share", 0.6))
        if prev < 8 or last >= share * prev:
            return []
        extra = f"; {filled} completate dalla riserva di Yahoo" if filled else ""
        return [(t, "ultimo periodo a metà", "avviso",
                 f"{days[-1].date()}: {last} voci dai depositi ufficiali contro {prev} del periodo prima{extra} "
                 "(deposito mancante, incompleto o non ancora nell'archivio)")]

    def _consistency(self, t, data) -> list[tuple]:
        """Controlli di coerenza nati dalla verifica a campione contro Yahoo (2026-11): debito troppo piccolo per gli
        interessi che paga (voci di debito non lette), liquidità più grande delle attività correnti, azioni non
        aggiornate da più di un anno rispetto ai bilanci."""
        st = data.fundamentals(t)
        if st.empty:
            return []
        cur, latest = st.iloc[-1], st.ffill().iloc[-1]
        g = lambda row, k: float(row.get(k, np.nan))  # noqa: E731
        out = []
        debt, interest = g(latest, "total_debt"), abs(g(cur, "interest_expense"))
        if np.isfinite(interest) and interest > 0 and (not np.isfinite(debt) or interest > 0.25 * max(debt, 1.0)):
            shown = f"{debt / 1e6:,.0f} mln" if np.isfinite(debt) else "mancante"
            out.append((t, "debito forse incompleto", "avviso",
                        f"interessi {interest / 1e6:,.0f} mln l'anno con un debito di {shown}: voci di debito non lette?"))
        cash, current = g(latest, "cash"), g(latest, "current_assets")
        if np.isfinite(cash) and np.isfinite(current) and current > 0 and cash > current * 1.01:
            out.append((t, "liquidità incoerente", "avviso",
                        f"cassa {cash / 1e6:,.0f} mln più delle attività correnti ({current / 1e6:,.0f} mln)"))
        if "shares" in st and np.isnan(data.last_shares(st)) and st["shares"].notna().any():
            out.append((t, "azioni vecchie", "avviso", "l'ultimo numero di azioni ha più di un anno rispetto ai bilanci: "
                        "capitalizzazione e multipli vuoti (aziende con più classi di azioni?)"))
        return out


def stale_prices_summary(issues: pd.DataFrame, limit: int = 30) -> str:
    """Riepilogo in una riga dei titoli con il prezzo vecchio, raggruppati per ultimo giorno: un gruppo numeroso
    fermo allo stesso giorno indica un aggiornamento che salta un blocco di titoli, non singoli titoli sospesi.
    Stringa vuota se non ce ne sono."""
    if issues.empty:
        return ""
    stale = issues[issues["check"] == "prezzo vecchio"]
    if stale.empty:
        return ""
    days = stale["detail"].str.extract(r"(\d{4}-\d{2}-\d{2})")[0].fillna("?")
    by_day = days.value_counts().sort_index(ascending=False)
    names = sorted(stale["ticker"])
    shown = ", ".join(names[:limit]) + (f" e altri {len(names) - limit}" if len(names) > limit else "")
    groups = ", ".join(f"{d} ({n})" for d, n in by_day.items())
    return f"{len(names)} titoli con il prezzo vecchio, fermi al {groups}: {shown}"


def summary(issues: pd.DataFrame) -> pd.DataFrame:
    """Quanti titoli per controllo e gravità."""
    if issues.empty:
        return pd.DataFrame(columns=["check", "severity", "tickers"])
    return (issues.groupby(["severity", "check"])["ticker"].nunique().rename("tickers")
            .reset_index().sort_values(["severity", "tickers"], ascending=[False, False]))  # prima gli errori
