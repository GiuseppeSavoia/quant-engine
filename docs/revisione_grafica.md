# Revisione grafica e di usabilità — QUANT_ENGINE (2026-11-13)

Scopo del prodotto: **capire se un'azienda è da comprare o no**. L'aspetto "terminale" (nero, ambra, monospazio,
tasti funzione, barra dei comandi) resta; questa specifica lo rende leggibile e utilizzabile da un investitore
privato. Nessun nome, logo o schermata di prodotti di terzi.

## 0. Come è stata fatta la revisione (cosa ho visto davvero)

- Server demo avviato su una **copia del commit HEAD** (`git archive` + copia di `data/demo.db` nella scratchpad),
  porta 8520: il server avviato sul progetto vivo si è rotto a metà revisione (`ImportError: _price_tab` da
  `ui/pages/azienda/prezzo.py`, che un altro agente sta riscrivendo). Quindi quanto descritto qui è **lo stato
  del commit b3baa94**, non le modifiche in corso (candele, verdetto, changes).
- Viste davvero con screenshot (pannello browser, 1440x900 e 1280x720): Monitor, Le mie aziende (vuota e con 5
  aziende), Esplora, Confronta (3 aziende), Notizie, Portafoglio, Diario, Il mio metodo, Inserisci dati, Grandi
  investitori, Strategie, Strategia automatica, Backtest, Laboratorio, Qualità dei dati; scheda azienda con tutte
  le 11 funzioni (DES, FA, ERN, GP, RV, DCF, DVD, HDS, CF, NOTE, AUTO). Aspetto chiaro visto su Monitor e Scheda
  (cambiando il file delle preferenze **della copia**, non quello del progetto).
- Misurato con JavaScript nella pagina: dimensioni dei caratteri effettive, larghezze delle tabelle, stato attivo
  dei tasti F. Contrasti calcolati con la formula WCAG 2.x (script in scratchpad `contrast.py`).
- Limiti: gli screenshot del pannello sono ridotti (800 px di larghezza), quindi i dettagli fini li ho verificati
  dal DOM; dati demo (insider, 13F, 8-K, trimestri assenti: ho visto solo gli stati vuoti di HDS, CF, Fondi,
  Notizie, ERN); tempi di caricamento valutati a occhio (nessun indicatore misurabile in modo affidabile).
- Nota tecnica: il pannello browser è condiviso con altri agenti (a metà revisione la mia scheda è stata portata
  su `localhost:8518`); per le verifiche visive future conviene una scheda propria.

---

## 1. Problemi, ordinati per gravità

Gravità: **A** = impedisce o falsa il percorso principale; **B** = rallenta o confonde spesso; **C** = rifinitura.

### A1 — Nessuna risposta alla domanda "la compro?" dove l'utente arriva
- **Pagina**: Scheda azienda.
- **Cosa si vede**: dopo selectbox, titolo, didascalia, testata, didascalia di capitalizzazione, menu a 11 voci e
  un'altra didascalia, la funzione predefinita DES mostra ~70 metriche in 10 tabelle con barre ambra tutte uguali.
  L'unico giudizio esplicito ("BUY", confidenza 0,67) è nascosto nella funzione **11 AUTO**, scritto con nomi di
  colonna grezzi (`run_id`, `as_of`, `reasoning`) e numeri a 4 decimali.
- **Perché è un problema**: il percorso "apro → cerco → capisco" si ferma alla terza tappa; l'utente deve fare la
  sintesi da solo tra decine di numeri. Con il Verdetto in arrivo ci sarebbero DUE giudizi (verdetto a pilastri e
  BUY/SKIP della strategia automatica) potenzialmente in contrasto.
- **Correzione**: Verdetto in cima alla scheda (§4.2), subito sotto la testata, prima del menu delle funzioni.
  AUTO resta come funzione secondaria, rinominata "Strategia automatica (sperimentale)", e il suo BUY/SKIP NON
  compare nella testata né nel Verdetto (eventualmente una riga "La strategia automatica dice: BUY" tra le prove
  di contesto, con il distintivo "sperimentale").

### A2 — Il pannello "Borse mondiali" si sovrappone a "Le mie aziende"
- **Pagina**: Monitor, sia a 1440 sia (peggio) a 1280.
- **Cosa si vede**: la tabella delle borse è larga 797 px in un riquadro di 745 px (misurato): la colonna OGGI
  esce dal pannello e copre la colonna TICKER delle "mie aziende" ("−0.35%AAPL"). A 1280 la sovrapposizione copre
  anche ULTIMO.
- **Perché**: le uniche righe che contano davvero per l'utente (le sue aziende) diventano illeggibili.
- **Correzione**: `table.qe-dense { table-layout: fixed; }` + contenitore con `overflow-x:auto` (vedi §3.5);
  togliere dal pannello le colonne "Orari" e "apre tra…" (vanno nel tooltip `title=`); soprattutto spostare le borse
  fuori dalla prima schermata (§4.1).

### A3 — Il risultato della valutazione è due schermate sotto gli input, e mostra "None"
- **Pagina**: Scheda → 6 DCF.
- **Cosa si vede**: prima i due interruttori, il testo "punto di partenza", il costo del capitale, la storia,
  la tabella delle 11 leve × 3 scenari; solo dopo ~1,5 schermate i valori Pessimista/Base/Ottimista/Atteso.
  La tabella "È plausibile?" mostra `None` in 4 colonne e la colonna Giudizio tronca ("settore troppo piccolo
  per un …"); le leve sono in % nella tabella (23.54) e in frazione nella plausibilità (0.285).
- **Perché**: la risposta (quanto vale, quanto sconto) è sepolta; "None" sembra un errore del programma.
- **Correzione**: in cima alla funzione una riga di risultato (valore atteso per azione, intervallo 10°–90°
  percentile, margine sul prezzo, crescita implicita nel prezzo) con il componente "riga di testata" (§3.6); sotto,
  le leve. Mai `None`: convertire a "–" (formattare in stringa prima di `st.dataframe`, o `column_config` con
  `NumberColumn` e NaN). Percentuali sempre come percentuali (28,5%). Se il settore è troppo piccolo, una sola riga
  di stato vuoto al posto della tabella.

### A4 — Testo troppo piccolo in tutta l'interfaccia
- **Pagine**: tutte.
- **Cosa si vede (misurato)**: tasti F 10,4 px; intestazioni delle tabelle fitte 11 px; celle 12 px; barra di
  stato 11,5 px; didascalie 12,25 px; menu in alto 10,9 px; etichette di `st.metric` 10 px. Font monospazio (più
  largo, quindi a parità di px sembra ancora più piccolo nelle tabelle strette).
- **Perché**: l'utente legge per minuti, non dà un'occhiata da trader; sotto 12 px su nero con monospazio
  affatica, specie in maiuscolo.
- **Correzione**: scala tipografica §3.1 con minimo 12 px ovunque, 13 px per i dati, 14 px per il testo.

### A5 — "Dove sono?" non ha risposta nell'aspetto terminale
- **Pagine**: tutte.
- **Cosa si vede**: i tasti F sono tutti ambra pieni, nessuno evidenziato (`aria-current` assente, il CSS
  sovrascrive lo stato attivo che nell'aspetto chiaro invece si vede). Il titolo della pagina (h1 ambra 21 px) è
  lontano dai tasti. La barra dei comandi e i tasti scorrono via con la pagina; resta solo il menu in alto, piccolo.
- **Correzione**: tasto F attivo in negativo (fondo nero, bordo e testo ambra) o sottolineato bianco; barra comandi
  + tasti F fissi in alto (`position: sticky`) sotto il menu; "briciola" a sinistra del titolo (es. `AZIENDE ›
  SCHEDA › AAPL › DCF`).

### A6 — Il comando SEGUI porta al Monitor e il messaggio di conferma si perde
- **Pagina**: barra dei comandi, da "Le mie aziende".
- **Cosa si vede**: `SEGUI AAPL MSFT…` da Le mie aziende salta al Monitor; il `st.success` è disegnato prima di
  `st.switch_page` e non si vede.
- **Correzione** (`ui/shell.py`): con `cmd.follow` restare sulla pagina corrente (non impostare `qe_goto` se la
  pagina del comando è la home); mostrare la conferma con `st.toast` (sopravvive al cambio pagina se emessa dopo).

### A7 — Valori mancanti scritti `None`, `-0.0x`, decimali grezzi
- **Pagine**: Le mie aziende (Tuo fair value, Margine sul tuo FV = `None`), DCF plausibilità, Strategie
  (Punteggio = `None`), AUTO (0.6909, 138.5858), RV (`-0.0x` per debito netto/EBITDA), Qualità ("Choose options" in
  inglese nel multiselect).
- **Perché**: sembrano errori; rovinano la fiducia nei numeri, che è tutto il prodotto.
- **Correzione**: regola unica "mancante = –" (en dash, grigio disattivato), arrotondamento per tipo (§3.4),
  `placeholder=` in italiano su ogni `st.multiselect`/`st.selectbox`.

### A8 — Due ricerche aziende diverse, nessuna davvero comoda
- **Pagine**: barra dei comandi (218 px, testo ambra maiuscolo, nessun suggerimento) e selectbox "Azienda" in
  cima alla scheda (larga tutta la pagina, prima cosa che si vede nella scheda).
- **Perché**: il passo "cerco un'azienda" è il secondo del percorso principale: chi non conosce i comandi non sa
  che può scrivere "apple"; la selectbox ruba lo spazio più prezioso della scheda.
- **Correzione**: una sola ricerca nella barra in alto, larga (≥ 360 px), placeholder "Cerca azienda o comando —
  es. Apple, AAPL, AAPL DCF" (non maiuscolo forzato: `text-transform` solo sul ticker riconosciuto). Nella scheda
  togliere la selectbox grande; al suo posto, nella riga identità, una selectbox compatta "Cambia" larga 220 px
  oppure i ticker recenti come pulsanti piccoli (`st.pills`).

### B1 — Densità sbagliata: troppo dove non serve, troppo poco dove serve
- Monitor: prima schermata = striscia di 12 mercati + borse mondiali (12 righe) + movimenti dell'S&P 500;
  le mie aziende in un riquadro stretto a destra. Mappa a 520 px di altezza.
- Scheda DES: tabelle `st.dataframe` ad altezza fissa con righe vuote in fondo (Valutazione 16 righe, Crescita
  7 righe + 3 vuote); nomi metrica troncati ("P/E rispetto alla sua stori", "Quota degli utili pagata in").
- Confronta: griglia a due colonne con altezze diverse → buchi neri di ~300 px sotto "Dimensione".
- Il mio giudizio (NOTE): 5 slider larghi tutta la pagina per un voto 1-5.
- **Correzione**: strutture §4; tabelle fitte HTML (`dense_table`) per le tabelle in sola lettura con meno di ~40
  righe, altezza naturale; Confronta in una colonna; voti 1-5 con `st.segmented_control` o `st.feedback("stars")`
  in due colonne.

### B2 — Due stili di tabella che non si somigliano
- 37 `st.dataframe` contro 9 `dense_table`. I dataframe hanno righe alte 35 px, intestazioni ambra ma corpo grigio,
  barre di ricerca/scaricamento al passaggio del mouse, numeri allineati a destra con formati diversi
  ("1589.3 mld $" senza separatore, "66.3 mld $" altrove); le dense_table hanno righe da 18 px. In "Le mie aziende"
  1 mese / 1 anno sono percentuali nude senza ▲▼ né colore, nel Monitor sono colorate con freccia.
- **Correzione**: regola §3.5 (quando usare quale) e un unico modo di formattare: `ui/common.py` espone
  `fmt_table(df, columns)` che produce stringhe già formattate per entrambi.

### B3 — Gerarchia: l'ambra è dappertutto, quindi non indica niente
- Ambra per: titoli h1-h4, etichette di metriche, intestazioni di tabella, menu in alto, tasti F (pieni),
  pulsanti primari (pieni), chip dei multiselect (pieni, in Esplora 10 chip ambra di fila), expander, barra del
  comando, progress bar della DES (tutte uguali, anche per "25% nel settore").
- **Perché**: l'occhio va sulle chip di Esplora e sui tasti F, non sui dati. Le barre ambra "Nel settore" a 25% e a
  100% hanno lo stesso colore: non si capisce se è buono o cattivo.
- **Correzione**: ruoli colore §3.3: ambra pieno SOLO per l'azione principale della pagina e il tasto F attivo;
  ambra testo per etichette e titoli di pannello; chip dei multiselect grigie con bordo; barre del percentile in
  grigio chiaro con il valore, colore solo agli estremi (≤20% rosso-arancio, ≥80% verde) e sempre con il numero.

### B4 — Maiuscolo dove affatica
- Tutto maiuscolo: titoli di pagina, expander lunghi ("IL COSTO DEL CAPITALE, PASSO PER PASSO: 5.64% (PROPOSTO
  PER GLI ANNI 1-5)", "FAIR VALUE DEL SISTEMA NEL TEMPO (DCF AUTOMATICO, RICALCOLATO OGNI MESE)"), testo digitato
  nella barra comandi, titoli h3, barra di stato intera.
- **Correzione**: regole §3.2.

### B5 — Stati vuoti e messaggi in tre stili, con istruzioni da programmatore
- Riquadri blu `st.info` (CF, HDS, Fondi, Le mie aziende vuota), giallo `st.warning` (ERN), didascalie grigie
  (Monitor). Testi come "oppure `python main.py --insiders`", "config.yaml, sezione superinvestors".
- **Perché**: il blu di Streamlit su nero rompe lo stile; l'utente non usa il terminale per sapere cosa fare.
- **Correzione**: componente "stato vuoto" §3.8: cosa manca, perché, UN pulsante che lo risolve (es. "Scarica
  insider e fondi (5-10 min)" che apre il menu Dati); i dettagli tecnici in un `title=`/expander "Dettagli".

### B6 — Clic persi e nessun segnale di attesa
- Scheda: cliccando una funzione mentre la precedente sta ancora calcolando (DCF, NOTE) il clic viene ignorato
  (mi è successo 3 volte su 11); l'unico indicatore è l'omino "Running" di Streamlit in alto a destra, quasi
  invisibile sul nero. Il menu in alto compare in ritardo dopo un cambio pagina.
- **Correzione**: `st.spinner("Calcolo la valutazione…")` sui blocchi lenti (simulazione DCF, multipli nel tempo,
  storico AUTO) con `show_time=True`; CSS che rende visibile lo stato di esecuzione (§3.9: barra ambra sottile
  animata in cima quando `[data-testid="stStatusWidget"]` è presente); cache `st.cache_data` per i calcoli per
  ticker; sezioni lente divise in `st.fragment` perché il resto della pagina resti cliccabile.

### B7 — A 1280 px la testata e il menu vanno a capo, la striscia dei mercati spreca una riga
- Testata scheda: "VOL 20G" va sulla seconda riga; menu delle 11 funzioni su due righe anche a 1440; striscia
  dei mercati: 9 + 2 celle con un buco; barra di stato tagliata ("●HKEX ●…").
- **Correzione**: testata a due livelli fissi (§3.6) invece di una riga che si spezza a caso; menu funzioni
  raggruppato (§4.2: 6 voci visibili + "Altro"); striscia mercati come una sola riga scorrevole orizzontale
  (`overflow-x:auto; flex-wrap:nowrap`) o max 8 simboli; barra di stato con priorità (le borse si tagliano per
  prime, mai la data dei prezzi).

### B8 — Numeri in due convenzioni
- Tabelle e testo: punto decimale ("60.28", "12.3x"); campi di inserimento (Portafoglio, Diario): virgola
  ("0,0000", "13/11/2026"). Prezzo nel Diario 0,00 con pulsanti +/- che cambiano di 0,01.
- **Correzione**: decisione da far prendere all'utente (vedi §6, domanda 1). Nel frattempo: `number_input` con
  `step` sensati (prezzi `step=None`, nessun +/-: `st.number_input(..., step=None)` non basta → usare
  `st.text_input` validato, oppure CSS che nasconde i pulsanti `[data-testid="stNumberInputStepUp"]`).

### B9 — Grafici che ingannano o distraggono
- FA "Azioni in circolazione": asse da 1100 a 1110 → un calo dell'1% sembra un crollo. Mettere l'asse a zero o
  mostrare la variazione % con etichetta esplicita.
- GP: le operazioni del backtest (acquisti, stop-loss, take-profit) sono accese per impostazione → rumore per chi
  valuta l'azienda. Spente di default.
- Monitor in demo: prezzi Yahoo veri (AAPL 354.88) accanto ai dati simulati della scheda (AAPL 60.28). Solo demo,
  ma lo stesso vale con dati reali: il Monitor mostra la quotazione di oggi, la scheda la chiusura del database,
  senza dirlo. Scrivere la fonte e l'ora accanto al prezzo in testata ("chiusura 02/11" / "15:42, ritardata").

### B10 — Moduli lunghi senza gerarchia
- Portafoglio: 9 campi in una riga, "Cambio (per 1 €)" che va a capo, "Prezzo" a 0,0000; Il mio metodo e NOTE
  con molte aree di testo uguali; più pulsanti "Salva" sulla stessa pagina (NOTE: "Salva la scheda", "Salva i
  voti", "Salva nel diario delle valutazioni").
- **Correzione**: componente modulo §3.10: campi obbligatori prima, opzionali in un expander "Altri dettagli",
  un solo pulsante primario per modulo, conferma con `st.toast` e riga aggiunta evidenziata.

### C — Rifiniture
- C1 Il pulsante "Deploy" e il menu ⋮ di Streamlit nella testata: nasconderli
  (`client.toolbarMode = "minimal"` in config.toml; "Deploy" non serve in un'app locale).
- C2 Didascalia ripetuta sotto il menu funzioni ("AAPL US · DES · Profilo — scrivi DES o un numero…") a ogni
  sezione: toglierla, il suggerimento va nell'AIUTO e nel `help=` del menu.
- C3 "Capitalizzazione 66.3 mld $ · enterprise value…" duplica CAP della testata: unire nella testata.
- C4 "US" accanto a ogni ticker (stile di un terminale) non aiuta un privato: togliere, tenere la borsa solo per
  i titoli non USA (ENI.MI → "ENI · Milano").
- C5 Tasti F: su Mac F1-F12 richiedono fn e alcuni sono presi dal sistema; nell'AIUTO dirlo, e aggiungere
  scorciatoie con lettere (`g m` Monitor, `g s` scheda… oppure Alt+1…9).
- C6 Icone ✓/★/☆ e "⚠" nelle tabelle: ok, ma con `help=` sulla colonna (già fatto per ★).
- C7 Aspetto chiaro: verde `#0ca30c` su bianco 3,35:1 e blu `#2a78d6` sulla barra di stato `#f0efec` 3,84:1 non
  passano AA per testo piccolo (valori corretti in §3.3); i tasti F nel chiaro sono link con icone su due righe,
  diversi dal terminale.
- C8 Bordi dei campi `#333` su nero: 1,66:1 (sotto il 3:1 richiesto per i controlli): i campi si distinguono a
  fatica.
- C9 Esplora: la colonna ★ vuota in testa alla tabella spreca spazio; le 10 chip ambra di "Altre colonne" occupano
  due righe: spostare "Altre colonne" in un `st.popover("Colonne")`.
- C10 Laboratorio, Backtest, Strategia automatica: sono strumenti di ricerca, non del percorso principale; vanno
  bene così ma con le stesse regole di tabella e numeri.

---

## 2. Percorso principale: "apro l'app → cerco → capisco se comprare"

Oggi (dati demo, AAPL seguita):
1. Apro → Monitor (borse mondiali, mercati, movimenti dell'indice). Le mie aziende in un riquadro laterale, con
   prezzo, variazione e "margine FV" ma **nessun giudizio**.
2. Cerco → devo sapere che si scrive nella barra (piccola, senza suggerimenti) oppure andare a F3 e usare la
   selectbox. 2 passaggi.
3. Capisco → DES: 70 metriche. Per un giudizio: DCF (2 schermate di scorrimento), RV, FA, NOTE, AUTO. **5-6
   funzioni e 10+ scorrimenti**, e la sintesi la fa l'utente.

Obiettivo: **2 passaggi e zero scorrimenti** per la risposta (Verdetto visibile nella prima schermata a 1280x720),
**1 clic** per ogni prova ("perché?") e per "cosa è cambiato".

Cosa distrae oggi: borse mondiali e mappa in prima schermata, operazioni del backtest sul grafico, BUY/SKIP della
strategia automatica, didascalie ripetute, tasti F per pagine di ricerca (F9 Backtest) al posto di quelle del
percorso principale.

---

## 3. Sistema visivo

Tutto in `ui/common.py` (costanti e CSS) + un nuovo `ui/components.py` (funzioni che restituiscono HTML). Ogni
colore e dimensione come variabile CSS su `:root`, così l'aspetto chiaro cambia solo i valori.

### 3.1 Scala tipografica (px; base 14)

| Ruolo | Dimensione | Peso | Uso |
|---|---|---|---|
| `--fs-micro` | **12** (minimo assoluto) | 400 | barra di stato, intestazioni di colonna, etichette di campo nella testata, distintivi |
| `--fs-data` | 13 | 400 | celle delle tabelle, testo dei pannelli |
| `--fs-body` | 14 | 400 | testo, didascalie (oggi 12,25 → 13 minimo), moduli |
| `--fs-title` | 13 | 600 | titolo di pannello (maiuscolo, spaziatura .04em) |
| `--fs-h2` | 16 | 600 | titolo di sezione dentro una pagina (NON maiuscolo) |
| `--fs-h1` | 20 | 600 | titolo di pagina (NON maiuscolo) |
| `--fs-key` | 26 | 600 | numero chiave: prezzo in testata, etichetta del verdetto |
| `--fs-big` | 18 | 500 | valori di `st.metric` / riga di risultato |

Interlinea: 1.45 per il testo, 1.3 per le tabelle (righe da 22 px con padding 4/8). Numeri sempre
`font-variant-numeric: tabular-nums`. Font: monospazio solo per numeri, ticker, tabelle, codici (F1, DES);
**testo corrente in sans-serif** (`-apple-system, "Segoe UI", sans-serif`) anche nell'aspetto terminale: le frasi
lunghe in monospazio (didascalie, prove del verdetto, note) si leggono il 15-20% più lentamente. È la modifica più
efficace per "utilizzabile" senza perdere l'aspetto terminale (che è dato da nero, ambra, tabelle e tasti).
In `config.toml`: `font = "sans-serif"`, `codeFont = "monospace"`, e il CSS mette monospazio a tabelle, testata,
barra comandi, tasti, badge.

### 3.2 Maiuscole
- SÌ (con `letter-spacing: .04em`): titoli di pannello (≤ 4 parole), intestazioni di colonna, codici (F1, DES,
  FV, DOC), etichette della testata (CAP, P/E), distintivi di stato.
- NO: titoli di pagina e di sezione, voci di menu, expander, pulsanti, testo digitato, frasi, barra di stato
  (solo le sigle in maiuscolo), etichetta del verdetto ("Candidata all'acquisto").
- Mai più di ~30 caratteri di fila in maiuscolo.

### 3.3 Palette con ruoli e contrasti (WCAG, calcolati)

Aspetto terminale (fondo `#000000`; superfici `#0d0d0d` e `#1a1a1a`):

| Token | Valore | Su #000 | Su #1a1a1a | Uso |
|---|---|---|---|---|
| `--bg` | `#000000` | — | — | fondo pagina |
| `--surface-1` | `#0d0d0d` | — | — | corpo dei pannelli |
| `--surface-2` | `#1a1a1a` | — | — | barra del titolo dei pannelli, intestazioni tabella, riga al passaggio |
| `--surface-hi` | `#3a2a00` | — | — | riga dell'azienda corrente (bianco su questo: 13,9:1) |
| `--border` | `#2a2a2a` | 1,3 | — | divisori decorativi (righe di tabella) |
| `--border-ctl` | `#5c5c5c` | **3,14** | 2,6 | bordi di campi, pulsanti secondari, pannelli (≥3:1 richiesto ai controlli) |
| `--text` | `#e8e6e1` | 16,8 | 13,9 | testo |
| `--text-num` | `#ffffff` | 21 | 17,4 | numeri nelle tabelle e nella testata |
| `--text-2` | `#b4b2a9` | 9,9 | 8,2 | testo secondario, didascalie (oggi #9a978f: ok ma passarle a questo per 13px) |
| `--text-3` | `#9a978f` | 7,2 | 6,0 | unità, date, note a piè di tabella |
| `--text-off` | `#6e6d68` | 4,05 | 3,4 | solo "–" dei valori mancanti e controlli disabilitati |
| `--accent` | `#ffa028` | 10,3 | 8,6 | etichette, titoli di pannello, tasto attivo, focus; testo nero su ambra 10,3 |
| `--pos` | `#2fbf4a` | 8,7 | 7,2 | variazioni positive, esito favorevole (sempre con ▲ o ✓) |
| `--neg` | `#ff5a52` | 6,8 | 5,7 | variazioni negative, esito contrario (sempre con ▼ o ✗) |
| `--warn` | `#ffa028` (= accento) → usare `#ffd166` | 14,5 | 12,0 | avvisi "da guardare" (giallo, distinto dall'ambra delle etichette) |
| `--info` | `#5aa0f0` | 7,7 | 6,4 | link nelle tabelle, notizie |
| serie grafici | `#c98500 #3987e5 #d95926 #9085e9 #199e70` | 6,8 / 5,8 / 5,4 / 6,7 / 6,2 | — | invariata (già validata) |

Aspetto chiaro (fondo `#ffffff`, superfici `#f7f6f3` / `#efeee9`): accento `#1f6fd1` (4,94:1), positivo `#0a7f0a`
(5,18:1, oggi `#0ca30c` 3,35 non passa), negativo `#b42f2f` (6,2:1), testo-3 `#6b6a66` (5,41:1; oggi MUTED
`#8a8983` 3,51 non passa per testo), bordo controlli `#8a8983` (3,5:1).

Regola del colore: **nessuna informazione solo col colore**. Variazione = segno + freccia (▲▼■); esito = simbolo
(✓ ✗ ●) + parola; stato borsa = ●/○ + parola. Il verde/rosso si usa solo per "va bene / va male" (variazioni ed
esiti), mai per decorare.

### 3.4 Numeri e formati (una sola funzione, `ui/common.fmt`)
- Mancante: "–" in `--text-off`. Mai `None`, `nan`, `-0.0x` (zero con segno → "0,0x").
- Importi: `money()` esistente (1 decimale + mld/mln + valuta dopo); separatore delle migliaia sempre.
- Percentuali: 1 decimale; con segno solo per variazioni e margini.
- Multipli: 1 decimale + "x". Prezzi: 2 decimali. Conteggi: interi.
- Valuta: nell'intestazione di colonna ("Prezzo $") quando tutte le righe hanno la stessa; nella cella solo se
  la tabella mescola valute.
- Date: `13/11/2026`; nelle tabelle fitte `13/11/26`; "oggi"/"ieri" nelle notizie.

### 3.5 Tabelle: quale usare
- **Tabella fitta HTML (`dense_table`)**: sola lettura, ≤ 40 righe, nessun ordinamento richiesto (Monitor, DES,
  RV mediane, Confronta, DVD, prove del verdetto, cosa è cambiato). Altezza naturale, niente righe vuote.
- **`st.dataframe`**: elenchi lunghi da ordinare/selezionare (Esplora, Le mie aziende, CF, storico avvisi,
  portafoglio). Altezza = `min(righe, 15) * 35 + 38`, mai fissa più alta del contenuto; colonne numeriche con
  `column_config`; testo già formattato per valori misti.

CSS (sostituisce quello in `STATUS_CSS`):
```css
.qe-tablewrap { overflow-x: auto; max-width: 100%; }
table.qe-dense { width: 100%; border-collapse: collapse; table-layout: fixed;
  font: var(--fs-data)/1.3 var(--mono); font-variant-numeric: tabular-nums; }
table.qe-dense th { position: sticky; top: 0; background: var(--surface-2); color: var(--accent);
  font-size: var(--fs-micro); font-weight: 500; text-transform: uppercase; letter-spacing: .04em;
  text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border-ctl); white-space: nowrap; }
table.qe-dense td { padding: 4px 8px; border-bottom: 1px solid var(--border); color: var(--text-num);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
table.qe-dense td.t { color: var(--text); }              /* testo (nome, settore) */
table.qe-dense td.n, table.qe-dense th.n { text-align: right; }
table.qe-dense td.na { color: var(--text-off); text-align: right; }
table.qe-dense td.fill { width: 100%; max-width: 0; }
table.qe-dense tr:hover td { background: var(--surface-2); }
table.qe-dense tr.me td { background: var(--surface-hi); }
table.qe-dense tr[data-href] { cursor: pointer; }        /* solo se la riga è davvero cliccabile */
```
Le righe delle tabelle fitte oggi NON sono cliccabili (cambiano colore al passaggio: sembrano cliccabili e non lo
sono). Due scelte coerenti: o niente `:hover`, o il ticker diventa un link `<a href="./azienda?t=AAPL"
target="_self">` (richiede di leggere `st.query_params["t"]` in `page_company`). Consiglio il link: è il gesto
più naturale ("clicco sull'azienda e si apre").

### 3.6 Riga di testata (scheda, risultato DCF, riepilogo portafoglio)
Due livelli fissi invece di una riga che va a capo.
```html
<div class="qe-head">
  <div class="qe-head-id">
    <span class="tk">AAPL</span><span class="nm">Apple Inc.</span>
    <span class="meta">Information Technology · Hardware</span>
  </div>
  <div class="qe-head-px">
    <span class="px">60,28 $</span><span class="chg pos">▲ +1,55%</span>
    <span class="src">chiusura 02/11/26</span>
  </div>
  <dl class="qe-kv">
    <div><dt>Cap.</dt><dd>66,3 mld $</dd></div>
    <div><dt>P/E</dt><dd>12,3x</dd></div>
    <div><dt>EV/EBIT</dt><dd>9,7x</dd></div>
    <div><dt>Rend. FCF</dt><dd>6,8%</dd></div>
    <div><dt>1 anno</dt><dd class="neg">▼ −10,6%</dd></div>
    <div><dt>52 sett.</dt><dd>52,91 <i class="rng"><b style="left:73%"></b></i> 69,07</dd></div>
    <div><dt>Tuo FV</dt><dd>— <a>imposta</a></dd></div>
  </dl>
</div>
```
```css
.qe-head { display:grid; grid-template-columns: 1fr auto; gap:4px 16px; padding:8px 12px;
  border:1px solid var(--border-ctl); background: var(--surface-1); }
.qe-head .tk { font: 600 var(--fs-key) var(--mono); color: var(--text-num); margin-right:12px; }
.qe-head .nm { font-size: var(--fs-h2); color: var(--text); }
.qe-head .meta, .qe-head .src { display:block; color: var(--text-3); font-size: var(--fs-micro); }
.qe-head-px { text-align:right; } .qe-head .px { font: 600 var(--fs-key) var(--mono); color: var(--text-num); }
.qe-kv { grid-column: 1 / -1; display:flex; flex-wrap:wrap; gap: 4px 24px; margin:6px 0 0; }
.qe-kv dt { color: var(--accent); font: var(--fs-micro) var(--mono); text-transform: uppercase; }
.qe-kv dd { margin:0; font: var(--fs-data) var(--mono); color: var(--text-num); }
.qe-kv .rng { display:inline-block; position:relative; width:64px; height:4px; background: var(--border-ctl);
  vertical-align:middle; margin:0 4px; } .qe-kv .rng b { position:absolute; top:-3px; width:2px; height:10px;
  background: var(--accent); }
.pos { color: var(--pos); } .neg { color: var(--neg); }
```

### 3.7 Pannello
```python
@contextmanager
def panel(title: str, *, right: str = "", key: str | None = None):
    """Riquadro: barra del titolo (maiuscolo, ambra) con testo facoltativo a destra (data, fonte, link)."""
    with st.container(border=True, key=key):
        st.html(f'<div class="qe-panel-title"><span>{html.escape(title)}</span>'
                f'<span class="r">{right}</span></div>')
        yield
```
```css
[data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--border-ctl) !important; border-radius:0 !important;
  background: var(--surface-1); }
.qe-panel-title { display:flex; justify-content:space-between; margin:-.4rem -.6rem .5rem; padding:5px 10px;
  background: var(--surface-2); border-left:3px solid var(--accent); border-bottom:1px solid var(--border);
  font: 600 var(--fs-title) var(--mono); color: var(--accent); text-transform: uppercase; letter-spacing:.04em; }
.qe-panel-title .r { color: var(--text-3); text-transform:none; font-weight:400; }
```
La fonte/data (es. "chiusure del 02/11/2026") va a destra nella barra, non nel titolo.

### 3.8 Distintivo di stato e stato vuoto
```python
def badge(text: str, tone: str = "neutral", symbol: str = "") -> str:
    """tone: pos | neg | warn | neutral | info. Sempre testo + simbolo, mai solo colore."""
    return f'<span class="qe-badge {tone}">{symbol + " " if symbol else ""}{html.escape(text)}</span>'

def empty_state(what: str, why: str, action_label: str | None = None, key: str | None = None) -> bool:
    st.html(f'<div class="qe-empty"><b>{html.escape(what)}</b><span>{html.escape(why)}</span></div>')
    return bool(action_label) and st.button(action_label, key=key)
```
```css
.qe-badge { display:inline-block; padding:1px 6px; font: 500 var(--fs-micro) var(--mono); text-transform:uppercase;
  letter-spacing:.04em; border:1px solid currentColor; white-space:nowrap; }
.qe-badge.pos { color: var(--pos); } .qe-badge.neg { color: var(--neg); } .qe-badge.warn { color: var(--warn); }
.qe-badge.neutral { color: var(--text-2); } .qe-badge.info { color: var(--info); }
.qe-empty { border:1px dashed var(--border-ctl); padding:12px 14px; color: var(--text-2); font-size: var(--fs-body); }
.qe-empty b { display:block; color: var(--text); font-weight:600; margin-bottom:2px; }
```
Avvisi della home oggi in `code` verde (`MRG`): diventano `badge("MRG margine in calo", "warn", "!")`.
Sostituire TUTTI gli `st.info`/`st.warning` usati come stato vuoto (34 occorrenze) con `empty_state`;
`st.error` resta per gli errori veri.

### 3.9 Attesa
```css
/* barra sottile animata in cima mentre lo script gira (lo stato widget di Streamlit esiste solo durante l'esecuzione) */
body:has([data-testid="stStatusWidget"]) [data-testid="stHeader"]::after { content:""; position:absolute; left:0;
  right:0; bottom:-1px; height:2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
  background-size: 40% 100%; background-repeat:no-repeat; animation: qe-run 1s linear infinite; }
@keyframes qe-run { from { background-position: -40% 0 } to { background-position: 140% 0 } }
```
(verificare il selettore con Streamlit 1.66: il widget di stato compare nella testata durante l'esecuzione.)
Più `st.spinner` con testo specifico sui blocchi > 1 s.

### 3.10 Modulo
- Etichetta sopra il campo, 13 px, `--text-2`; campo con bordo `--border-ctl`, focus 2 px `--accent`.
- Ordine: campi obbligatori (max 4 per riga a 1280) → expander "Altri dettagli" → un pulsante primario a
  sinistra con verbo preciso ("Aggiungi acquisto", "Salva decisione").
- Numeri: niente +/- (`[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] {display:none}`),
  `format` coerente col resto dell'app.
- Dopo il salvataggio: `st.toast("Acquisto di 10 AAPL aggiunto")` e la riga nuova evidenziata nella tabella sotto.

### 3.11 Spaziature
Unità 4 px: 4 (dentro le celle), 8 (tra etichetta e valore, padding pannelli), 12 (tra elementi di un
pannello), 16 (tra pannelli; `[data-testid="stVerticalBlock"] { gap: 12px }` invece di .7rem), 24 (tra sezioni).
Margini laterali della pagina: 24 px; `padding-top` della pagina solo quanto serve sotto la barra fissa.

### 3.12 Cornice (shell)
```css
/* barra comandi + tasti fissi sotto il menu */
.st-key-qe_shell { position: sticky; top: 3.75rem; z-index: 999; background: var(--bg); padding: 6px 0;
  border-bottom: 1px solid var(--border); }
.st-key-qe_fkeys a { background: transparent; border:1px solid var(--accent); }
.st-key-qe_fkeys a * { color: var(--accent) !important; font: 600 var(--fs-micro) var(--mono) !important; }
.st-key-qe_fkeys a[aria-current="page"], .st-key-qe_fkeys a.active { background: var(--accent); }
.st-key-qe_fkeys a[aria-current="page"] * { color:#000 !important; }
```
(invertire la logica di oggi: tasti a contorno, il tasto della pagina corrente pieno. Se `st.page_link` non
espone `aria-current`, `shell._command_bar` conosce la pagina corrente da `st.navigation(...).url_path` e può
disegnare il tasto attivo con `disabled=True` o un `key` diverso da colpire col CSS.)
Tasti F proposti, solo percorso principale: F1 Oggi (Monitor), F2 Le mie aziende, F3 Scheda, F4 Esplora,
F5 Confronta, F6 Portafoglio, F7 Diario, F8 Notizie. Strategie, Backtest, Fondi, Laboratorio nel menu
"Strategie e ricerca".

---

## 4. Struttura delle pagine

### 4.1 Monitor → "Oggi" (F1)
Domanda della pagina: "è successo qualcosa alle MIE aziende?"
```
[cornice fissa: menu | ricerca/comando | F1..F8 | Dati]
┌ LE MIE AZIENDE ─────────────── prezzi 15:42 ritardati ┐┌ AVVISI ───────────── storico › ┐
│ Ticker Nome  Prezzo  Oggi   1 anno  Margine FV Verdetto││ ! AAPL  DOC 10-Q depositato ieri│
│ AAPL  Apple  60,28 ▼-1,6% ▼-10,6%   +12%  ✓ Candidata  ││ ! MSFT  MRG margine in calo     │
│ ... (tutte, ordinate per avvisi poi verdetto)          ││ ...                             │
└────────────────────────────────────────────── (8 col) ┘└────────────────── (4 col) ──────┘
┌ NOTIZIE · le mie aziende prima ─────────────────────── ┐┌ BILANCI ARRIVATI · 10-Q/10-K ──┐
│ oggi 14:02 AAPL 8-K  ⚠ 4.02 bilanci non affidabili     ││ 12/11 MSFT 10-Q caricato       │
└────────────────────────────────────────────────────────┘└─────────────────────────────────┘
┌ MERCATI ─ una riga scorrevole: S&P 500, Nasdaq, VIX, 10A, EUR/USD, oro, petrolio ─────────┐
┌ S&P 500 · IN SALITA ──┐┌ S&P 500 · IN DISCESA ──┐┌ ACQUISTI DEGLI INSIDER · 7 GG ──────────┐
▸ Mappa dell'S&P 500 (expander chiuso, 380 px)
▸ Borse mondiali (expander chiuso; lo stato ●/○ resta nella barra di stato)
```
Se l'utente non segue nessuna azienda: il primo pannello diventa uno stato vuoto con la ricerca e 3 ricerche pronte
di Esplora ("Qualità a prezzo giusto"...).

### 4.2 Scheda azienda (F3) — Verdetto in cima
Prima schermata a 1280x720 (circa 560 px utili sotto la cornice fissa): testata + verdetto interi.
```
AZIENDE › SCHEDA › AAPL                                         [☆ Segui] [Cambia ▾]
┌ testata a 2 livelli (§3.6) ───────────────────────────────────────────────────────────┐
│ AAPL Apple Inc.                                                   60,28 $  ▲ +1,55%    │
│ Information Technology · Hardware                                 chiusura 02/11/26    │
│ CAP 66,3 mld · P/E 12,3x · EV/EBIT 9,7x · Rend.FCF 6,8% · 1A ▼-10,6% · 52s ──●── · FV – │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌ VERDETTO ─────────────────────────────── criteri fissati, non è una raccomandazione ──┐
│ ✓ Candidata all'acquisto                     │ COSA FAREBBE CAMBIARE IL GIUDIZIO       │
│ Qualità alta, prezzo sotto il valore stimato,│ • P/E sopra 22x (oggi 12,3x)            │
│ bilancio solido. Regola: qualità ≥ 0,6 e …   │ • margine operativo sotto 18% (25,6%)   │
│                                               │ • un allarme Beneish o Altman           │
│ Qualità del business  ■■■■■■■■□□ 0,78 buona   │                                         │
│ Solidità finanziaria  ■■■■■■■□□□ 0,71 buona   │ VALORE                                  │
│ Prezzo                ■■■■■■□□□□ 0,62 equo    │ Prezzo 60,28 · DCF 504 (416–593) · tuo –│
│ Segnali d'allarme     ■■■■■■■■■□ 0,90 pochi   │                                         │
│ Chi è dentro          dati insufficienti  ⓘ   │ [Perché? prove ▾]                       │
└───────────────────────────────────────────────┴─────────────────────────────────────────┘
┌ COSA È CAMBIATO · 10-Q del 30/10/26 contro il trimestre prima ────────── tutti › ───────┐
│ ▲ Ricavi 12 mesi +4,1% (27,3 mld)   ▼ Margine operativo −1,2 punti   ▲ Debito netto −8% │
│ ● Il prezzo è sceso dell'8% dal deposito: P/E da 13,4x a 12,3x                           │
└──────────────────────────────────────────────────────────────────────────────────────────┘
[ Prove | Bilanci | Grafico | Valutazione | Concorrenti | Notizie e documenti | Altro ▾ ]
  (Altro: Trimestri, Dividendi, Insider e fondi, La mia ricerca, Strategia automatica)
```
Disegno del verdetto:
- Etichetta 26 px, colore e simbolo per categoria: Candidata `✓` `--pos`; Aspettare `◐` `--warn`; Nessun segnale
  netto `●` `--text-2`; Prezzo basso ma qualità dubbia `!` `#d95926`; Da evitare `✗` `--neg`; Dati insufficienti
  `?` `--text-off` con bordo tratteggiato. Il bordo sinistro del riquadro (4 px) prende lo stesso colore.
- Pilastri: barra a 10 celle (testo `■□` o due `<i>` con larghezza %) + numero 0-1 + parola (`grade`) +
  copertura se < 100% ("7 prove su 9"). Pilastro senza punteggio: "dati insufficienti" in `--text-off`, niente barra.
  "Chi è dentro" con il distintivo "solo contesto".
- "Perché? prove" apre un expander (o `st.popover`) per pilastro con la tabella fitta delle `Evidence`: prova,
  valore, riferimento, esito (✓ favorevole / ● neutra / ✗ contraria / – mancante), frase. Le mancanti in fondo,
  in grigio, mai conteggiate come positive.
- Sempre visibile la frase di cautela a destra nella barra del titolo (testo-3), non un riquadro.
- Funzione predefinita del menu: **Prove** (= DES ripensata: le metriche raggruppate per pilastro, colonne
  Valore / Settore / Percentile con barra neutra / Esito). DES con le 70 metriche resta raggiungibile.
- Codici dei comandi invariati (DES, FA, GP, DCF, RV, CF...): le etichette del menu mostrano il nome per esteso e
  il codice piccolo (`Valutazione · DCF`), così chi usa la barra comandi non perde nulla.

Avvisi con storico: nella scheda solo gli avvisi ATTIVI dell'azienda, come distintivi sotto "cosa è cambiato";
lo storico completo in "Le mie aziende" (§4.3) e nel pannello Avvisi del Monitor ("storico ›").

Notizie in tempo reale: nella scheda, funzione "Notizie e documenti" (ex CF) con in alto le ultime notizie/8-K
(ora, fonte, titolo, ⚠ per le voci importanti) e sotto i depositi; nel Monitor il pannello Notizie; un 8-K ⚠ di
oggi compare anche come avviso nel verdetto ("Attenzione: 8-K voce 4.02 depositato oggi — il giudizio usa dati
che potrebbero cambiare").

Grafico a candele: nella funzione Grafico. Candele fino a 6 mesi, linea da 1 anno in su (le candele oltre ~150
barre diventano illeggibili); volumi in un pannello sotto (20% dell'altezza); medie 50/200 sottili in `--text-3`;
indicatori dei depositi 10-Q/10-K come linee verticali tratteggiate con etichetta; operazioni del backtest SPENTE
di default. Colori: corpo pieno `--pos`/`--neg`, stoppino dello stesso colore; nel chiaro i valori chiari §3.3.
Più un mini-grafico 1 anno (sparkline 120x28 px) nella testata accanto al prezzo.

### 4.3 Le mie aziende (F2)
```
Le mie aziende                                   5 aziende · prezzi al 02/11/26 [+ Aggiungi]
┌ DA GUARDARE ─────────────────────────────────────────────────────── storico avvisi › ───┐
│ MSFT  [! MRG margine in calo 26,8% → 21,7%]  [! DOC 10-Q di ieri]   apri ›            │
│ NVDA  [! MRG margine in calo 20,6% → 14,4%]                         apri ›            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
┌ LE MIE AZIENDE ─────────────────────────────────────────────── [Colonne ▾] ─────────────┐
│ ★ Ticker Nome  Verdetto        Prezzo  1 mese  1 anno  P/E  Rend.FCF ROIC  Tuo FV Margine │
│   AAPL  Apple ✓ Candidata      60,28  ▲+6,0%  ▼-10,6% 12,3x  6,8%  127%   –      –      │
│   ...  (8-10 colonne di default, le altre dal popover "Colonne")                         │
└──────────────────────────────────────────────────────────────────────────────────────────┘
Seleziona → [Apri la scheda] [Confronta] [Togli]
▸ Storico degli avvisi (tabella: data, azienda, codice, testo, risolto/attivo)
```
"Aggiungi o togli aziende" diventa un pulsante con `st.popover`, non un expander sopra tutto. Legenda dei codici
nel `help=` della colonna e nell'expander in fondo.

### 4.4 Esplora (F4)
```
Esplora                                         [Ricerca pronta ▾] [Carica] [Salva ▾]
Universo (S&P 500 | Europa | Tutte)   Settori [▾]   Cerca nei risultati [____]
Condizioni: [ROIC ≥ 15% ✕] [P/E ≤ 20x ✕] [+ condizione]          (una riga, chip grigie)
40 aziende su 503 · ordinate per ROIC                                   [Colonne ▾]
┌ tabella (st.dataframe, altezza 15 righe) ─ Verdetto come 3ª colonna ─────────────────────┐
Seleziona → [Apri la scheda] [Segui] [Confronta]   (barra azioni SOPRA la tabella, fissa)
```
I risultati devono iniziare entro la prima schermata (oggi a ~70% dell'altezza a 1440x900). Le condizioni
con `_rules_widgets` restano, ma raccolte in un `st.expander("Condizioni (2)", expanded=True)` compatto.

### 4.5 Altre pagine (solo correzioni puntuali)
- Confronta: una colonna sola, una tabella fitta per gruppo, colonne = aziende, migliore della riga in grassetto +
  `▲` (oggi verde scuro `#0f2a0f`-ish poco visibile), verdetto di ogni azienda in testa.
- Portafoglio / Diario / Metodo / Inserisci dati: regole §3.10; nel Portafoglio "Cambio (per 1 €)" →
  "Cambio €/$" con `help`.
- Strategie / Strategia automatica / Backtest / Laboratorio / Qualità: niente nomi di colonna grezzi
  (`run_id`, `as_of`, `reasoning`), numeri formattati, `None` → "–", placeholder italiani.

---

## 5. Piano di lavoro (compiti con file disgiunti)

Ordine: **T1 e T2 in parallelo → T3…T8 in parallelo → T9**. Ogni compito aggiorna i propri test solo se il file
di test è suo; `tests/test_app_pages.py` è condiviso → lo tocca solo T9 (gli altri segnalano cosa aggiungere).

| # | Compito | File (solo questi) | Dipende da |
|---|---|---|---|
| T1 | Fondamenta: token CSS su `:root` (terminale e chiaro), scala tipografica, sans-serif per il testo, `panel(right=)`, `dense_table` nuova (§3.5, link sul ticker, `na`), `fmt`/`fmt_table` (mai None), `badge`, `empty_state`, `head_html` (§3.6), `verdict_card_html`, `pillar_bar_html`, CSS di attesa e moduli; `client.toolbarMode="minimal"` | `ui/common.py`, **nuovo** `ui/components.py`, `.streamlit/config.toml` | — (pubblicare per primi i NOMI delle funzioni, anche vuote, così gli altri compiti partono) |
| T2 | Cornice: barra fissa, ricerca larga con placeholder, tasti F a contorno + attivo pieno, nuova mappa F1-F8, SEGUI senza cambio pagina + `st.toast`, briciole, barra di stato con priorità e 12 px, AIUTO con nota su fn | `ui/shell.py`, `ui/nav.py`, `modules/commands.py` (solo testi AIUTO/alias) | T1 (solo token) |
| T3 | Scheda: riga identità + testata 2 livelli, Verdetto in cima, "cosa è cambiato", avvisi attivi, menu raggruppato con "Altro", funzione "Prove", rimozione selectbox grande e didascalie ripetute, `st.query_params["t"]` | `ui/pages/azienda/scheda.py`, **nuovo** `ui/pages/azienda/verdetto.py`; eventuali accessori dati in `modules/dashboard_data.py` (solo T3 lo tocca) | T1, `modules/verdict.py`, `modules/changes.py` |
| T4 | Funzioni della scheda: DCF con risultato in cima e niente None; FA asse delle azioni; DVD/HDS/CF/NOTE con `empty_state`, voti 1-5 compatti, un solo primario; AUTO con nomi leggibili e "sperimentale" | `ui/pages/azienda/valutazione.py`, `bilanci.py`, `ricerca.py` | T1 |
| T4b | Grafico a candele + volumi + sparkline (attenzione: `prezzo.py` è in modifica ora da un altro agente → assegnarlo a quell'agente o attendere) | `ui/pages/azienda/prezzo.py` | T1 |
| T5 | Monitor "Oggi" (§4.1) e Notizie | `ui/pages/monitor.py`, `ui/pages/notizie.py` | T1; colonna Verdetto da `verdict.assess_many` |
| T6 | Le mie aziende, Esplora, Confronta (§4.3-4.5) | `ui/pages/mie_aziende.py`, `esplora.py`, `confronta.py`, `ui/rules.py` | T1 |
| T7 | Moduli | `ui/pages/portafoglio.py`, `diario.py`, `inserisci_dati.py`, `metodo.py` | T1 |
| T8 | Pagine di ricerca (solo formati, stati vuoti, placeholder) | `ui/pages/strategie.py`, `strategia_automatica.py`, `backtest.py`, `laboratorio.py`, `qualita.py`, `fondi.py` | T1 |
| T9 | Verifica: test AppTest di tutte le pagine, prova visiva a 1440x900 / 1280x720 / chiaro, CLAUDE.md | `tests/test_app_pages.py`, `CLAUDE.md` | tutti |

Criteri di accettazione comuni: nessun testo sotto 12 px (verificabile con lo script JS usato in questa
revisione: raccoglie `getComputedStyle(e).fontSize` di ogni nodo di testo); nessuna tabella più larga del suo
contenitore a 1280 px; nessun `None`/`nan` visibile (cercare nel testo della pagina); il Verdetto visibile senza
scorrere a 1280x720; nessun `st.info`/`st.warning` usato come stato vuoto.

---

## 6. Decisioni da chiedere all'utente
1. **Convenzione dei numeri**: oggi punto decimale (60.28) nelle tabelle e virgola (0,00) nei campi. Proposta:
   passare tutto all'italiano (60,28; 1.234,5 mld) — è il formato che legge ogni giorno; costa una funzione
   `fmt` e i `format` delle `NumberColumn` (che però seguono le stringhe printf: per l'italiano servono colonne
   testo già formattate, perdendo l'ordinamento numerico in Esplora; alternativa: italiano ovunque tranne nei
   `st.dataframe` ordinabili).
2. **Testo corrente in sans-serif** dentro l'aspetto terminale (numeri e tabelle restano monospazio)?
3. **Pagina iniziale**: Monitor ridisegnato come "Oggi" (consigliato) o direttamente "Le mie aziende"?
4. **Tasti F**: tenere solo le 8 pagine del percorso principale?

---

## 7. Aggiornamento: funzioni nuove già nel codice (verdict, changes, alerts, etf, candele, notizie)

Viste in una SECONDA copia del progetto (albero di lavoro del 13/11 sera, con le modifiche non ancora committate),
sempre su 8520: grafico GP a candele e pagina Notizie con il flusso RSS (372 titoli dopo ~30 s). Verdetto,
cosa è cambiato, avvisi con storico ed ETF non sono ancora nell'interfaccia: progettati dal codice
(docstring e dataclass di `modules/verdict.py`, `changes.py`, `alerts.py`, `etf.py`, `news.py`).

### 7.1 Verdetto (`verdict.assess_ticker` → `Verdict`)
Campi da mostrare e dove (completa §4.2):

| Campo | Dove | Come |
|---|---|---|
| `label` + `category` | riquadro Verdetto, riga 1 | 26 px, simbolo e colore per categoria (§4.2) |
| `rule` | riga 2 | testo 14 px `--text-2`, max 2 righe, il resto in "Perché?" |
| `pillars[k].score/grade/coverage` | sinistra | barra a 10 celle + numero + `grade`; "7 prove su 9" se `coverage<1`; `score=None` → "dati insufficienti" |
| `pillars[k].evidence` / `missing` | expander "Perché? prove" per pilastro | tabella fitta: Prova · Valore (`shown`) · Riferimento (`reference`) · Esito (✓/●/✗/–) · Frase (`text`); mancanti in fondo, grigie |
| `alarms` (gravi) | SOPRA i pilastri, se presenti | banda con bordo `--neg`: "✗ Allarme: …" (uno per riga); sono loro a dare "Da evitare", vanno letti per primi |
| `checks` (da verificare) | sotto gli allarmi | banda con bordo `--warn`: "! Da verificare: …" |
| `changes` ("cosa farebbe cambiare") | colonna destra | elenco puntato, max 4 visibili + "altri n" |
| `data_issues` | piè del riquadro | `--text-3`, 12 px, "Dati: …" |
| `profile_label` | barra del titolo a destra | es. "Banca", "REIT", "Generale" (spiega perché certe prove mancano) |
| `as_of`, `price`, `disclaimer` | barra del titolo a destra | "al 02/11/26 · non è una raccomandazione" (il disclaimer intero nel `title=`) |

`assess_many` → colonna "Verdetto" (badge con simbolo + etichetta corta: Candidata / Aspettare / Neutra /
Dubbia / Evitare / Dati insuff.) in Le mie aziende, Esplora (filtrabile: `st.pills` sopra la tabella con il
conteggio per categoria), Confronta (riga in testa) e Monitor. Ordinamento con `verdict.ORDER`. Colonne
facoltative dei 5 punteggi (0-1, `ProgressColumn` grigia) nel popover "Colonne".
Prestazioni: `assess_many` su 500 titoli va messo in `st.cache_data` con chiave = chiave dei dati; nella pagina un
`st.spinner("Calcolo i giudizi…")` la prima volta.

### 7.2 Cosa è cambiato (`changes.what_changed`, `summarize`, `thesis_check`)
- Scheda, sotto il Verdetto (§4.2): barra del titolo "COSA È CAMBIATO · 10-Q del 30/10/26" con a destra il
  confronto ("contro il trimestre prima" / "contro un anno prima" / "esercizio su esercizio" per le europee).
  Corpo: due colonne **Favorevoli ▲** / **Contrarie ▼** (frasi di `summarize`, al massimo 3+3, ordinate per
  importanza; le neutre non si mostrano qui), poi una riga "Prezzo dal deposito: …" e, se esiste una tesi,
  la riga "La tua tesi: ✓ 2 ipotesi rispettate · ✗ 1 no (crescita ricavi 8% contro 12% che avevi ipotizzato)".
- "Tutti i cambi ›" apre un `st.dialog` (largo) con la tabella completa prima/dopo: Voce · Prima · Dopo ·
  Variazione · Rilevante (●) · Verso (▲ buono / ▼ cattivo), con selettore del confronto.
- Nessun deposito nuovo da 90 giorni: il pannello si riduce a una riga ("Ultimo bilancio 30/07/26, nessun cambio
  rilevante") per non occupare spazio.
- In Le mie aziende e nel Monitor: l'avviso CHG porta la prima frase contraria (o favorevole) come testo.

### 7.3 Avvisi con storico (`alerts`, tabelle `alerts` e `price_targets`)
- **Monitor, pannello AVVISI** (§4.1): solo attivi (`cleared_at` vuoto), i NON VISTI in cima con un pallino
  `--accent` e testo bianco, i visti in `--text-2`. Ogni riga: codice come badge (PX- PX+ FV CHG INS MOV MOV1 52S
  REV 8K; tono: PX-, FV, INS = `pos`; PX+, 8K, MOV giù = `neg`; CHG, REV = `warn`), ticker (link alla scheda),
  messaggio, "da 3 g". Pulsante "Segna tutti come visti" (`db.mark_alerts_seen()`); aprire la scheda segna visti
  quelli di quell'azienda. Contatore dei non visti nella barra del titolo e nel tasto F1 ("F1 Oggi · 3").
- **Le mie aziende**: "Da guardare" = avvisi attivi raggruppati per azienda (§4.3); expander "Storico degli
  avvisi" = `st.dataframe` (data, azienda, codice, messaggio, stato "attivo"/"chiuso il 05/11", visto) con filtro
  per azienda e per codice.
- **Scheda**: avvisi attivi dell'azienda come badge sotto "cosa è cambiato".
- **Soglie di prezzo (`price_targets`)**: nella testata della scheda, accanto a "Tuo FV": "Compra sotto 55,00 ·
  Vendi sopra 80,00" (o "imposta soglie"); un `st.popover("Soglie")` con due `number_input` + nota + Salva/Togli.
  Sul grafico GP due linee orizzontali tratteggiate (`--pos` compra, `--neg` vendi) con etichetta a destra.
  In Le mie aziende due colonne facoltative "Compra sotto" / "Vendi sopra" e la distanza in %.
- Notifica del Mac (`notification_text`) e badge in-app devono usare gli stessi codici e testi.

### 7.4 Dentro gli ETF (`etf.py`) — pagina Portafoglio
Struttura proposta della pagina Portafoglio (oggi solo il modulo d'inserimento in cima):
```
Portafoglio                       valore 48.230 € · P/L +3.120 € (+6,9%) · vs SPY −1,2 punti   [+ Operazione]
┌ POSIZIONI ──────────────────────────────────────────────────────────────────────────┐
│ Titolo  Qtà  Costo medio  Prezzo  Valore €  Peso  P/L €  P/L %  di cui cambio  Verdetto │
└────────────────────────────────────────────────────────────────────────────────────────┘
[ Posizioni | Esposizione vera | Sovrapposizioni ETF | Operazioni | Dividendi e tasse ]
Esposizione vera:
┌ PER AZIENDA (azioni dirette + ETF) ─────── limite inferiore ─┐┌ PER SETTORE ───────────────┐
│ Apple   4,1%  (diretta 2,0% + VWCE 1,3% + SXR8 0,8%)         ││ barre orizzontali           │
│ …                                                             ││                             │
│ ░ Non dettagliato: 61% (ETF oltre le prime 10 posizioni)      ││ ░ non classificato          │
└───────────────────────────────────────────────────────────────┘└─────────────────────────────┘
Sovrapposizioni: matrice ETF × ETF con l'intervallo ("tra 18% e 34%") + `concentration` (prime 10 = x%).
```
Regole: la quota "non dettagliata" sempre visibile come ultima riga/barra tratteggiata (mai nasconderla); ogni
numero per azienda con l'etichetta "almeno"; data dello scarico della composizione nella barra del titolo;
`possible_duplicates` come avviso "Forse la stessa azienda: ALV.DE / ALV — unisci?" (non automatico). Paesi: NON
mostrarli (il modulo non li ha, il suffisso è la borsa).
Il modulo "Nuova operazione" passa in un `st.dialog` aperto da "+ Operazione" (oggi occupa la prima schermata).

### 7.5 Grafico a candele (GP, visto)
Ora: candele fino a 1A, volumi colorati come la candela sotto, pannello "dal massimo" sotto, comandi Periodo /
Grafico (Auto · Candele · Linea) / Medie / Log / vs SPY / Operazioni / 10-K, riga di statistiche (rendimento,
massimo, minimo, volatilità, dal massimo, drawdown). Correzioni:
- 1A = ~250 candele larghe 2-3 px: illeggibili. "Auto" = candele fino a 6M, linea da 1A in su (o candele
  settimanali per 1A).
- Volumi saturi rosso/verde attirano l'occhio quanto il prezzo: colori al 45% di opacità o grigi con la sola
  ultima barra colorata; altezza 20%.
- "Operazioni" (acquisti/stop-loss/take-profit del backtest) ACCESE di default: spegnerle (sono della strategia
  automatica, non dell'utente); tenere le linee dei depositi 10-K/10-Q e aggiungere le soglie `price_targets` e
  il fair value dell'utente come linee orizzontali.
- Tooltip: valori grezzi (65.69639) e nomi tagliati ("Media 50 gio..."): `hovertemplate` con 2 decimali e nomi
  brevi ("MM50", "MM200").
- Il pannello "dal massimo" (rosso pieno 200 px) è utile ma pesante: 120 px, rosso al 35%.
- Nel chiaro, verde/rosso delle candele: `#0a7f0a` / `#b42f2f` (§3.3).

### 7.6 Notizie in tempo reale (vista) e riquadri X (in arrivo)
Vista ora: tabella fitta di titoli (ora · fonte · ticker · titolo), filtri Tutte / Le mie aziende / Un ticker,
fonte, lingua, ricerca. Bene la densità e la marcatura dei ticker in ambra. Problemi:
- Il flusso "Tutte" è un torrente (372 titoli, soprattutto comunicati stampa e Seeking Alpha) e tutte le righe
  hanno lo stesso peso: per un investitore privato la predefinita deve essere **Le mie aziende** (+ portafoglio),
  con "Tutte" come seconda scelta.
- Distinguere a colpo d'occhio: titoli con un ticker seguito in `--text` pieno, gli altri in `--text-2`; le fonti
  ufficiali (comunicati, banche centrali, SEC) con badge `info` "UFF"; le nuove dall'ultimo aggiornamento con il
  pallino `--accent` a sinistra (oggi "*"), non solo con il colore.
- I titoli devono essere link all'articolo (`target="_blank" rel="noopener"`), e il ticker un link alla scheda.
- "primo giro dei feed in corso · 0 notizie" con pagina vuota: usare `empty_state` con il conteggio delle fonti
  che rispondono e un'animazione di attesa (§3.9).
- Stato delle fonti: bene nell'expander; aggiungere l'ora dell'ultimo aggiornamento nella barra del titolo.

Riquadri X (progetto):
- Sezione separata nella pagina Notizie, **sotto** il flusso o in una scheda "X" accanto a "Flusso" e "Documenti
  ufficiali (8-K)": griglia di riquadri a 3 colonne (1440) / 2 (1280), ognuno = un account scelto in config
  (aziende seguite, banche centrali, agenzie), altezza fissa 420 px con scorrimento interno, barra del titolo del
  pannello col nome dell'account e "apri su X ›".
- Gli incorporamenti ufficiali caricano JavaScript esterno e tracciamento: di default mostrare un **riquadro
  segnaposto** (nome account, ultimo aggiornamento noto se c'è, pulsante "Carica i post") e caricare
  l'incorporamento solo su richiesta, con `st.components.v1.html(..., height=420, scrolling=True)` in un iframe
  isolato; tema scuro dell'incorporamento nell'aspetto terminale. Se il caricamento fallisce (account privato,
  limiti, niente rete) → `empty_state("Post non disponibili", "X non ha risposto", "Apri su X")`.
- Non mischiare i post X nel flusso RSS (affidabilità diversa); nella scheda azienda al massimo un link "Account
  ufficiale su X" nella funzione Notizie.
- Testi e loghi di X: solo il nome del servizio nel link, niente logo.

### 7.7 Aggiornamento del piano di lavoro (§5)
- T3 (scheda) assorbe Verdetto + Cosa è cambiato + badge degli avvisi + popover soglie: file `scheda.py`, nuovo
  `ui/pages/azienda/verdetto.py`, nuovo `ui/pages/azienda/cambiamenti.py`; unico a toccare `modules/dashboard_data.py`
  (accessori `verdict(t)`, `verdicts()` in cache, `what_changed(t)`, `alerts(...)`, `price_target(t)`).
- T4b (candele, `prezzo.py`): correzioni §7.5 + linee delle soglie e del fair value.
- T5 (Monitor + Notizie): pannello Avvisi, colonna Verdetto, flusso notizie "mie aziende" di default, riquadri X.
  Attenzione: `ui/pages/notizie.py` è in modifica ora da un altro agente → stesso agente o dopo.
- T6 (Le mie aziende, Esplora, Confronta): colonna e filtro Verdetto, storico avvisi, colonne soglie.
- T7 (moduli) + nuovo T7b **Portafoglio** (`ui/pages/portafoglio.py` da solo): §7.4.
- Dipendenza comune: i componenti `verdict_card_html`, `pillar_bar_html`, `badge`, `empty_state`,
  `alert_row_html` in `ui/components.py` (T1) prima di T3/T5/T6.
