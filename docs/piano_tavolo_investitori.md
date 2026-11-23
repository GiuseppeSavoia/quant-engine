# Tavolo dei grandi investitori: brief per una nuova chat (2026-11-23)

Da leggere insieme a `CLAUDE.md` (contesto completo) e `docs/prossimi_passi.md`. Qui c'è solo la funzione nuova.

## Obiettivo
Aggiungere alla scheda azienda un **"tavolo dei grandi investitori"**. Per un'azienda mostra come la giudicherebbe
ciascuno dei grandi investitori del catalogo e poi una sintesi: chi la comprerebbe, chi no e perché, dove sono in
disaccordo, cosa dovrebbe cambiare.

L'idea della struttura viene da `virattt/ai-hedge-fund`: analisti con lo stile di un investitore, poi rischio, poi sintesi.
Qui però **non si usa nessuna AI e nessuna API di modelli linguistici**: è una scelta ferma dell'utente. Ogni opinione
e ogni frase escono da regole e testi fissi nel codice. Dal progetto di riferimento non si copia codice, si prende solo
l'idea.

## Principi non negoziabili (coerenti con il progetto)
1. **Deterministico**: stessi dati, stessa configurazione, stesso risultato. Le frasi vengono da modelli di testo in
   italiano riempiti con i numeri del catalogo.
2. **Solo informazione**, come le stime degli analisti: il tavolo non entra nel Verdetto (`verdict.py`), negli avvisi
   o nel sito pubblico. Non dà BUY/SELL.
3. **Point-in-time**: si usa la stessa vista dei dati della scheda (`MarketData.as_of`), con la data dei dati mostrata.
4. **Un dato mancante non conta mai come regola passata**: si scrive "dato mancante" (stessa regola ferma del Verdetto).
5. **Niente numeri magici**: soglie per "quasi" e numero di membri in `config.yaml` (sezione `investor_panel:`), con i
   default in `modules/config.py`. Le regole degli investitori restano quelle di `investors.py`, fissate a priori: qui
   non si toccano.
6. Nessuna rete in più: tutto si calcola dai dati già presenti, anche in demo e nei test.

## Cosa esiste già da riusare (non ricalcolare a modo proprio)
- `modules/investors.py`: 18 strategie dei grandi investitori.
  - Ognuna ha una definizione (`universe.sectors`, `filters` = regole {metric, op, value}, `ranking` = metriche
    pesate, `portfolio.positions`).
  - Ognuna ha una scheda (`who`, `source`, `original`, `translation` = cosa manca nella traduzione in regole).
  - Magic Formula e Piotroski stanno in `strategies.CATALOG`.
- `modules/screener.py`: `apply_rules`, `describe_rule` (frase della regola), regola `top_sector` / `top_index`.
- `modules/metrics.py`: catalogo unico, `compute_metrics`, `percentile_ranks`, formati (`fmt_metric` in ui/components).
- `DashboardData.strategy_result` / `experiments`: risultati storici già salvati di ogni strategia (CAGR sulle due
  metà, Sharpe). `modules/significance.py`: Sharpe deflazionato. Vanno mostrati con le cautele già scritte in CLAUDE.md
  (60 configurazioni provate, selezione a posteriori).
- `verdict.py` (pilastri, prove, "cosa farebbe cambiare il giudizio" con il prezzo che lo cambierebbe): è il modello da
  seguire per il prezzo che fa passare una regola di prezzo.
- Servizi in `modules/services/` (scheda e API passano da lì), `api/` (FastAPI, nessun calcolo), scheda Streamlit in
  `ui/pages/azienda/`, funzioni della scheda in `commands.FUNCTIONS`, pezzi grafici in `ui/components.py`
  (`panel`, `badge`, `dense_table`, `empty_state`, `notice`).

## Disegno proposto
**Per ogni investitore** (`modules/investor_panel.py`, funzione pura e testabile):
- **Universo**: il settore dell'azienda è tra i suoi settori? Se no: "Fuori dal suo campo", con il motivo preso da
  `translation` (es. Financials esclusi).
- **Regole**: per ogni filtro, valore dell'azienda contro la soglia → passata / mancata / dato mancante. Per le
  mancate, di quanto (scarto relativo).
- **Classifica**: punteggio di `ranking` (media pesata dei percentili, come `Strategy._run_rules`) e posizione
  nell'universo filtrato. Rientrerebbe nei suoi `positions` titoli?
- **Esito**, con regole in ordine e scritte in chiaro come `verdict._decide`:
  - "La comprerebbe": tutte le regole passate e dentro le posizioni;
  - "Le piace, ma ne ha di migliori": regole passate, fuori classifica;
  - "Quasi": una sola regola mancata entro la soglia di `investor_panel.near_miss`;
  - "Non fa per lui";
  - "Fuori dal suo campo";
  - "Dati insufficienti".
- **"Cosa dovrebbe cambiare"**:
  - per le regole di prezzo (P/E, P/B, P/E x P/B, FCF yield…) il prezzo a cui passerebbero, calcolato e non stimato;
  - per le altre, il valore che servirebbe.
- **Contesto**: una riga dalla scheda (`who`, metodo originale), "cosa manca rispetto al suo vero metodo"
  (`translation`) e il risultato storico della strategia con le sue cautele.

**Sintesi del tavolo** (sempre a regole):
- quanti la comprerebbero / quasi / no;
- **disaccordi** tra famiglie di stile: qualità (Munger, Terry Smith, Buffett), value profondo (Graham, Schloss,
  Dreman, Templeton, Klarman), crescita e momentum (O'Neil, Driehaus, Lynch, Zweig). Le famiglie vanno scritte in
  config o nel modulo, fissate a priori.
- "L'ostacolo più comune": la regola mancata più spesso;
- il prezzo più alto a cui almeno un investitore in più la comprerebbe.

**Facoltativo, dopo**: lo storico del tavolo sugli ultimi trimestri (stesso calcolo su `MarketData.as_of` a fine
trimestre: "da quando piace a Buffett"). Va salvato come `verdict_history`, solo se costa poco: misurare prima.

**Dove si vede**:
- **Scheda azienda**: una funzione nuova sotto "Altro" (codice es. `TAV` o `INV`). In cima la sintesi, sotto una riga
  per investitore con il badge dell'esito, poi un dettaglio richiudibile con regole, valori e soglie.
- **Esplora**: un filtro "piace ad almeno N investitori" (metrica derivata, NON usabile nelle strategie: va aggiunta a
  `JUDGMENT_KEYS` come la checklist, per non creare strategie circolari).
- **API**: `GET /api/v1/companies/{ticker}/investors` tramite un servizio `modules/services/investor_panel.py`.

## Prima di iniziare
- **Albero di lavoro non pulito**: al 2026-11-23 risultano modificati, senza commit, `app.py`, `CLAUDE.md`,
  `modules/alerts.py`, `dashboard_data.py`, `estimates.py`, i file in `docs/` e altri. Chiedere all'utente se sono
  lavoro in corso di un'altra sessione: vale la regola di una sola sessione per cartella, e il tavolo non va mischiato
  in quei commit.
- Nessuna dipendenza nuova.

## Ordine di lavoro e criterio di "fatto"
1. `investor_panel.py`: esito per investitore + sintesi, con test. Casi da coprire:
   - tutte le regole passate;
   - una mancata di poco;
   - dato mancante;
   - settore escluso;
   - prezzo che fa passare una regola di P/E (verificato ricalcolando la metrica a quel prezzo);
   - determinismo con `PYTHONHASHSEED` diversi.
2. Servizio + funzione nella scheda (test con AppTest sulla demo, come `tests/test_app_pages.py`).
3. Filtro in Esplora + endpoint API.
4. Aggiornare `CLAUDE.md` (Architettura) e `docs/prossimi_passi.md`.

"Fatto" quando:
- l'intera suite `.venv/bin/python -m pytest` è verde;
- la funzione è stata provata nel browser sulla demo (preview `webapp-demo`), con il server riavviato dopo le modifiche;
- su 3 aziende reali (es. una di qualità, una value, una finanziaria) gli esiti tornano con le regole lette a mano.

## Da non fare
- Nessuna API di AI o modello linguistico, nemmeno come opzione.
- Non cambiare soglie, pesi o universi delle strategie di `investors.py` per far "tornare" gli esiti.
- Non presentare l'esito come una raccomandazione. Nota fissa: "Come le regole tradotte dal metodo pubblicato di ogni
  investitore leggono i numeri di oggi. Non è il loro giudizio e non è una raccomandazione."
- Non copiare codice da `koala73/worldmonitor`: è AGPL-3.0. Al massimo ispirazione per un futuro riquadro macro, che
  partirà dai feed che `news.py` ha già.

## Dopo: GitHub
Il repository pubblico `GiuseppeSavoia/quant-engine` è una fotografia di questa cartella, non è collegato a git qui.
Va aggiornato con lo stesso metodo:
- copia da `git archive HEAD`;
- senza `CLAUDE.md` e `.claude/`;
- `sec.user_agent: ""`;
- controllo che non ci siano email, percorsi `/Users`, chiavi o `.env`.

Poi si aggiunge una sezione "Legendary investors panel" al README, con uno screenshot preso dalla demo.
