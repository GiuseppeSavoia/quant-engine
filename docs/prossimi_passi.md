# Prossimi passi (passaggio di consegne al 2026-11-22)

Punto di partenza per una nuova chat. Contesto completo in `CLAUDE.md`; storia in `docs/storia_decisioni.md`;
specifica grafica in `docs/revisione_grafica.md`. Ultimo commit: `c5930d9`, albero pulito, 558 test verdi.

## Stato in breve
- App Streamlit sul Mac rifatta: pagina "Oggi", scheda azienda con il Giudizio in cima, Esplora/Confronta,
  Portafoglio con "Esposizione vera" negli ETF, Diario, Notizie (22 feed RSS + sezione X), temi Sistema/Scuro/Chiaro.
- Giudizio (`verdict.py`) = checklist, NON segnale provato: prova storica 2013-2026 rifatta dopo le correzioni ai dati,
  Candidate -1,3% a 12 mesi contro l'indice a pesi uguali (t -1,4). Storico dei giudizi salvato ogni sera
  (`verdict_history`), misura in avanti pronta (`tools/verdict_tracking.py`: servono mesi di storia).
- Dati: prezzi fermi corretti (164 titoli), interessi/EBIT/ammortamenti da più etichette SEC ("dati insufficienti"
  sull'S&P 500 da 47 a 26), bilanci europei ESEF migliorati (16 tedesche senza bilanci: decisione dell'utente).
- Sito pubblico statico "Registro Aperto" (`modules/public_site.py`, `tools/build_site.py`, output `build/sito/`):
  pronto ma NON online e `public_site.enabled: false`.
- Aggiornamento serale a passi con riepilogo (`modules/pipeline.py`); strategia automatica originale solo con `--strategy`.

## Da fare, in ordine
1. ✅ **13F con unità sbagliata, alla fonte** (2026-11-22): `superinvestors.unit_factors`/`fix_units`, usata
   in ingestione e nel sito; `tools/fix_13f_units.py` ha corretto 46 depositi nel database reale (backup prima).
2. **Mettere online il sito**: servono dall'utente la firma (`public_site.author`) e un account gratuito
   (Cloudflare Pages: `npx wrangler login`, `npx wrangler pages deploy build/sito --project-name registro-aperto`;
   oppure GitHub Pages con un repository separato e `rsync` di `build/sito`), poi `public_site.base_url` e
   `enabled: true`. Ricordare all'utente il parere legale (pubblica importi e giudizi: regole su raccomandazioni
   d'investimento e conflitti d'interesse). Mai pubblicare `sec.user_agent` (contiene la sua email).
3. ✅ **Verifica nel browser sui dati reali** (2026-11-22): menu Dati (ultimo aggiornamento, passi), "Segui il sistema"
   (chiaro/scuro seguono il sistema; preferenza dell'utente rimessa su Scuro), Fondi dopo la correzione 13F, GP con
   "vs SPY", sito da `tools/build_site.py`. Trovato e corretto: la copia veloce della webapp (`data/cache/`) non si
   rinnovava dopo `fix_units` (l'impronta `data_version` ora include valori e ticker dei 13F).
4. ✅ **Piccoli punti aperti** (2026-11-22): formati solo in `modules/formats.py` (ui/components li riesporta);
   `public_site.HISTORICAL_TEST` = -1,3%, t -1,4; chiave di cache della webapp anche sui servizi; GP con "vs SPY":
   Operazioni e 10-K spenti e bloccati come le medie.

5. ✅ **Dati mancanti** (2026-11-22): bilanci di riserva da Yahoo (`modules/yahoo_fundamentals.py`, passo
   `bilanci_riserva`), "dati insufficienti" da 83 a 28; nessun numero inserito a memoria. Da tenere d'occhio: le
   Candidate che poggiano su esercizi interi di Yahoo (BPE, BZU, ISP, MC.PA, TEN).
6. ✅ **Beta di settore solo sull'S&P 500** (2026-11-22): le europee (beta contro l'SPY, troppo basso) entravano nelle
   mediane e cambiavano il costo dell'equity, quindi il P/TBV giustificato, delle banche USA (CB, SCHW...). Ora le
   mediane sono solo USA e le europee usano quella del loro settore; 9 giudizi su 588 cambiati. Beta contro un indice
   europeo: fatto al punto 7.

7. ✅ **Cinque migliorie** (2026-11-23): provenienza dei dati (FA con "*" sulle celle di riserva, nota nel Verdetto:
   78 aziende, 25 Candidate); Prezzo solo freno (Candidate 133 -> 183, prova storica -0,8% a 12 mesi, NON provato:
   misurare in avanti con `tools/verdict_tracking.py`); beta delle europee contro l'Euro Stoxx 50, settori ICB tradotti
   in GICS per i gruppi, banche europee senza correzione per la cassa (48 europee su 85 con un gruppo europeo, costo
   dell'equity +0,6 punti, 3 giudizi cambiati;
   DOPO l'aggiornamento serale controllare che `price_history` abbia righe per `^STOXX50E`: se il primo scarico
   fallisce il simbolo resta segnato "già chiesto" in `fetch_log` e non si riprova, cancellare quella riga); avviso "ultimo periodo a metà" (16 aziende, quasi
   tutte europee con il 2025 solo da Yahoo); avviso di riavvio quando il codice cambia. Avvisi di pandas: nessuno (581
   test e log degli aggiornamenti puliti: quelli visti venivano da uno script di prova).
   Aperti: correzione per la cassa delle finanziarie USA (+30-40% sul beta); tasso privo di rischio in euro per le
   europee (oggi Treasury USA: DCF in euro scontati a tassi in dollari); i gruppi di confronto del GIUDIZIO per le
   europee usano ancora i settori ICB/GICS misti ("Banks" e "Financials" separati).

8. ✅ **Strumento di lavoro** (2026-11-23): export Excel con formule e PDF di sintesi, pipeline con "fatto quando",
   stime degli analisti (CSV + Yahoo, solo informazione), link dei bilanci ai depositi SEC, Oggi più pulita, aspetto
   più calmo. Da fare: aprire una volta il modello in Excel a mano (lo script si blocca sul permesso dei file della
   sandbox); dopo qualche giorno controllare le revisioni delle stime; eventuale foglio "Stime" nel file Excel.

9. ✅ **Corsi applicati al motore** (2026-11-23): test LBO e distanza dal default (Merton), metriche, riquadro nella
   scheda, foglio Excel "LBO"; prova storica pre-registrata: nessun segnale provato (vedi CLAUDE.md e
   `reports/metriche_storiche_20261123.md`). Da valutare: crescita dell'EBITDA o multiplo di uscita di settore nel test
   LBO (solo come lettura, NON tarati sui risultati); giorni di incasso/magazzino (servono voci SEC nuove e
   `--force-fundamentals`).

## Decisioni che restano all'utente
- Lettura dei documenti con l'API di Claude (chiave e limite di spesa).
- Uso al lavoro: compliance su strumenti personali e operazioni personali; dati Yahoo/Wikipedia senza licenza
  professionale (per il lavoro usare l'import CSV dai sistemi della banca).
- Fornitore di prezzi a pagamento (anche prezzi dei delistati per test più onesti).
- Account X: il riquadro ufficiale mostra i post solo a chi è collegato; l'alternativa è l'API a pagamento.

## Come lavorare
- Regole globali dell'utente (`~/.claude/CLAUDE.md`): concisione, modifiche minime, sotto-agenti Haiku solo per ricerca
  e test, advisor nei momenti decisivi. In questa chat l'utente aveva chiesto la "modalità direttore" (sotto-agenti
  Opus 5.5 che eseguono): chiedere quale preferisce se il lavoro è grande.
- Una sola sessione di Claude per cartella: il 2026-11-16 un'altra sessione (Sonnet) ha fatto commit di lavoro a metà.
- Mai scrivere nel database reale per prove; demo su copie (`config.demo_db_path`). Dopo modifiche al codice
  riavviare il server Streamlit.
