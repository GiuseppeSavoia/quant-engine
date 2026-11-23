# Storia delle decisioni e dei risultati

Spostato da CLAUDE.md il 2026-11-16: diario datato dei piani concordati e dei risultati dei backtest.
Non serve a ogni sessione; CLAUDE.md tiene solo comandi, architettura e regole da rispettare.

## Piano concordato il 2026-11-04 (un blocco per commit) — stato al 2026-11-05
- ✅ A. Verifica dei dati (`data_audit.py`, `tools/verify_data.py` -> reports/verifica_dati_*.md/csv): bilanci contro
  Yahoo (4 esercizi, ~88% identici), prezzi contro Nasdaq (api.nasdaq.com con UA da browser; Stooq non risponde): 1 giorno
  su 11.250 sopra il 2% (stacco dividendo CME). Corretti in `sec_edgar.py`: debito da strumenti (`DEBT_NOTES`, usato
  solo se > 1,25 x il totale standard: WMT), debito trimestrale con le sole voci a breve = NaN (BAX, ED), cassa +
  investimenti <= attivo corrente, capex software (ROP), EBIT = utile ante imposte + interessi se manca (XOM, SLB),
  ricavi delle banche `RevenuesNetOfInterestExpense` o margine + altri ricavi (JPM 182 mld). Controlli permanenti in
  `data_quality._consistency` (debito forse incompleto, liquidità incoerente, azioni vecchie). Differenze rimaste =
  definizioni di Yahoo (EBIT normalizzato, riacquisti con le ritenute, cassa).
- ✅ B (in parte). Istantanea parquet di MarketData (`save_snapshot`/`load_snapshot`, `data/cache/<db>/`, chiave =
  ultima esecuzione | `db.data_version()` | dati utente | tickers): l'apertura non rilegge SQLite (prima ~11 s).
  Barra comandi e tasti F su una riga, barra laterale chiusa, `panel()` con barra del titolo, `dense_table()` (colonna
  del nome che si accorcia con "…"), testata della scheda su una riga (`_company_header`), GP 1M..MAX, mirino, niente
  barra Plotly, codici al posto delle emoji negli avvisi (FV, DOC, MOV, 52S, INS, REV, MRG). Fatti dopo: app.py diviso per
  pagina (2026-11-12), candele (GP: candele o linea, volumi sotto con lo stesso asse, medie 50/200, festivi tolti
  dall'asse, settimanali/mensili per 5A/MAX; apertura in `MarketData.open`; soglie in `price_chart:` di config.yaml).
  Resta: IBM Plex Mono incluso (serve il permesso di scaricare i font).
- ✅ C. Metriche di settore (gruppo "Settore" in metrics.py, `sector_metrics`): P/TBV, ROTCE, margine di interesse,
  efficienza, accantonamenti (banche), loss ratio (assicurazioni), FFO yield e P/FFO (solo se immobili >= 30%
  dell'attivo). Voci SEC nuove in `_flow_metrics`/`_balance_metrics`. Concorrenti con colonne per settore
  (`PEER_COLUMNS_BY_SECTOR`). JPM P/TBV 2,97, ROTCE 20,7%; TRV loss ratio 56,5%; O P/FFO 15.
- ✅ D (in parte). `europe.py`: FTSE MIB + Euro Stoxx 50 da Wikipedia (85 aziende, tabella `foreign_companies`,
  `companies.in_index` 0: niente backtest), prezzi Yahoo .MI/.PA/.DE/.AS..., bilanci annuali ESEF da filings.xbrl.org
  (xBRL-JSON, fine periodo esclusiva; indice dell'archivio per paese, 1 richiesta/s: risponde 429). LEI: GLEIF per
  ISIN (FTSE MIB), altrimenti nome contro gli emittenti dell'archivio (`match_archive_name`, forme societarie tolte,
  `NAME_ALIASES`), poi GLEIF per testo. 69 su 85: le 16 tedesche NON depositano in quell'archivio (niente bilanci, solo
  prezzi). Voci proprie dell'azienda solo per il capex (`EXT_CAPEX`: TotalEnergies, L'Oréal in francese, LVMH);
  azioni = utile / utile per azione (IFRS `...EarningsLossPerShare`). Bilanci in altra valuta convertiti (STM in USD).
  Esplora con scelta dell'universo, `DashboardData.universe_of`. 2026-11-13: ricavi e voci di settore per banche e
  assicurazioni, più debito, azioni e flussi dai bilanci ESEF (ultimo esercizio, 68 aziende: ricavi 49 -> 66, risultato
  operativo 43 -> 51, azioni 53 -> 59, flusso operativo 61 -> 68); banche SENZA debito né FCF per scelta (non hanno
  senso), azioni in scala sbagliata scartate. Da fare: grandi straniere USA (20-F IFRS), bilanci tedeschi (altra fonte).
  2026-11-22: bilanci tedeschi e buchi coperti con la riserva di Yahoo (`yahoo_fundamentals.py`). Accordo misurato con
  i dati primari (uguale o entro il 10%) su 70 titoli S&P a caso + le europee: SEC ricavi 97%, utile 98%, attivo 100%,
  patrimonio 100%, utile operativo 78%, interessi 92%, ammortamenti 73%, cassa 79%, debito 89%, immobilizzazioni 54%
  (tolte); ESEF utile operativo 65%, interessi 42%, debito 62% (definizioni IFRS diverse: negli esercizi ESEF si
  aggiungono solo le voci >= 80%). Verifica su fonti pubblicate: SAP 2025 ricavi 36,8 mld (comunicato Q4 2025),
  Siemens FY2025 ricavi 78,9 mld e utile agli azionisti ~9,6 mld (EPS 12,25). Database reale: "dati insufficienti" da
  83 a 28. Restano: REIT senza P/FFO (ARE, VICI, WY), V e ARES (esclusi apposta), banche/assicurazioni (BLK, ALV, MUV2,
  DBK, DB1, AZM, PST, ERIE: voci di settore), spin-off con pochi anni (HONA, VYLR, SKYD), FERG (cambio d'esercizio),
  TRN.MI (2025 assente anche su Yahoo), UNI.MI (Yahoo incoerente con ESEF), AIR.PA/TTE.PA/BC.MI (utile operativo e
  interessi ESEF assenti, quelli di Yahoo non concordano), DOW/MOS/MRNA/COIN/STLAM/TIT (pilastro Prezzo: utili
  negativi o multipli non significativi).
- ✅ E. Portafoglio a mano (`portfolio.py`, tabella `portfolio_transactions`, pagina Portafoglio, F6, comando PORT):
  costo medio ponderato in euro con commissioni, cambio del giorno (scritto o Yahoo EUR{CCY}=X), P/L con l'effetto
  cambio, pesi, dividendi (15% USA + 26%), tasse STIMATE (non consulenza fiscale), confronto con SPY con gli stessi euro
  negli stessi giorni (benchmark in `my_portfolio:` di config.yaml: NON `portfolio:`, che è la strategia simulata;
  `my_portfolio` resta fuori da `run_info.config_hash`). Titoli e cambi del portafoglio entrano nell'aggiornamento
  (`needed_symbols`).
- ✅ F. Diario delle decisioni (`journal.py`, tabella `decision_journal`, pagina Diario, comando DIARIO): BUY/SELL/PASS/
  WATCH, motivo, cosa farebbe cambiare idea, data di revisione (avviso REV in home); giudizio contro il benchmark solo
  dopo 90 giorni.
- G. Lettura dei documenti con l'API di Claude (chiave dell'utente solo sul Mac): riassunti 10-K/10-Q/8-K delle sue
  aziende, cambi nei fattori di rischio, limite di spesa, costo stimato sui primi documenti.


## Stato e prossimi passi
- Primo giro su dati reali fatto il 2026-11-01 (503 titoli, ~3 min di download + ~1 min di calcolo).
  Emersi e corretti: yfinance vecchio bloccato, azioni XBRL in scala sbagliata (MCD, HST, PKG...),
  capex a componenti (EOG, LLY), XOM con CIK della nuova holding (`sec.extra_ciks`).
  Restano: ~160 titoli senza DCF (Financials + FCF mancante), HONA/VYLR senza 10-K (nuovi),
  DCF poco sensato per i REIT (FCF senza acquisizioni di immobili).
- Piano concordato con l'utente (2026-11-01), in ordine: ✅ git, riproducibilità, backup, qualità dati,
  ricerca segnali → dati senza survivorship bias (fornitore a pagamento, decide l'utente) e backtest 15-20 anni
  → walk-forward e pesi stimati dai dati → correggere regime (in Bear BUY impossibile: max confidenza 0,70 < 0,80)
  e uscite ATR (dimezzano il rendimento in demo) → rischio di portafoglio (settori, vol target, drawdown,
  stress test) → registro posizioni persistente, costi realistici, cambio EUR/USD e tasse 26%
  → esecuzione automatica giornaliera + mesi di paper trading prima di soldi veri.
- Risultati (2026-11-01, dati 2012-2026, seconda metà = fuori campione 2019-2026): strategia originale +38%,
  migliore variante scelta sulla prima metà +54% (crollata fuori campione), strategia a fattori 50 titoli +134%
  (rotazione 19x/anno, quasi tutta dall'inversione a 1 mese), tutto l'indice a pesi uguali +119%, SPY +207%.
  Nessun fattore singolo significativo nei large cap; la combinazione regge fuori campione ma debole (IC ~0,02-0,04).
- 2026-11-02: `factors.exclude: [reversal_1m]` (scelta di progetto: costi, ma decisa DOPO aver visto la seconda
  metà, che va considerata già vista). Fattori senza inversione: 2a metà +133% (Sharpe 0,52, dd -36%, rotazione 6x,
  costi 11k invece di 33k su 100k), 1a metà +148% (Sharpe 0,78). Pesi uguali: 2a +119% (0,50), 1a +164% (0,88).
  Quindi la selezione per fattori NON batte in modo convincente comprare tutto a peso uguale (perde nella 1a metà,
  vince di ~1 punto/anno nella 2a). IC fuori campione della combinazione senza inversione +0,011 (t 0,69).
- 2026-11-02, TTM dai 10-Q (età mediana dei bilanci 303 → 91 giorni; 589/620 aziende con TTM più recente
  dell'esercizio): strategia originale 2012-26 +64% (era +60%), 2a metà +49% (era +38%). Fattori (pesi
  ristimati sulla 1a metà: FCF/prezzo 0,31, momentum 0,28, margine lordo/attivo 0,22...): 2a metà +168%
  (CAGR 14,2%, Sharpe 0,61, dd -37%) contro pesi uguali +119% e SPY +207%; 1a metà +159% contro pesi uguali
  +164%. Ma l'IC fuori campione cambia appena (+0,012, t 0,79): +35 punti di backtest con lo stesso IC dicono
  che differenze così stanno nel rumore. La 2a metà ormai è stata guardata più volte.
- 2026-11-02, limiti di rischio provati su entrambe le metà (fattori e originale): settori 30% neutro (attivato);
  volatilità obiettivo 15% (fattori 2a metà +168% → +113%, Sharpe 0,61 → 0,52) e perdita -15%/esposizione 0,5
  (+168% → +115%, drawdown massimo invariato -37%) peggiorano in entrambe le metà: spenti.
  Stress del portafoglio originale di oggi: beta 0,39 ma nel Covid -38% (mercato -34%): il beta sottostima le crisi.
- TTM: se non c'è un'etichetta comune ai tre periodi si combinano le metriche già calcolate (sinonimi, es. Agilent
  ProfitLoss/NetIncomeLoss), tranne ricavi e margine lordo (`NO_MIXED_LABELS`).
- 2026-11-02, catalogo strategie 2012-2026 (CAGR tutto / 1a metà / 2a metà): Magic Formula 16,0/14,3/17,6 (Sharpe
  0,78, unica sopra SPY 14,7/13,2/16,4 in entrambe le metà), Quality 13,8, combinazione fattori 13,7, pesi uguali
  13,0/13,9/12,0, Momentum 12,1, Value 11,8 (dd -49%), qualità a prezzo giusto 11,6, bassa volatilità 11,3, strategia
  originale 3,6/6,9/0,4. Cautele: 9 strategie provate (la migliore è anche la più fortunata), survivorship bias
  residuo (copertura ~87%), Magic Formula esclude Financials e Utilities. Piotroski (P/B nel 20% più basso,
  F-score >= 8): 10,4/8,3/12,6, dd -22%.
- Piotroski F-score: 9 controlli sugli ultimi 12 mesi vs un anno prima; con 7-8 controlli disponibili riportato a 9,
  con meno vuoto (copertura 92%). Altman Z: passività totali = attivo - patrimonio se manca la voce (copertura 79%
  esclusi i Financials, per cui non è significativo). Confronta: 2-5 aziende (5° colore della palette validata).
- Debito: riga a lungo termine del bilancio + debito a breve (stesse voci in 10-K e 10-Q). Prima i 10-Q di
  molte aziende (T, UPS, KO, HD) davano solo la parte a breve: debito trimestrale incoerente nell'11% dei casi
  (ora 2,9%, quasi tutto acquisizioni vere). Per la cassa restano salti del 7%, in gran parte reali (utility),
  qualche caso con fondi di terzi (AON). Con `--no-update` non si scarica nulla, nemmeno con --force-fundamentals.
- Corretto insieme: ricavi = voce più grande tra le etichette (ETN/FLS avevano "Revenues" ~0, i REIT gli
  affitti fuori). Restano incoerenze TTM/esercizio nello 0,6% dei casi, quasi tutte reali (banche, crociere
  nel Covid) o aziende con l'annuale sbagliato (CPT).
- Idee aperte: GARCH, correlazioni cross-asset, stress test macro, prezzi dei delistati
  per eliminare il survivorship bias, analisi della sensibilità del regime HMM (pesa molto sulle decisioni).
- `_backup_originale/` contiene la versione iniziale del progetto prima della riscrittura.

