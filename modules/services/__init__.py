"""Servizi dei dati dell'app, divisi per area, senza Streamlit (riusabili da main.py, strumenti, un sito statico).

- data.py       AppData: caricamento (copia Parquet), versioni, anagrafica, universi, valute, catalogo delle
                metriche, prezzi; contesto comune di tutti i servizi
- company.py    scheda azienda: bilanci, archivio SEC, metriche con i confronti, multipli nel tempo, concorrenti,
                dividendi, documenti, insider e grandi investitori, Confronta
- valuation.py  costo del capitale, valutazione a leve e plausibilità, diario delle valutazioni, DCF
- portfolio.py  portafoglio reale, esposizione dentro gli ETF, diario delle decisioni
- watch.py      aziende seguite e avvisi della home, Esplora, note e scheda di ricerca
- my_method.py  metriche personali, checklist, profilo, dati inseriti a mano
- strategies.py ultima analisi della strategia, strategie a regole, risultati dei backtest
- news.py       8-K e 10-Q/10-K recenti dalla SEC
- reports.py    CSV dei rapporti di main.py (backtest, ricerca, fattori, qualità dei dati)

Uso: `app = AppData(cfg, db, reports_dir)`, poi `app.services.company.peers("AAPL")` o `app.peers("AAPL")`.
`modules.dashboard_data.DashboardData` è la facciata della webapp (stessi metodi di sempre)."""
from modules.services.data import AppData, Services

__all__ = ["AppData", "Services"]
