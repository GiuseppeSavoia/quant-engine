# 🏛️ QUANT_ENGINE

Sistema deterministico di analisi azionaria: niente AI generativa, solo matematica
quantitativa, **regime detection** e **valutazione intrinseca**. Gira in modalità
*paper trading*: produce decisioni e ordini simulati, **non è collegato a nessun broker**.

> Non è una raccomandazione di investimento. Prima di fidarti dei segnali, guarda il backtest.

---

## Avvio rapido

```bash
cd quant-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# apri config.yaml e scrivi in sec.user_agent nome ed email (es. "Mario Rossi mario@esempio.it")

python main.py --demo --backtest   # prova completa su dati simulati, senza internet
python main.py                     # dati reali: aggiorna, analizza, genera ordini simulati
python main.py --backtest          # come sopra + backtest della stessa strategia
python main.py --no-update         # usa i dati già salvati (nessun download)
python main.py --list              # solo i ticker di config.yaml invece dell'S&P 500
python main.py --research          # ricerca dei segnali: ogni punteggio prevede i rendimenti futuri?
python main.py --factors           # ricerca dei fattori classici e della loro combinazione (walk-forward)
python -m pytest                   # test automatici
streamlit run app.py               # webapp nel browser (http://localhost:8501)
python tools/build_mac_app.py      # crea l'app per Mac (icona in ~/Applications, vedi sotto)
python tools/schedule.py attiva    # aggiornamento automatico ogni sera (anche dal menu "Dati" della webapp)
```

**Aggiornamento automatico**: con l'interruttore "Aggiornamento automatico" nel menu "Dati" in alto a destra (o
`python tools/schedule.py attiva|disattiva|stato`) il Mac aggiorna tutto da solo ogni giorno feriale, mezz'ora
dopo la chiusura di Wall Street (22:30 ora italiana): prezzi di chiusura, 10-Q e 10-K appena depositati alla SEC,
insider, 13F e analisi, con una notifica alla fine. Usa launchd (il pianificatore di macOS): controlla ogni ora se
dopo l'ultima chiusura c'è già stato un aggiornamento riuscito, quindi se il Mac era spento o in stop recupera
appena si riaccende. Registro in `data/logs/pianificato.log`, output dell'aggiornamento in
`data/logs/aggiornamento.log`.

**App per Mac**: `python tools/build_mac_app.py` crea `~/Applications/QUANT_ENGINE.app` (con `--dest` un'altra
cartella, con `--demo` un'app sui dati simulati). Si apre dal Launchpad o da Spotlight; trascinandola nel Dock
resta a un clic. L'app mostra la webapp in una finestra sua (pywebview) e chiudendola si ferma anche il server.
Mostra i dati dell'ultima esecuzione di `main.py`; per aggiornarli c'è il pulsante **Aggiorna dati** nel menu
"Dati" in alto a destra (esegue `main.py` in un processo separato, con l'opzione di rifare anche il backtest; log in
`data/logs/aggiornamento.log`). Mentre lavora si continua a usare l'app con i dati precedenti; alla fine la
pagina si ricarica da sola.
L'app punta a questa cartella e al suo `.venv`: se il progetto si sposta, va ricreata. Log in
`~/Library/Logs/QUANT_ENGINE.log`.

I risultati finiscono in `reports/`: una pagina HTML leggibile e i CSV con tutti i numeri.
Ogni decisione e ogni ordine simulato vengono anche registrati nel database
(`data/finance_lake.db`, tabelle `decisions` e `paper_orders`).

Tutti i parametri (universo, pesi, soglie, capitale, costi…) sono in **`config.yaml`**.

**Webapp** (`streamlit run app.py`, oppure `streamlit run app.py -- --demo` per i dati simulati):

Aspetto da terminale finanziario (fondo nero, testo monospazio, ambra; l'interruttore "Aspetto terminale" nel
menu "Dati" torna all'aspetto chiaro). In cima a ogni pagina una barra dei comandi: `AAPL` (o `APPLE`) apre la
scheda, `AAPL MSFT` il confronto, `SEGUI KO` aggiunge alle tue aziende, `FONDI`, `ESPLORA`, `BT`… aprono le pagine,
`AIUTO` mostra l'elenco. Tasti F1-F8 (o Alt+1...8) per le pagine principali (F1 Oggi, F2 Le mie aziende,
F3 Scheda, F4 Esplora, F5 Confronta, F6 Portafoglio, F7 Diario, F8 Notizie), `/` per tornare alla barra dei comandi. In fondo, la
barra di stato: data dei prezzi, 10-Q/10-K arrivati, aggiornamento automatico, Borsa di New York aperta o chiusa.

- *Monitor* (la pagina iniziale, F1): striscia dei mercati (S&P 500, Nasdaq, Dow, Russell, VIX, tasso a 10 anni,
  dollaro, EUR/USD, oro, petrolio, bitcoin; `config.yaml`, sezione `monitor`), le tue aziende con la variazione di oggi
  e il margine sul tuo fair value, i titoli dell'S&P 500 più in salita e in discesa, la mappa dell'indice (riquadri
  grandi quanto la capitalizzazione, colore = variazione), le notizie ufficiali (8-K), i 10-Q/10-K arrivati e gli
  acquisti degli insider. Quotazioni Yahoo con circa 15 minuti di ritardo, aggiornate da sole ogni minuto a mercato
  aperto.
- *Notizie* (`CN`, F8): gli 8-K delle aziende dell'S&P 500 (risultati, acquisizioni, dirigenti, debito, revisore...),
  filtrabili per periodo, tipo di evento, solo le tue aziende o solo quelli da guardare subito (⚠).
- *Scheda azienda*: un menu numerato di funzioni, come in un terminale: `AAPL DES` (o `1`) profilo, `FA` bilanci, `ERN`
  trimestri, `GP` grafico, `RV` concorrenti dello stesso sotto-settore, `DCF` valutazione, `DVD` dividendi e
  riacquisti, `HDS` insider e fondi, `CF` depositi SEC e notizie con il link al documento, `NOTE` la tua ricerca,
  `AUTO` strategia automatica.
- *Le mie aziende* (la pagina iniziale): le aziende che segui (si aggiungono qui o con "☆ Segui" nella
  scheda) con i numeri principali, il tuo fair value e il margine rispetto al prezzo, l'ultimo bilancio e gli
  avvisi: prezzo sotto o vicino al tuo fair value, nuovo 10-Q/10-K, movimenti forti in un mese, distanza dal
  massimo, margine operativo in calo (soglie in `config.yaml`, sezione `watchlist`).
- *Il mio metodo*: il tuo profilo (stile, orizzonte, principi, cosa ti fa scartare un'azienda, quando vendi),
  le tue metriche (formule sulle metriche del catalogo, es. `fcf_yield - 0.02 * net_debt_ebitda`, valide ovunque
  e anche nei backtest) e la tua checklist qualitativa (domande e pesi tuoi; il voto si dà nella scheda azienda,
  "La mia ricerca", e si usa in Esplora e Confronta ma non nei backtest).
- *Inserisci dati*: qualsiasi voce di bilancio, per qualsiasi periodo (esercizio o ultimi 12 mesi), in una
  tabella già compilata con i dati attuali: modifichi o aggiungi le celle, il resto (free cash flow, margini,
  multipli, ROIC, punteggi, DCF) lo calcola l'app. Un valore a mano prevale su quello SEC per lo stesso periodo,
  da oggi o (per correggere la storia) dalla data di pubblicazione che indichi. Puoi anche aggiungere aziende fuori
  dall'S&P 500 con il simbolo Yahoo (es. ENI.MI, NESN.SW): i prezzi si scaricano con "Aggiorna dati", i bilanci li
  inserisci tu (per le americane arrivano dalla SEC). Non entrano nei backtest delle strategie.
- *Grandi investitori*: i portafogli di 22 investitori noti (Berkshire, Pershing Square, Baupost, Akre,
  Fundsmith, Markel...; elenco in `config.yaml`, sezione `superinvestors`) dai 13F trimestrali: le aziende più
  presenti e il portafoglio di ogni fondo con nuove posizioni, aumenti, riduzioni e uscite. I 13F escono fino a
  45 giorni dopo la fine del trimestre: servono per idee e conferme, non per copiare. Insider e 13F si scaricano la
  prima volta con l'opzione "Anche insider e grandi investitori" di "Aggiorna dati" (o `python main.py --insiders`),
  poi si aggiornano da soli a ogni analisi; nella home compare un avviso quando gli insider di un'azienda che segui
  comprano per almeno 100.000 $ in 30 giorni.
- *Confronta*: da 2 a 5 aziende affiancate su tutte le metriche (evidenziata la migliore di ogni riga),
  prezzo, ricavi, margine operativo e P/E nel tempo; ci si arriva anche selezionando più righe in Esplora o
  in Le mie aziende.
- *Strategie*: il catalogo (indice a pesi uguali, momentum, value, quality, bassa volatilità, Magic Formula,
  Piotroski, qualità a prezzo giusto, combinazione di fattori, strategia originale) e le strategie tue, costruite senza
  codice in quattro passi: universo (settori o solo le aziende che segui), filtri (anche da una ricerca di Esplora),
  classifica (metriche e pesi), portafoglio (numero di titoli, pesi, massimi per titolo e settore,
  ribilanciamento). Per ognuna: backtest dal 2012 con i soli dati noti a ogni data, confronto con l'indice a pesi
  uguali e con SPY sull'intero periodo e sulle due metà, anno per anno, rotazione e costi, cosa terrebbe oggi, e
  il conteggio delle varianti provate (più se ne provano, più la migliore è fortunata). I backtest restano salvati
  e si rifanno solo quando cambiano i dati o la strategia (dopo l'aggiornamento serale si vede l'ultimo, con la
  sua data e il pulsante "Ricalcola").
  **Grandi investitori** (🏛, `modules/investors.py`): 20 strategie ispirate a Graham, Buffett, Munger, Lynch,
  Philip Fisher, Greenblatt (Magic Formula), Piotroski, Templeton, Neff, Dreman, Schloss, Klarman, Zweig, O'Neil
  (CAN SLIM), Driehaus, O'Shaughnessy (Trending Value), Ken Fisher, Terry Smith, Slater (Zulu) e Pabrai (che
  "clona" i grandi investitori dai 13F, backtest dal 2014). Ognuna ha la sua scheda: chi è, la fonte (libro o
  lettere), il metodo originale e come è tradotto in regole, con cosa manca. Le regole prendono solo la parte
  misurabile del metodo; i parametri sono fissati prima di guardare i risultati. Esclusi Soros, Druckenmiller,
  Dalio, Simons, Icahn, Ackman, Livermore: macro, modelli segreti, attivismo o trading non si traducono in regole
  sulle azioni dell'S&P 500.
- *Strategia automatica*: regime, portafoglio simulato con pesi, stop-loss e take-profit, peso per settore,
  tutte le decisioni filtrabili con la motivazione.
- *Esplora*: cerca tra le ~500 aziende con condizioni sulle metriche del catalogo ("ROIC almeno 15%",
  "debito netto/EBITDA al massimo 2,5", "nel 25% migliore del settore per rendimento del FCF"...), per settore e
  per nome; ricerche pronte (qualità a prezzo giusto, economiche sul cash flow, crescita costante, dividendi
  solidi, riacquisti, migliori del settore) e ricerche tue salvate; dai risultati segui le aziende o apri la
  scheda.
- *Scheda azienda* (lo strumento per valutare un'azienda):
  - **Riepilogo**: le ~35 metriche del catalogo, ognuna con la mediana del settore e dell'S&P 500 e la
    posizione nel settore (100% = la migliore);
  - **Bilanci**: esercizi, trimestri singoli o ultimi 12 mesi a ogni trimestre, grafici di ricavi/utili/FCF,
    margini, debito e cassa, dividendi e riacquisti, numero di azioni, tabella completa. In fondo "Tutte le voci
    depositate alla SEC": ogni etichetta XBRL dei 10-K e 10-Q dell'azienda (anche quelle che l'app non usa,
    500-900 per azienda) per trimestre, con grafico, versioni e CSV di tutte le voci;
  - **Valutazione** alla Damodaran, "dalla storia ai numeri": scrivi la storia dell'azienda e la traduci in leve
    (crescita dei ricavi, margine operativo obiettivo, ricavi per euro investito, costo del capitale, crescita per
    sempre, ROIC nella fase stabile, rischio di fallimento) per tre scenari con le tue probabilità. L'app calcola
    valore per scenario e valore atteso, la crescita che il prezzo di oggi richiede, i controlli di coerenza di
    Damodaran, la plausibilità di ogni ipotesi contro le aziende del settore, una simulazione con 5.000 valutazioni
    (probabilità che il valore superi il prezzo, leve che contano di più) e l'anno per anno. "Salva nel diario"
    conserva la valutazione con prezzo e ipotesi di allora. Sotto, i multipli degli ultimi 10 anni e il DCF rapido
    di prima. Il costo del capitale proposto è calcolato come Damodaran, con un riquadro passo per passo: beta di
    settore (non quello rumoroso del singolo titolo), costo del debito da un rating sintetico, premio per il rischio
    implicito nei prezzi dell'S&P 500. Si possono attivare le sue correzioni: ricerca e sviluppo come investimento,
    leasing operativi come debito;
  - **Andamento** del titolo (medie 50/200 giorni, volume, confronto con SPY, drawdown, depositi dei 10-K);
  - **Insider e fondi**: acquisti e vendite degli amministratori e dei dirigenti (Form 4) sul grafico del prezzo,
    con importi a 90 giorni e a 12 mesi (le vendite programmate 10b5-1 e i soci oltre il 10% sono tenuti a parte),
    e i grandi investitori seguiti che hanno l'azienda nell'ultimo 13F, con la variazione rispetto al trimestre prima;
  - **La mia ricerca**: scheda strutturata (tesi in una frase, storia, cosa deve succedere, rischi, cosa ti farebbe
    cambiare idea, data della prossima revisione, che diventa un avviso nella home), il tuo voto della checklist, il
    diario delle valutazioni con il senno di poi (prezzo da allora, si è mosso verso il tuo valore?), appunti liberi
    ed esportazione della scheda in PDF;
  - **Strategia automatica**: perché la strategia ha deciso BUY/SKIP, storico delle decisioni, versioni
    depositate alla SEC.
- *Backtest e ricerca*: curva del portafoglio contro il benchmark, esposizione, mappa dell'IC dei segnali.
- *Laboratorio*: cambi pesi, soglie, uscite, numero di titoli, settori senza DCF… e rifai il backtest
  (circa 15 secondi) senza toccare `config.yaml`. Ci sono punti di partenza pronti ("Senza stop-loss/
  take-profit", "Regime fuori dal punteggio", "REIT senza DCF", "Solo multi-factor"). Ogni prova è
  salvata (tabella `experiments`) con commit e configurazione; il confronto mostra metriche e curve
  affiancate, quante prove sono state fatte sugli stessi dati e le modifiche in YAML da copiare.
- *Qualità dei dati*: i problemi trovati nell'ultima analisi.

La webapp legge soltanto: i dati li produce `main.py`. Dopo una nuova esecuzione basta ricaricare la pagina;
dopo una modifica al codice invece va riavviata (Ctrl+C e di nuovo `streamlit run app.py`).

**Fattori classici** (`--factors`, `modules/factors.py`): 17 fattori della letteratura, tutti
orientati in modo che "più alto = meglio" e calcolati point-in-time: valore (utili, FCF, patrimonio,
ricavi sul prezzo), qualità (margine lordo/attivo, ROA, ROE, basso debito, utili di cassa), bassa
crescita dell'attivo, crescita dei ricavi, momentum 12-1 e 6-1, inversione a un mese, bassa
volatilità, basso beta, piccola capitalizzazione. Ognuno viene misurato come i segnali della
strategia; poi una **combinazione** con pesi proporzionali all'IC, stimati SOLO sulla prima metà
delle date e giudicati sulla seconda. I fattori in `factors.exclude` sono misurati ma restano fuori
dalla combinazione: di default l'inversione a un mese, che con un ribilanciamento mensile triplica la
rotazione e i costi senza migliorare il risultato. Con `strategy.mode: factors` la strategia compra i primi
`max_positions` titoli per combinazione (sempre investita, senza regime né soglie); nel Laboratorio
c'è il punto di partenza "Fattori: pesi dalla ricerca" e il confronto "Tutto l'indice a pesi uguali".

**Storico lungo**: 18 anni di prezzi (`data.history_years`, aumentandolo si scarica solo la parte
mancante) e bilanci SEC dal 2009; backtest e ricerca dal 2012 (`backtest.start`, `research.start`).
Il backtest riporta i rendimenti anno per anno e la copertura dell'universo. Nel laboratorio si può
limitare il backtest alla prima o alla seconda metà del periodo: si sceglie la variante su una metà
e la si verifica sull'altra.

**Riproducibilità**: il progetto è sotto git. Ogni esecuzione registra nella tabella `runs` il
commit, se c'erano modifiche non salvate, la configurazione completa e le versioni delle librerie;
l'intestazione dei report riporta commit e impronta della configurazione. Prima di ogni
aggiornamento dei dati il database viene copiato in `data/backups/` (una copia al giorno, le ultime 3:
con lo storico lungo il database pesa circa 330 MB).

**Qualità dei dati**: a ogni analisi si controllano prezzi mancanti o vecchi, salti di prezzo
non spiegati da uno split, bilanci assenti o vecchi, numero di azioni discontinuo. Il riepilogo è
nel log e nel report, l'elenco completo in `qualita_dati_<run>.csv`.

**Ricerca dei segnali** (`--research`): prima di combinare i punteggi bisogna sapere se ognuno
funziona da solo. Ogni 21 giorni di borsa degli ultimi 3 anni la stessa strategia calcola i punteggi
point-in-time, che vengono confrontati con i rendimenti successivi (5, 21, 63, 126 giorni):
IC (correlazione di rango), t-stat, % di date in cui il segnale ha ragione, IC dentro i settori,
rendimento per quintile e differenza tra migliori e peggiori. Il verdetto "solido" richiede un
t-stat di almeno 3, perché con tanti segnali e orizzonti qualche risultato sopra 2 esce per caso.

### Da dove arrivano i dati

| Dato | Fonte | Note |
|---|---|---|
| Composizione S&P 500, settori, data di ingresso | Wikipedia | aggiornata ogni 7 giorni |
| Codice SEC (CIK) mancante | SEC `company_tickers.json` | |
| Bilanci annuali (10-K) e trimestrali (10-Q) | **SEC EDGAR**, API XBRL `companyfacts` | ufficiale e gratuita; serve `sec.user_agent` |
| Prezzi giornalieri | Yahoo Finance (yfinance) | a blocchi da 100 ticker, con nuovi tentativi |
| Tasso risk-free | Yahoo (`^TNX`) | |
| Operazioni degli insider (Form 4) | SEC: archivi trimestrali Form 3/4/5 + singoli Form 4 recenti delle aziende seguite | 12 trimestri |
| Portafogli dei grandi investitori (13F) | SEC EDGAR, tabelle 13F-HR dei fondi in `config.yaml` | ultimi 8 trimestri; ticker ricavato dal nome |

**Primo avvio sull'S&P 500**: scarica 18 anni di prezzi per ~500 titoli e i bilanci di ~500
aziende dalla SEC (qualche centinaio di MB compressi, rispettando il limite di 10 richieste/secondo).
Ci vogliono alcuni minuti. Dalle volte successive si scaricano solo i giorni di prezzo mancanti, e
i bilanci di un'azienda quando l'indice giornaliero della SEC mostra un suo 10-Q o 10-K nuovo (o comunque
ogni 90 giorni). Se i dati XBRL del documento non sono ancora pubblicati, si riprova al giro dopo; la pagina
"Qualità dei dati" mostra i depositi degli ultimi 14 giorni e il loro stato.

**Trimestri singoli** (tabella `quarterly_financials`): per ogni trimestre il valore dichiarato nel 10-Q, oppure
la differenza tra due valori "da inizio esercizio" (il rendiconto finanziario dei 10-Q è sempre cumulato; il quarto
trimestre = esercizio del 10-K meno i primi nove mesi). Ogni versione resta con la sua data di deposito.
**Archivio completo** (`data/sec_facts.db`, ~15 milioni di valori, fuori dai backup perché si riscarica dalla
SEC): ogni valore di ogni etichetta dei 10-K e 10-Q, come depositato.

**Perché la SEC**: ogni valore ha la **data di deposito**, e un 10-K riporta anche gli anni
precedenti (a volte corretti). Il database conserva tutte le versioni: a ogni data il backtest usa
solo i bilanci già pubblici in quel momento, nella versione allora disponibile.

**Metriche delle aziende** (`modules/metrics.py`): un unico catalogo di ~40 numeri per azienda (P/E, EV/EBIT,
rendimento del FCF e dei dividendi, margini, ROE, ROIC, crescite, debito/EBITDA, current ratio, copertura degli
interessi, Piotroski F-score, Altman Z-score, qualità degli utili (conversione in cassa, compensi in azioni,
FCF dopo i compensi in azioni, Beneish M-score), insider e fondi (acquisti degli insider, quanti hanno comprato,
acquisti netti sulla capitalizzazione, quanti grandi investitori la possiedono e quanti la comprano), storia su 5
esercizi (crescita annua di utili per azione e ricavi, ROE minimo, ROIC medio, tendenza del margine, anni di fila in
utile e con dividendi, P/E rispetto alla propria storia), misure dei grandi investitori (PEG, P/E x P/B, prezzo/ricavi,
rapporto di Neff, FCF/enterprise value, economicità complessiva di O'Shaughnessy), crescita e accelerazione degli
utili trimestrali (dai trimestri singoli), distanza dal minimo di 52 settimane, momentum, volatilità, beta...),
calcolati sugli ultimi 12 mesi e point-in-time: gli stessi numeri
valgono nella scheda azienda, nei filtri e nelle regole delle strategie, anche nel backtest. I rapporti senza
senso restano vuoti (es. P/E con utili negativi).

**Rischio di portafoglio** (`risk` in config.yaml, `modules/risk.py`): dopo l'ottimizzatore i pesi passano
da tre limiti, uguali nell'analisi e nel backtest: tetto per settore (attivo, 30%), volatilità obiettivo e
limite di perdita dal massimo (spenti: sui dati 2012-2026 peggiorano rendimento e Sharpe in entrambe le metà;
si possono provare nel Laboratorio). Il report e la webapp mostrano volatilità stimata, beta, VaR/CVaR,
concentrazione e le prove di stress: il portafoglio di oggi nelle crisi 2008, 2011, 2018, 2020, 2022 e con
shock ipotetici (mercato -20% via beta, settore più pesante -30%). Il limite di perdita nell'analisi
giornaliera non scatta finché non c'è un registro delle posizioni (serve il valore del portafoglio nel tempo).

**Ultimi 12 mesi (TTM)**: un 10-K arriva una volta l'anno, quindi i suoi numeri possono avere fino a
~15 mesi. Dai 10-Q si ricavano i valori degli ultimi 12 mesi a ogni trimestre: i flussi (ricavi,
utili, cash flow) come "da inizio esercizio di quest'anno + ultimo esercizio completo - stesso periodo
dell'anno prima", lo stato patrimoniale e le azioni alla chiusura del trimestre. Sono salvati con
fonte `sec_ttm` e la data di deposito del 10-Q. I fattori, il quality e il DCF (FCF base, debito, azioni)
usano la vista più recente (`MarketData.fundamentals`); la storia annuale (`statements`) resta per la
crescita del FCF e per le tabelle per esercizio.

**Split azionari**: Yahoo rettifica tutti i prezzi passati per ogni split, la SEC riporta le azioni
com'erano al deposito. Il sistema salva gli split (tabella `splits`) e moltiplica le azioni di ogni
bilancio per gli split successivi al suo deposito, così prezzo e numero di azioni sono sulla stessa
base (senza correzione, prima dello split 10:1 di NVDA il fair value risulterebbe ~10 volte più alto).
Se uno split compare negli aggiornamenti incrementali, lo storico prezzi di quel titolo viene
riscaricato per intero.

---

## Come ragiona

Per ogni data di analisi:

1. **Dati** — prezzi e bilanci dal Golden Data Lake (SQLite), aggiornati in modo incrementale.
2. **Regime di mercato** — Hidden Markov Model sul benchmark (SPY): Bull / Neutral / Bear.
3. Per ogni titolo:
   - **Circuit breaker**: blocca su storico corto, volatilità estrema o movimento anomalo.
   - **Valutazione (DCF)** (non per il settore Financials, dove il FCF non è significativo): beta stimato, WACC con il Treasury 10Y, FCF proiettato a 10 anni
     con crescita che sfuma verso quella terminale → fair value per azione e margine di sicurezza.
     Calcola anche la crescita *implicita* nel prezzo (reverse DCF).
   - **Multi-factor**: Momentum 12-1 mesi, Quality (debito/patrimonio, margine netto), bassa volatilità.
   - **Confidenza** (0–1) = 30% regime + 40% margine di sicurezza + 30% multi-factor.
     Soglia dinamica: 0,50 in Bull, 0,65 in Neutral, 0,80 in Bear.
4. **Portafoglio** — tra i titoli promossi si tengono i 25 con confidenza più alta, pesati con
   risk parity (o minima varianza), massimo 20% ciascuno; il resto resta liquido.
5. **Esecuzione simulata** — quantità, tipo di ordine (MARKET/LIMIT), stop-loss e take-profit su ATR.

Il **backtest** usa esattamente la stessa strategia, data per data, vedendo solo i prezzi passati,
i bilanci già depositati e i titoli già entrati nell'S&P 500 a quella data. I segnali sono eseguiti il giorno di borsa successivo,
con costi, vendite, stop-loss/take-profit e confronto con SPY.

---

## Struttura

```
main.py                     orchestratore (opzioni da riga di comando)
app.py                      webapp Streamlit (punto d'ingresso; scrive solo i dati dell'utente)
ui/
  common.py                 tema, token di colore, CSS comune (mai "<" nel CSS), caricamento dei dati
  components.py             formati in italiano (fmt, num_column) e pezzi HTML: pannello, tabella fitta, stati vuoti,
                            avvisi, testata, riquadro del giudizio
  nav.py                    pagine, menu e tasti F1-F8
  shell.py                  cornice: barra dei comandi, tasti, briciole, menu "Dati", barra di stato
  rules.py                  editor delle regole (Esplora, Strategie) e tabelle dei giudizi
  pages/                    una pagina per file (Oggi = monitor.py); azienda/ = la scheda (verdetto, bilanci,
                            prezzo, valutazione, ricerca)
config.yaml                 tutti i parametri
data/db_manager.py          database SQLite (unico punto di accesso)
modules/
  config.py                 caricamento configurazione + default
  universe.py               universo: S&P 500 da Wikipedia (o lista fissa), CIK, settori
  sec_edgar.py              lettura dei bilanci SEC (XBRL companyfacts) con versioni e date di deposito
  sec_quarterly.py          tutti i valori depositati e trimestri singoli (dichiarati o per differenza)
  sec_filings.py            nuovi 10-Q/10-K dall'indice giornaliero della SEC
  share_classes.py          azioni delle aziende con più classi (es. Berkshire) dalla copertina dei 10-Q/10-K
  scheduler.py              aggiornamento automatico sul Mac (launchd)
  updater.py                aggiornamento in un processo separato (pulsante della webapp e automatico)
  http_client.py            richieste HTTP con limite al secondo, nuovi tentativi e timeout
  data_ingestor.py          download prezzi Yahoo a blocchi, split, aggiornamento bilanci
  data_quality.py           controlli di qualità su prezzi e bilanci
  market_data.py            dati in memoria con slicing point-in-time
  regime_detection.py       HMM gaussiano (numpy, nessuna dipendenza esterna)
  fundamental.py            beta, WACC, DCF, reverse DCF
  multi_factor.py           momentum, quality, low volatility
  confidence_engine.py      voto pesato
  threshold_manager.py      soglie per regime
  portfolio_optimizer.py    risk parity / minima varianza long-only
  execution_engine.py       ordini simulati (paper trading)
  strategy.py               la pipeline completa, condivisa da analisi e backtest
  backtester.py             backtest point-in-time + metriche
  research.py               ricerca dei segnali e dei fattori: IC, quantili, decadimento, walk-forward
  factors.py                fattori classici point-in-time e loro combinazione
  run_info.py               commit git, configurazione e versioni di ogni esecuzione
  insiders.py               operazioni degli insider (Form 4: archivi trimestrali SEC + depositi recenti)
  superinvestors.py         portafogli dei grandi investitori dai 13F, nome dell'emittente -> ticker
  dashboard_data.py         dati per la webapp (screener, bilanci, prezzi, valutazione)
  metrics.py                catalogo unico delle metriche di un'azienda, point-in-time
  screener.py               regole di ricerca (Esplora) e ricerche pronte
  method.py, formulas.py    il mio metodo: profilo, metriche personali (formule sicure), checklist
  valuation_model.py        valutazione a leve (Damodaran), DCF inverso, controlli di plausibilità
  cost_of_capital.py        costo del capitale: beta di settore, rating sintetico, premio implicito dell'S&P 500
  research_pdf.py           scheda di ricerca in PDF
  manual_data.py            dati di bilancio inseriti a mano e aziende fuori indice
  verdict.py                il giudizio: cinque pilastri di prove con soglie fisse, regole in chiaro (frasi in italiano)
  verdict_research.py       prova storica del giudizio (point-in-time 2013-2026): le Candidate non hanno battuto
                            l'indice, quindi è una checklist e non un segnale provato
  changes.py                cosa è cambiato a ogni bilancio (confronto point-in-time)
  alerts.py                 avvisi con storico (tabelle alerts e price_targets)
  news.py                   notizie da 22 feed pubblici (ritardo 1-15 minuti), collegate ai ticker
  exchanges.py              borse mondiali: orari e festività calcolate a regole
  etf.py                    dentro gli ETF: prime 10 posizioni e settori, esposizione vera, sovrapposizioni
  technical.py              indicatori tecnici
  lab.py                    laboratorio: parametri modificabili, esperimenti, preset
  strategies.py             strategie a regole: catalogo, definizioni, traduzione per il motore
  investors.py              le 20 strategie dei grandi investitori, con le schede
  commands.py               la barra dei comandi della webapp (ticker, funzioni della scheda, confronti, pagine)
  quotes.py                 quotazioni del giorno per il Monitor (Yahoo, in un thread)
  data_audit.py             verifica dei dati contro Yahoo (bilanci) e Nasdaq (prezzi)
  europe.py                 FTSE MIB ed Euro Stoxx 50: elenco, codici LEI, bilanci ESEF
  portfolio.py              il portafoglio reale inserito a mano: posizioni, P/L in euro, dividendi, tasse stimate
  journal.py                diario delle decisioni e verifica con il senno di poi
  report.py                 report HTML e CSV
  demo_data.py              dati simulati per prove offline e test
safety/circuit_breaker.py   blocchi di sicurezza
tools/verify_data.py        verifica a campione dei dati (report in reports/verifica_dati_*.md)
tools/verdict_backtest.py   prova storica del giudizio (report in reports/)
tools/schedule.py           aggiornamento automatico (launchd); tools/build_mac_app.py: l'app per Mac
tests/                      test automatici (pytest)
```

---

## Limiti noti

- **Survivorship bias (ridotto, non eliminato)**: la composizione storica dell'indice viene da Wikipedia
  ("Historical components of the S&P 500"), quindi a ogni data il backtest usa i titoli che c'erano
  davvero, ex membri compresi. Gli ex membri ancora quotati hanno i prezzi; quelli delistati (acquisiti
  o falliti) no, e Yahoo non li fornisce. Il backtest misura la **copertura**: circa 71% dei membri
  con un prezzo nel 2012, 91% nel 2020, 99% oggi. La tabella dei cambi è completa solo dal 2010-2011,
  per questo il backtest lungo parte dal 2012. Per eliminare del tutto il bias servono i prezzi dei
  delistati (fornitore a pagamento, es. Sharadar).
- **Etichette XBRL**: le aziende usano voci diverse per la stessa grandezza. Il lettore copre le più
  comuni; il debito totale è ricostruito dalle componenti (leasing operativo escluso). Alcune aziende
  possono risultare senza qualche metrica (il report indica i titoli "senza DCF").
- **Errori nei dati SEC**: alcune aziende dichiarano le azioni in milioni o migliaia invece che in unità;
  il lettore le corregge confrontandole con la mediana delle azioni di copertina dei documenti dello
  stesso anno. Chi usa solo voci contabili proprie per il capex (es. DLR, NEE, PSX) resta senza free
  cash flow e quindi senza DCF.
- **Riorganizzazioni societarie**: se un'azienda passa a una nuova holding, Wikipedia indica il CIK
  nuovo, che non ha ancora 10-K. Il controllo qualità segnala "bilanci assenti"; il vecchio CIK si
  aggiunge in `sec.extra_ciks` (già fatto per XOM).
- **Ultimi 12 mesi**: se 10-Q e 10-K usano etichette diverse per la stessa voce, il valore TTM può essere
  incoerente (ricavi o margine lordo non positivi vengono scartati). Senza l'esercizio precedente (aziende
  appena quotate) del 10-Q restano solo stato patrimoniale e azioni.
- **Dividendi**: i prezzi sono rettificati anche per i dividendi (`auto_adjust`), quindi i prezzi
  passati usati nel DCF del backtest sono un po' più bassi di quelli reali (di circa il rendimento da
  dividendo cumulato); inoltre i dividendi successivi al primo download non rettificano lo storico salvato.
- **Azioni da Yahoo** (`fundamentals_source: yahoo`): si assume che siano già sulla base attuale e non
  vengono corrette per gli split (da verificare).
- **Prezzi Yahoo**: fonte non ufficiale; con molti ticker può limitare le richieste. Il sistema ritenta,
  e i ticker mancanti vengono recuperati all'esecuzione successiva.
- **Il regime HMM è sensibile**: piccoli cambi di dati o parametri possono spostare il regime,
  e il regime pesa molto sulle decisioni. Va tenuto d'occhio nei backtest.

---

## Roadmap (dal Master Blueprint originale)

| Fase | Stato |
|---|---|
| 1.1 Schema SQLite (prezzi, bilanci, journal) | ✅ |
| 1.2 Pipeline di ingestione | ✅ incrementale, a blocchi, con nuovi tentativi |
| 1.3 Point-in-time engine | ✅ date di deposito SEC + membership storica S&P 500 |
| 1.x Indicatori macro (Fed, inflazione, curva) | ⏳ solo Treasury 10Y |
| 2.1 Hidden Markov Models | ✅ |
| 2.2 Multi-factor model | ✅ |
| 2.3 GARCH per la volatilità | ⏳ |
| 2.4 Correlazioni cross-asset (oro, bond, petrolio) | ⏳ |
| 3.1 WACC dinamico | ✅ |
| 3.2 DCF deterministico | ✅ |
| 3.3 Reverse DCF | ✅ |
| 3.4 Stress test macro | ⏳ |
| 4 Orchestrazione e decision layer | ✅ (paper trading) |
