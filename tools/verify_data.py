"""
Verifica a campione dei dati: bilanci contro Yahoo, prezzi contro Nasdaq (vedi modules/data_audit.py).

    .venv/bin/python tools/verify_data.py [--per-sector 3] [--tickers AAPL,MSFT]

Scrive reports/verifica_dati_AAAAMMGG.csv (ogni confronto) e .md (riepilogo e casi da guardare).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.db_manager import DBManager  # noqa: E402
from modules import data_audit as da  # noqa: E402
from modules.config import PROJECT_ROOT, load_config  # noqa: E402

# casi difficili da controllare sempre: banche, REIT, più classi di azioni, split recenti, holding nuove
MUST = ["JPM", "BAC", "BRK-B", "O", "PLD", "GOOGL", "NVDA", "WMT", "XOM", "HSY", "AAPL", "AVGO"]


def md_table(df: pd.DataFrame, index: bool = True) -> str:
    """Tabella Markdown senza dipendenze (pandas.to_markdown vuole il pacchetto tabulate)."""
    d = df.reset_index() if index else df
    fmt = lambda v: f"{v:,.4g}" if isinstance(v, float) else str(v)  # noqa: E731
    head = "| " + " | ".join(map(str, d.columns)) + " |"
    sep = "|" + "---|" * len(d.columns)
    return "\n".join([head, sep] + ["| " + " | ".join(fmt(v) for v in row) + " |" for row in d.itertuples(index=False)])


def ours_annual(db: DBManager, ticker: str) -> pd.DataFrame:
    """Esercizi completi come depositati alla SEC (ultima versione), senza rettifiche per gli split."""
    from modules.market_data import adjust_shares_for_splits

    fin = db.load_financials([ticker])
    fin = fin[fin["source"].isin(["sec", "sec_classes"])].sort_values("filed")
    # azioni alla base di oggi (Yahoo riporta gli esercizi passati già rettificati per gli split)
    fin = adjust_shares_for_splits(fin, db.load_splits([ticker]))
    if fin.empty:
        return pd.DataFrame()
    last = fin.drop_duplicates(["period_end", "metric"], keep="last")
    return last.pivot(index="period_end", columns="metric", values="value").sort_index()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--per-sector", type=int, default=3)
    p.add_argument("--tickers", default="")
    args = p.parse_args(argv)
    cfg = load_config()
    db = DBManager(cfg["data"]["db_path"])
    comp = db.load_companies()
    comp = comp[comp["in_index"] == 1]
    by_sector = {s: list(g["ticker"]) for s, g in comp.groupby("sector")}
    tickers = args.tickers.split(",") if args.tickers else da.sample(by_sector, args.per_sector, MUST)
    sector = comp.set_index("ticker")["sector"].to_dict()
    prices = db.load_prices(tickers)

    stmt_rows, price_rows = [], []
    for i, t in enumerate(tickers, 1):
        try:
            res = da.compare_statements(ours_annual(db, t), da.yahoo_annual(t), t)
            stmt_rows.append(res.assign(sector=sector.get(t)))
        except Exception as exc:  # noqa: BLE001
            print(f"{t}: bilanci Yahoo non letti ({exc})")
        try:
            mine = prices[prices["ticker"] == t].set_index("date")["close"]
            mine.index = pd.to_datetime(mine.index)
            start = (pd.Timestamp.now() - pd.Timedelta(days=380)).strftime("%Y-%m-%d")
            price_rows.append({"ticker": t, **da.compare_prices(mine, da.nasdaq_closes(t, start))})
        except Exception as exc:  # noqa: BLE001
            print(f"{t}: prezzi Nasdaq non letti ({exc})")
        print(f"{i}/{len(tickers)} {t}", flush=True)
        time.sleep(0.5)

    stmts = pd.concat(stmt_rows, ignore_index=True) if stmt_rows else pd.DataFrame()
    px = pd.DataFrame(price_rows)
    stamp = datetime.now().strftime("%Y%m%d")
    out = PROJECT_ROOT / "reports"
    out.mkdir(exist_ok=True)
    stmts.to_csv(out / f"verifica_dati_{stamp}.csv", index=False)
    px.to_csv(out / f"verifica_prezzi_{stamp}.csv", index=False)

    lines = [f"# Verifica dei dati del {datetime.now():%d/%m/%Y}", "",
             f"{len(tickers)} aziende: {', '.join(tickers)}", "", "## Bilanci contro Yahoo (ultimi esercizi)", ""]
    if not stmts.empty:
        table = stmts.pivot_table(index="metric", columns="verdict", values="ticker", aggfunc="count", fill_value=0)
        lines += [md_table(table), "", "### Grandi differenze", ""]
        big = stmts[stmts["verdict"] == "grande"].sort_values(["metric", "ticker"])
        lines += [md_table(big[["ticker", "sector", "period_end", "metric", "ours", "yahoo", "diff"]], index=False)
                  if not big.empty else "Nessuna.", ""]
    lines += ["## Prezzi contro Nasdaq (ultimi 250 giorni)", "",
              md_table(px, index=False) if not px.empty else "Nasdaq non raggiungibile.", ""]
    (out / f"verifica_dati_{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {out / f'verifica_dati_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
