# Piano: interfaccia separata da Streamlit (QuantEngine come prodotto)

Data: 2026-11-23. Stato: **decisione 1 presa il 2026-11-23: servizio online (B)**; la riscrittura è quindi necessaria.
Aperte: decisione 2 (fornitore dei dati), 3 (parere legale), 4 (nuove dipendenze). Fase 1a avviata (non richiede
dipendenze nuove). Richiesta dell'utente: "è un prodotto che devo vendere".

## 1. Perché

Oggi l'interfaccia è Streamlit (17 pagine, ~8.800 righe in `ui/`). Va bene per una persona sul proprio Mac, ma per un
prodotto venduto ha quattro limiti che non si risolvono con lo stile:

| Limite | Dove si vede oggi |
|---|---|
| Un solo utente per server | `_apply_theme` cambia il tema di TUTTO il server; preferenze in file JSON; nessun login |
| Grafica legata agli interni di Streamlit | 40 selettori interni nel CSS (ora controllati da `tests/test_ui_contract.py`, ma restano interni) |
| Ogni clic riesegue la pagina | Scheda azienda ~3 s a pagina ricaricata; niente navigazione istantanea, niente stato locale |
| Controllo limitato su interazioni | tastiera, tabelle, menu, transizioni: tutto con JavaScript iniettato (`shell._keyboard`) |

Il motore (dati point-in-time, giudizio, valutazione, backtest, 611 test) è la parte di valore e NON si tocca.

## 2. Architettura proposta

```
 Browser / finestra desktop                    Python (motore di oggi, invariato)
┌──────────────────────────────┐   JSON    ┌──────────────────────────────────────────┐
│ React + TypeScript (Vite)    │ ───────▶  │ API FastAPI  (nuovo, sottile)            │
│ - design system (token Apple │ ◀───────  │   └─ DashboardData / modules/services    │
│   di ui/common.py)           │  figure   │   └─ verdict, valuation_model, screener… │
│ - TanStack Query / Table     │  Plotly   │   └─ updater (processo separato)         │
│ - plotly.js per i grafici    │  file     │ SQLite dati di mercato (sola lettura)    │
└──────────────────────────────┘ xlsx/pdf  │ Dati dell'utente (note, portafoglio, …)  │
                                           └──────────────────────────────────────────┘
```

Scelte e motivo:
- **API FastAPI sopra `DashboardData`**: le pagine usano già una facciata (`DashboardData`: 80 metodi distinti chiamati
  da `ui/`). MA non tutto passa da lì (conteggio del 2026-11-23): 19 file di `ui/` chiamano direttamente il database
  (36 metodi distinti di `db.`), le pagine importano `valuation_model`, `verdict`, `changes`, `lab`, `workflow`,
  `estimates`, `screener`, e le pagine più lunghe (`azienda/valutazione.py` ~600 righe, `portafoglio.py` ~750,
  `ui/rules.py` con `verdict_table`/`load_or_assess`) contengono calcoli, non solo disegno. Prima dell'API quindi si
  sposta quella logica in `modules/services` con i suoi test (fase 1a). Poi l'API espone i servizi così come sono: il
  frontend non calcola nulla.
- **Grafici: le figure Plotly restano in Python** (`fig.to_json()` → `react-plotly.js`), ma il codice dei grafici va
  prima spostato fuori da `ui/common.py`: quel file importa streamlit e fissa il tema (`TERMINAL`, `SERIES`, `TOKENS`)
  una volta per tutto il processo leggendo un file di preferenze. In un modulo nuovo senza streamlit (`modules/charts.py`)
  con il tema come parametro, ogni utente ha il suo aspetto e la fase 7 può togliere streamlit.
- **Numeri: l'API manda valori grezzi + tipo** (`{"v": 0.123, "kind": "pct"}`); il frontend formatta con una porta TS di
  `ui/components.fmt`. Una sola fonte di verità: un file JSON di casi (valore, tipo → testo atteso) generato dai test
  Python e riusato dai test TypeScript, così le due versioni non possono divergere.
- **Esportazioni (Excel, PDF) restano in Python**: endpoint che restituiscono il file.
- **Aggiornamento dati**: `updater` resta un processo separato; l'API espone avvio e stato (`aggiornamento_stato.json`).
- **Componenti**: Radix UI (accessibili, senza stile) + CSS con gli stessi token di `ui/common.py` (TOKENS_LIGHT/DARK,
  RADII, FONTS). Niente libreria di componenti "con un look" da combattere.

## 3. Decisioni da prendere PRIMA di scrivere codice

Cambiano la quantità di lavoro più di qualunque scelta tecnica.

1. **Come si vende** (la più importante)
   - A. *App desktop con licenza* (ogni cliente ha i suoi dati sul suo Mac): niente server, niente multi-utente, niente
     login. **Con A la riscrittura NON serve per vendere**: Streamlit regge un utente per Mac e l'aspetto è già curato. I
     veri ostacoli alla vendita sono altri e non dipendono dall'interfaccia: licenza dei dati (decisione 2), parere legale
     (decisione 3), firma e notarizzazione dell'app Mac (certificato Apple Developer, ~1 settimana). Con A la riscrittura
     è un investimento facoltativo in velocità e rifinitura (~12-16 settimane), da cominciare dalla scheda azienda.
   - B. *Servizio online (SaaS)*: login, dati utente separati per cliente, Postgres, hosting, aggiornamento serale su
     server, pagamenti. **Qui la riscrittura è necessaria** (~15-20 settimane) e ci sono costi fissi mensili.
   - Consiglio: **A per arrivare presto alla vendita** (dati, legale, notarizzazione prima di tutto), e la riscrittura
     solo se si punta a B o se i clienti di A la chiedono. Se la si fa, l'API va scritta già "per utente" (fase 4) così
     un passaggio a B non rifà nulla.
2. **Fornitore dei dati** (punto 1 della valutazione di solidità): Yahoo non è utilizzabile in un prodotto venduto. Con A
   si può valutare "porta la tua chiave" (il cliente inserisce la chiave di un fornitore a pagamento); con B serve una
   licenza di redistribuzione. Da decidere prima della fase 5, non blocca le fasi 1-3.
3. **Parere legale su "Candidata all'acquisto"** (consulenza/ricerca regolata): può cambiare testi e funzioni
   dell'interfaccia. Da chiedere in parallelo alla fase 1.
4. **Nuove dipendenze** (serve il tuo sì): Python `fastapi`, `uvicorn`, `pydantic`; Node.js + `react`, `typescript`,
   `vite`, `@radix-ui/*`, `@tanstack/react-query`, `@tanstack/react-table`, `react-plotly.js`, `playwright` (test).

## 4. Fasi

Metodo "a sostituzione graduale": Streamlit resta funzionante finché ogni pagina nuova non è alla pari; nessun giorno
senza un prodotto usabile. Ogni fase ha un criterio di "fatto" verificabile.

| Fase | Contenuto | Fatto quando | Stima |
|---|---|---|---|
| **0. Decisioni** | punti 1-4 del §3 | modello di vendita e dipendenze approvati | 1-3 giorni (tuoi) |
| **1a. Logica fuori dalle pagine** | spostare in `modules/services` i calcoli e gli accessi diretti al database oggi in `ui/` (19 file, 36 metodi di `db.`; in particolare `azienda/valutazione.py`, `portafoglio.py`, `ui/rules.py`); spostare i grafici in `modules/charts.py` senza streamlit, tema come parametro | 0 chiamate a `db.` e 0 calcoli in `ui/pages` (solo disegno); test dei servizi nuovi; Streamlit identico a prima (test AppTest verdi) | 1,5-2 sett. |
| **1. API** | FastAPI in `api/`: endpoint per le pagine del nucleo (scheda, verdetto, bilanci, DCF, concorrenti, documenti, esplora, confronta, mie aziende, diario, metodo); schemi pydantic; scritture dell'utente (segui, note, soglie, valutazioni); avvio/stato aggiornamento; esportazioni | test con `TestClient` su copia della demo per ogni endpoint; risposte uguali ai dati che la pagina Streamlit mostra oggi (stesso `DashboardData`) | 1,5-2 sett. |
| **2. Fondamenta del frontend** | progetto Vite in `web/`; design system (token, tipografia, schede, tabelle, distintivi, pulsanti, controllo segmentato, campi) come componenti; cornice (menu, ricerca/comandi con `commands.parse` via API, scorciatoie F1-F8, barra di stato, aspetto chiaro/scuro/sistema per utente); formattazione `fmt` in TS con i casi condivisi | pagina "catalogo componenti" in chiaro e scuro; test dei formati verdi; FastAPI serve il frontend compilato | 1 sett. |
| **3. Nucleo** | Scheda azienda (verdetto e prove, cosa è cambiato, FA, GP, DCF a leve con risultato sempre in vista, RV, CF, ERN/DVD/HDS/NOTE/DES, esporta), Esplora, Confronta, Le mie aziende, Oggi, Diario, Il mio metodo | ogni pagina alla pari con Streamlit (lista di controllo per funzione, da `commands.FUNCTIONS` e `docs/priorita_funzioni.md`); test end-to-end Playwright sul percorso "apro → cerco → capisco se comprare"; scheda < 1 s | 3-4 sett. |
| **4. Dati per utente** | le tabelle dell'utente (note, fair value, valutazioni, seguite, soglie, portafoglio, diario, strategie, ricerche salvate, pipeline, preferenze) con `user_id`; preferenze dal file al database; con A un solo utente locale, con B login | nessuna scrittura dell'utente fuori dal database; test che due utenti non vedono i dati l'uno dell'altro | 1 sett. (A) / 2-3 sett. con login (B) |
| **5. Supporto** | Portafoglio ed ETF, Strategie e Grandi investitori, Backtest, Laboratorio, Notizie, Pipeline, Inserisci dati, Qualità dei dati, Strategia automatica | alla pari con Streamlit, come in fase 3 | 2-3 sett. |
| **6. Distribuzione** | A: app Mac con FastAPI + frontend compilato in pywebview, firma con certificato Apple Developer e notarizzazione, aggiornamenti automatici. B: container, hosting, Postgres, lavori serali, pagamenti, backup | A: installazione pulita su un Mac senza avvisi di Gatekeeper. B: ambiente di prova online con due utenti | 1-2 sett. (A) / 3-4 sett. (B) |
| **7. Ritiro di Streamlit** | togliere `app.py`, `ui/`, i test AppTest, `streamlit` dai requisiti | 0 riferimenti a streamlit; suite verde | 2-3 giorni |

Totale indicativo: **A ≈ 12-16 settimane**, **B ≈ 15-20 settimane** di lavoro concentrato. La fase 1a è utile anche
senza riscrittura (pagine più semplici, logica testata) ed è il rischio di stima più grande: misurarla per prima. Le stime valgono con il motore
invariato; ogni nuova funzione chiesta nel frattempo si aggiunge.

## 5. Cosa NON cambia

- `modules/` (motore, dati, giudizio, valutazione, backtest) e i suoi test: l'API lo chiama così com'è.
- Regole di progetto: point-in-time, nessun numero magico fuori da `config.yaml`, determinismo del backtest, niente
  broker, il giudizio non è un segnale provato (la nota resta visibile nel frontend nuovo).
- Testi in italiano e formati italiani (`fmt`).

## 6. Rischi e come li gestisco

| Rischio | Contromisura |
|---|---|
| La riscrittura si allunga e per settimane non c'è nulla da mostrare | sostituzione graduale: Streamlit resta; la scheda azienda è la prima pagina nuova completa (fine fase 3, ~5 settimane) |
| Logica che scivola nel frontend e diverge dal motore | il frontend non calcola nulla: solo `fmt` (con casi condivisi) e disegno; tutto il resto dall'API |
| Prestazioni (oggi `DashboardData` si carica in cache per processo) | un solo processo API con la stessa cache, chiave = ultima esecuzione (`runs`) come oggi; misurare la scheda < 1 s |
| Doppia manutenzione durante la transizione | nessuna funzione nuova in Streamlit dopo l'inizio della fase 3 |
| Dati di Yahoo in un prodotto venduto | decisione 2 del §3 prima della fase 6; il codice dei dati è già isolato in `data_ingestor`, `yahoo_fundamentals`, `quotes`, `estimates`, `etf` |

## 7. Primo passo concreto (dopo le decisioni)

Con A: decisioni 2 e 3 e la notarizzazione, indipendenti dall'interfaccia. Se si procede con la riscrittura: fasi 1a e 1
limitate a UNA pagina, la scheda azienda (logica di `azienda/` nei servizi, poi): endpoint `GET /api/companies/{ticker}` (testata + verdetto + cosa è
cambiato), `GET /api/companies/{ticker}/financials`, `GET /api/companies/{ticker}/valuation`, `POST /api/watchlist`,
con test. Serve a misurare la velocità reale prima di stimare il resto con più precisione.

## 8. Avanzamento

- 2026-11-23, fase 1a, prima fetta (scheda azienda): soglie di prezzo, avvisi, versione dei dati dell'utente
  (`WatchService`), giudizio e "cosa è cambiato" (`modules/services/judgment.py`), stime degli analisti con il quadro
  già calcolato (`modules/services/estimates.py`). `tests/test_architettura_ui.py` conta ciò che resta in
  `ui/pages/azienda` (elenco `ALLOWED`, solo in calo): fase della pipeline in `scheda.py`, valutazione a leve in
  `valutazione.py`. Poi: grafici e tema in `modules/charts.py` / `modules/theme.py` (dopo il commit della grafica).
- 2026-11-23, fase 1, prima fetta (dipendenze approvate dall'utente: FastAPI, uvicorn, httpx; React per la fase 2):
  `api/` con `/api/v1/health`, ricerca aziende, scheda (testata + giudizio con le prove + cosa è cambiato + avvisi),
  bilanci annuali/trimestrali con fonti e link, valutazione a leve (GET con le ipotesi di partenza, POST con leve e
  probabilità del client, niente salvataggio), stime salvate, aziende seguite e soglie di prezzo (scritture).
  Servizi nuovi: `company_header`, `lever_valuation`. Apertura dei dati senza Streamlit: `modules/app_context.py`.
  Tempi sulla demo: scheda 0,07 s (mediana), 0,25 s (massimo); valutazione 0,01 s. Test: `tests/test_api.py`.
  Da fare: `ui/common.data()` su `app_context` e la testata di `scheda.py` su `company_header` (dopo il commit della
  grafica); poi le altre pagine del nucleo; fase 2 (frontend React in `web/`).
- 2026-11-23, fase 2 (fondamenta): `web/` con React 18, TypeScript 5.6, Vite 6, TanStack Query 5, React Router 7, vitest 5
  (npm audit: 0 vulnerabilità; react-router 6 e vitest 3 ne avevano, aggiornati). Design system in `web/src/styles`
  (token generati da Python, stesso stile Apple), cornice (marchio, ricerca con tastiera e "/", pagine a segmenti,
  aspetto), componenti (`Card`, `Badge`, `Notice`, `Empty`, `Skeleton`, `Num`, `Segmented`, testata, giudizio, pilastri),
  pagine: elenco aziende, scheda (testata + giudizio + prove per pilastro), catalogo `/componenti`. `fmt.ts` = porta di
  `modules/formats.fmt` (arrotondamento come round() di Python), verificata su 1.204 casi generati. L'API serve
  `web/dist` (indirizzi dell'app -> index.html, mai fuori dalla cartella). Verificata nel browser in chiaro, scuro e a
  375 px. Da fare: funzioni della scheda (bilanci con grafici, DCF a leve con le leve modificabili, concorrenti,
  documenti), poi Esplora, Confronta, Le mie aziende, Oggi; test end-to-end Playwright sul percorso principale.
- 2026-11-23, scheda web, funzione Valutazione (`web/src/pages/Valuation.tsx`, `?fn=valutazione`): leve per scenario
  modificabili (virgola o punto), risultato ricalcolato dal motore mentre si scrive (POST, solo le celle cambiate: le
  altre restano al valore esatto del motore), controlli di coerenza spiegati, leve che contano di più, istogramma della
  simulazione, "è plausibile?", anno per anno, "Salva nel diario" (POST `/valuation/save`: diario + fair value).
  Servizio `lever_valuation` esteso (etichette delle leve, istogramma, confronto col settore, tabella del caso base) e
  `save_lever_valuation`; `explain_check`, `outside_range_note`, `EDIT_LEVERS` ora in modules/services/valuation.py
  (la pagina Streamlit li importa da lì), `it_text` in modules/formats.py. Restano solo nella pagina Streamlit: costo
  del capitale nel dettaglio, DCF della strategia con la tabella di sensibilità, multipli nel tempo, test LBO.
- 2026-11-23, scheda web, funzione Bilanci (`web/src/pages/Financials.tsx`, `?fn=bilanci&periodo=...`): esercizi, ultimi
  12 mesi a ogni trimestre, trimestri singoli; quattro grafici (ricavi/utili/FCF, margini, debito/cassa/patrimonio,
  soldi restituiti) e azioni in circolazione con asse da zero; tabella degli ultimi 12 periodi con link al 10-K/10-Q e
  segni sulle fonti di riserva. Grafici in SVG fatti in casa (`web/src/components/charts.tsx`: zero sempre visibile,
  legenda, dettaglio al passaggio del mouse e da tastiera), nessuna libreria nuova. Logica in
  `modules/services/statements.py` (etichette dei periodi, frase sulle azioni, segni delle fonti), importata anche da
  ui/pages/azienda/bilanci.py. API: `/financials?view=annual|quarterly_ttm|quarterly_single`. Resta solo in Streamlit:
  l'archivio completo delle voci SEC.
- 2026-11-23, pagina web "Le mie aziende" (`web/src/pages/MyCompanies.tsx`, `/mie-aziende`, prima voce del menu): avvisi
  da guardare raggruppati per azienda con "segna come visti", tabella ordinabile (giudizio, pilastri, prezzo,
  rendimenti, fair value e soglia con i margini, avvisi, togli), aggiungi da ricerca, "Per cominciare" con le prime
  Candidate quando la lista è vuota, storico degli avvisi per stato. Servizi nuovi in `WatchService`:
  `verdict_overview` (giudizi in tabella, dallo storico serale quando valgono, in memoria sul contesto finché non
  cambiano i fair value), `my_companies`, `alert_history`; colonne dei pilastri con prefisso `pillar_` (il pilastro
  "price" sovrascriveva il prezzo: trovato nel test). Toni e testi degli avvisi in `modules/alerts.py`
  (`ALERT_TONES`, `alert_tone`, `alert_message_it`; ui/components e ui/pages/mie_aziende li importano). API:
  `/my-companies`, `/alerts?state=`, `POST /alerts/seen`. Restano solo in Streamlit: editor delle soglie in tabella
  (nella scheda c'è), scelta delle colonne, confronto delle selezionate.
- 2026-11-23, pagina web "Esplora" (`web/src/pages/Explore.tsx`, `/esplora`): universo, ricerche pronte e salvate
  (carica, elimina, salva), settori, giudizio con i conteggi, condizioni sulle metriche del catalogo con le loro unità
  (%, mld $, x; "tra i migliori" solo per metriche con un verso), punteggi minimi dei pilastri, ricerca nei risultati,
  ordinamento, scelta delle colonne, stella per seguire. Il filtro lo fa il motore (`modules/services/explore.py`,
  stesse regole di modules/screener.py); `rule_unit`, testi dei pilastri, nota sul giudizio e colonne predefinite ora
  vivono lì (ui/rules.py e ui/pages/esplora.py li importano; i multipli ora hanno l'unità "x" anche in Streamlit).
  API: `/explore/catalog`, `POST /explore` (al massimo 600 righe), `PUT/DELETE /screens/{nome}`. Resta solo in
  Streamlit: selezione di più righe per il confronto (Confronta non è ancora portata).
- 2026-11-23, pagina web "Confronta" (`web/src/pages/Compare.tsx`, `/confronta?t=A,B&anni=5`): da 2 a 5 aziende,
  ognuna con lo stesso colore in tabelle e grafici; giudizio e pilastri affiancati, metriche del catalogo per gruppo
  con la migliore della riga, quattro grafici nel tempo (prezzo normalizzato con periodo 1/3/5/10 anni, ricavi,
  margine operativo, P/E). Si arriva da Esplora (caselle per scegliere fino a 5 aziende → "Confronta le N") e da Le
  mie aziende ("Confronta tutte"). Servizio `modules/services/compare.py` (`compare`, `compare_metrics` che non va mai
  in eccezione, `pillar_grade`, multipli nel tempo in memoria sul contesto); ui/pages/confronta.py e ui/rules.py lo
  importano. API `GET /compare?t=...&years=`. L'API ora risponde 404 ai file inesistenti (es. /favicon.ico) invece
  di restituire la pagina.
- 2026-11-23, scheda web, funzioni "Concorrenti" e "Notizie e documenti" (`web/src/pages/PeersDocs.tsx`,
  `?fn=concorrenti` / `?fn=documenti`): le più grandi del gruppo con l'azienda evidenziata e il link "Confronta con le
  prime N", l'azienda contro le mediane con "meglio/peggio" secondo il verso di ogni metrica; ultime notizie che citano
  l'azienda e depositi SEC (10-K, 10-Q, 8-K con ⚠ per le voci da guardare subito, link alla SEC). Servizi
  `company_peers` e `company_documents` in `CompanyService`; le notizie si leggono SOLO dalla copia salvata dei feed
  (`news.cache_dir`), l'API non scarica nulla. API: `/companies/{t}/peers`, `/companies/{t}/documents`.
  Scheda web ora con cinque funzioni: Prove, Bilanci, Valutazione, Concorrenti, Notizie e documenti. Restano solo in
  Streamlit: Grafico (GP), ERN, DVD, HDS, NOTE, DES, AUTO, EE, export Excel/PDF, soglie e pipeline dalla scheda.
- 2026-11-23, pagina web "Oggi" (`web/src/pages/TodayPage.tsx`, ora la pagina iniziale `/`; l'elenco delle aziende
  passa a `/aziende`): le mie aziende (seguite + portafoglio + con soglia) con prezzo, ultima seduta, 1 anno, margine
  sul fair value e soglia (colonne nascoste se vuote) e giudizio; avvisi con i non visti in cima, movimenti di prezzo
  in un riquadro richiuso, "segna tutti come visti"; notizie (le mie aziende prima) e documenti ufficiali (solo le mie
  a scelta); S&P 500 in salita e in discesa; acquisti degli insider; borse mondiali. Servizio
  `modules/services/today.py` (solo dati salvati: prezzi = ultime chiusure del database). `ALERT_PRIORITY` e
  `PRICE_MOVES` ora in `modules/alerts.py`. API `GET /today?only_mine=`. Menu della nuova interfaccia: Oggi, Le mie
  aziende, Scheda, Esplora, Confronta. Non portati: striscia dei mercati e mappa dell'indice (servono quotazioni dal
  vivo: in un servizio online vanno da un fornitore con licenza, decisione 2).
- 2026-11-23, scheda web, funzione "Grafico" (`web/src/pages/PricePanel.tsx`, `?fn=grafico`): periodi 1M-MAX, Auto /
  Candele / Linea (candele settimanali o mensili sui periodi lunghi, linea sulle chiusure settimanali oltre 800
  punti), medie a 50 e 200 sedute, scala logaritmica, confronto con il benchmark in base 100, depositi dei 10-K,
  operazioni del backtest, tue linee di riferimento (fair value, soglie, scenari; quelle fuori scala dette nella nota),
  volumi colorati con la loro media, sintesi del periodo, drawdown dal massimo; dettaglio di ogni barra al passaggio
  del mouse e con le frecce. SVG fatto in casa, nessuna libreria. Logica in `modules/services/price_chart.py`
  (ui/pages/azienda/prezzo.py la importa). API `GET /companies/{t}/price?period=&style=&bench=`. L'API ora manda
  `Cache-Control: no-cache` per la pagina e cache lunga per i file con l'impronta (`assets/`): prima, dopo un nuovo
  build, il browser teneva la pagina vecchia (trovato nella prova).
- 2026-11-23, pagina web "Diario" (`web/src/pages/Journal.tsx`, `/diario`): riepilogo (decisioni, giudicabili, quante
  volte avevi ragione contro l'indice dopo 3 mesi, revisioni scadute), nuova decisione (azienda, decisione, perché,
  cosa farebbe cambiare idea, prezzo vuoto = ultima chiusura, revisione vuota = fra 6 mesi), decisioni da rivedere con
  giudizio e nota, tutte le decisioni con esito, eliminazione con conferma. Servizi in `PortfolioService`
  (`journal_page`, `add_journal_decision`, `review_journal_decision`, `delete_journal_decision`, `journal_benchmark`):
  i controlli (perché obbligatorio, decisione e giudizio ammessi, prezzo positivo) ora stanno nel motore e la pagina
  Streamlit li usa; `VERDICTS` in `modules/journal.py`. API: `GET/POST /journal`, `POST /journal/{id}/review`,
  `DELETE /journal/{id}`.
- 2026-11-23, pagina web "Il mio metodo" (`web/src/pages/Method.tsx`, `/metodo`): profilo (stili, orizzonte, numero di
  aziende, principi, cosa fa scartare, quando vendo), metriche personali con l'anteprima di oggi mentre si scrive
  (quante aziende, mediana, 10°-90° percentile, le migliori) e l'elenco cliccabile dei nomi da usare, checklist
  (aggiungi, modifica con peso, togli). Servizi nuovi in `MethodService`: `method_page`, `formula_preview`,
  `create_custom_metric`, `save_checklist_question`, `remove_checklist_question` (usati anche dalla pagina Streamlit).
  Errore di sintassi delle formule ora in italiano ("Formula incompleta o scritta male…", prima "invalid syntax").
  API: `GET /method`, `PUT /method/profile`, `POST /method/metrics/preview`, `POST/DELETE /method/metrics`,
  `POST/PUT/DELETE /method/checklist`. ATTENZIONE per la fase 4 (più utenti): le metriche personali sono registrate
  nel catalogo globale `metrics.CUSTOM` (stato di processo): con più utenti vanno legate all'utente.
- 2026-11-23, pagina web "Portafoglio" (`web/src/pages/Portfolio.tsx`, `/portafoglio`): riepilogo in euro (valore,
  investito, guadagno, contro SPY, realizzato, dividendi netti), schede Posizioni (con il giudizio) / Operazioni (con
  elimina) / Dividendi e tasse (stima, non consulenza fiscale), andamento in euro contro SPY, composizione per settore
  e valuta, modulo "+ Operazione" (acquisto, vendita, dividendo; valuta indovinata dal simbolo, cambio vuoto = chiusura
  del giorno, il perché va nel Diario). Servizi nuovi in `PortfolioService`: `portfolio_page`, `add_transaction`,
  `delete_transaction` (controlli: quantità posseduta per le vendite, valuta nota; usati anche dalla pagina
  Streamlit). API: `GET /portfolio`, `POST /portfolio/transactions`, `DELETE /portfolio/transactions/{id}`. Restano
  solo in Streamlit: esposizione vera dentro gli ETF e la modifica di un'operazione (oggi: elimina e reinserisci).
- 2026-11-23, pagina web "Notizie" (`web/src/pages/News.tsx`, `/notizie`, `?sez=x` / `?sez=8k`): Flusso (tutte, le mie
  aziende, un ticker; fonti, lingua, testo nel titolo; ● per le notizie arrivate dal giro precedente; si rilegge ogni
  30 s; righe impilate su telefono), X (account scelti dall'utente, riquadro ufficiale in un iframe isolato senza la
  stessa origine, pagina servita da `/news/x/{account}/embed`), Documenti ufficiali (8-K: periodo, solo le mie, solo ⚠,
  tipo di evento). Servizi nuovi in `NewsService`: `news_stream`, `filings_page`, `x_accounts`, `add_x_account`,
  `remove_x_account`, `x_embed_html`; in `modules/news.py`: `GROUPS`, `filter_table`, `store_path`, `saved_table`
  (usati anche dalla pagina Streamlit, che ora prende le "mie aziende" da `alerts.followed_tickers` come Oggi). API:
  `GET /news`, `GET /news/filings`, `GET/POST /news/x`, `DELETE /news/x/{account}`. LIMITE: il flusso è la copia
  salvata dei feed, scritta solo dal thread della webapp Streamlit (l'API non va in rete): senza l'app classica aperta
  la copia invecchia (la pagina lo dice dopo 30 minuti). Nel servizio online le notizie andranno da un fornitore con
  licenza o da un lavoro sul server (decisione 2). Il browser integrato dell'app blocca gli iframe isolati: il
  riquadro di X va provato in un browser normale.
- 2026-11-23, pagina web "Strategie" (`web/src/pages/Strategies.tsx`, `/strategie?s=<nome>`, `?vista=crea`): scelta
  della strategia (di base, grandi investitori, mie), scheda dell'investitore con la cautela, regole in chiaro, avviso di
  selezione a posteriori, backtest IN CODA SUL SERVER (`modules/jobs.py`: un thread, uno alla volta, in ordine,
  niente doppioni per configurazione + data dei dati; la pagina segue lo stato e si aggiorna da sola), sintesi sulla
  seconda metà, tabella per periodo, valore nel tempo in scala logaritmica con la linea delle due metà, anni, rotazione
  e costi, configurazioni provate e Sharpe corretto per i tentativi, cosa terrebbe oggi, confronto di tutte con
  "Calcola le mancanti"; costruttore (universo, filtri con l'editor di Esplora ora condiviso in
  `web/src/components/rules.tsx`, classifica, portafoglio, anteprima, salva/modifica/duplica/elimina). Servizi nuovi in
  `StrategyService`: `strategies_list`, `strategy_page`, `strategy_holdings_view`, `strategies_overview`,
  `strategy_builder_options`, `strategy_preview`, `save_user_strategy` (nome riservato, doppione, voto della
  checklist vietato), `delete_user_strategy`, `strategy_run_key`, `run_strategy_backtest` (usati anche dalla pagina
  Streamlit per salva, elimina, anteprima e confronto). API: `GET /strategies`, `/strategies/detail?name=`,
  `/strategies/holdings?name=`, `/strategies/overview`, `/strategies/builder`, `POST /strategies/run`,
  `/strategies/run-missing`, `/strategies/preview`, `/strategies/mine`, `DELETE /strategies/mine?name=` (nome come
  parametro: spazi, accenti, "/"). `PeriodChart` ora ha `log` e `marker`. Nel servizio online la coda in memoria va
  sostituita da una coda di lavori vera (più processi, più utenti): l'interfaccia non cambia.
- 2026-11-23, tutte le pagine restanti portate (richiesta dell'utente: "continua con tutto il resto"):
  - Pipeline (`web/src/pages/Pipeline.tsx`, `/pipeline`) e azioni della scheda (`components/CompanyActions.tsx`: Segui,
    Soglie di prezzo, Pipeline con cosa manca per la fase, Esporta Excel/PDF). Servizio `modules/services/pipeline.py`
    (`pipeline_board`, `pipeline_stage`, `set_pipeline_stage`, `remove_from_pipeline`; usato anche da Streamlit: tolte
    tre voci da ALLOWED in tests/test_architettura_ui.py), esportazioni in `ReportService` (`export_excel`,
    `export_pdf`). API: `/pipeline`, `/companies/{t}/pipeline` (GET/PUT/DELETE), `/companies/{t}/export.xlsx|.pdf`;
    la testata della scheda ha `followed`.
  - Scheda, menu "Altro" (`web/src/pages/CompanyMore.tsx`): Trimestri (bilanci a trimestri singoli), Dividendi e
    riacquisti, Insider e fondi, La mia ricerca (tesi, rischi, revisione, voti della checklist, appunti, diario delle
    valutazioni con il grafico prezzo/valore), Tutte le metriche, Stime degli analisti (con import CSV: `POST
    /estimates/import`; lo scarico da Yahoo resta solo nella webapp classica), Strategia automatica. Servizio
    `modules/services/dossier.py`; API `/companies/{t}/metrics|dividends|ownership|research|auto`.
  - Backtest (`Backtest.tsx`), Laboratorio (`Lab.tsx`: esperimenti nella stessa coda dei backtest, `/lab/*`), Strategia
    automatica (`AutoStrategy.tsx`, `/auto-strategy`): servizio `modules/services/lab.py`. I calcoli di main.py
    (backtest della strategia originale, ricerca dei segnali e dei fattori, analisi) NON si lanciano dall'API: la pagina
    mostra il comando da terminale.
  - Grandi investitori (`Funds.tsx`, `/funds`), Qualità dei dati (`Quality.tsx`, `/quality`), Inserisci dati
    (`DataEntry.tsx`: tabella voci x periodi modificabile, valori inseriti con eliminazione, aziende fuori indice;
    `/manual/{t}`, `/custom-companies`): servizio `modules/services/data_admin.py`.
  - Barra in alto: Oggi, Le mie aziende, Scheda, Esplora, Confronta, Portafoglio, Notizie + menu "Altro" (Il mio
    lavoro: Pipeline, Diario, Metodo; Strategie e ricerca: Strategie, Backtest, Laboratorio, Strategia automatica,
    Grandi investitori; Dati: Inserisci dati, Qualità dei dati).
  Restano solo nella webapp classica: aggiornamento dei dati e aggiornamento automatico (menu Dati), esposizione vera
  dentro gli ETF, modifica di un'operazione del portafoglio, scarico delle stime da Yahoo, striscia dei mercati e mappa
  dell'indice (quotazioni dal vivo), lancio dei calcoli di main.py. Sono le funzioni che vanno in rete: arrivano con
  il login e i lavori sul server (fase 4) o con un fornitore di dati (decisione 2).
