"""
Strategie ispirate ai grandi investitori: il loro metodo, come l'hanno descritto in libri, lettere e interviste,
tradotto in regole sui dati point-in-time dell'app (universo S&P 500, bilanci SEC, prezzi).

Ogni voce ha la definizione (come le altre strategie del catalogo, vedi strategies.py) e una scheda:
  who          chi è e quando ha investito
  source       dove ha descritto il metodo
  original     la regola originale, in breve
  translation  cosa cambia nella traduzione e cosa manca (giudizio, piccole aziende, dati storici...)

I parametri sono fissati PRIMA di guardare i risultati, prendendoli dalle fonti (o, dove la fonte non dà numeri,
da una lettura prudente). Quando una regola chiede 10 o 20 anni di storia si usano 5 esercizi: i dati SEC
partono dal 2009. Magic Formula (Greenblatt) e Piotroski sono già nel catalogo: qui ci sono solo le loro schede.
"""
from __future__ import annotations

from modules.strategies import NON_FINANCIAL, _rules

ALL_BUT_FINANCIALS = NON_FINANCIAL + ["Utilities"]
QUALITY_SECTORS = ["Communication Services", "Consumer Discretionary", "Consumer Staples", "Health Care",
                   "Industrials", "Information Technology"]
THIRTEENF_START = "2014-06-01"  # i 13F in XML partono dal 2013: serve almeno un trimestre precedente


def _r(metric: str, op: str, value: float) -> dict:
    return {"metric": metric, "op": op, "value": value}


def _entry(definition: dict, who: str, source: str, original: str, translation: str) -> dict:
    return {"kind": "rules", "group": "Grandi investitori", "definition": definition,
            "investor": {"who": who, "source": source, "original": original, "translation": translation}}


INVESTOR_STRATEGIES: dict[str, dict] = {
    "Graham – investitore difensivo": _entry(
        _rules("Graham – investitore difensivo",
               "Aziende solide, con utili e dividendi costanti, comprate a un prezzo moderato rispetto a utili e "
               "patrimonio. Se ne passano poche, il resto resta liquido.",
               [("pe_x_pb", 1.0)], positions=30, max_weight=0.10, rebalance_days=252,
               sectors=ALL_BUT_FINANCIALS,
               filters=[_r("profitable_years", ">=", 5), _r("dividend_years", ">=", 5),
                        _r("current_ratio", ">=", 2.0), _r("eps_cagr_5y", ">=", 0.03),
                        _r("pe", "<=", 15), _r("pe_x_pb", "<=", 22.5)]),
        "Benjamin Graham (1894-1976), padre dell'analisi finanziaria e maestro di Buffett; Graham-Newman "
        "Corporation dal 1936 al 1956.",
        "The Intelligent Investor (1949), i criteri per l'investitore difensivo.",
        "Azienda grande; current ratio almeno 2; utili positivi in ognuno degli ultimi 10 anni; dividendi pagati "
        "senza interruzioni per 20 anni; utili per azione cresciuti di almeno un terzo in 10 anni; P/E al massimo "
        "15 sulla media di 3 anni; P/E x P/B al massimo 22,5.",
        "Utili e dividendi: 5 esercizi di fila invece di 10 e 20 (i dati SEC partono dal 2009); crescita "
        "minima del 3% l'anno sui 5 esercizi (un terzo in 10 anni); P/E sugli ultimi 12 mesi, non sulla media di 3 "
        "anni. Esclusi Financials e Utilities: per loro il current ratio non ha senso (Graham aveva criteri a "
        "parte). Classifica per P/E x P/B, ribilanciamento annuale."),
    "Buffett – aziende eccellenti a prezzo ragionevole": _entry(
        _rules("Buffett – aziende eccellenti a prezzo ragionevole",
               "Redditività alta e costante, poco debito, utili in crescita; tra queste, quelle che rendono più "
               "cassa rispetto al prezzo. Pochi titoli, tenuti a lungo.",
               [("fcf_yield", 1.0), ("roic_avg_5y", 0.5)], positions=20, max_weight=0.08, rebalance_days=252,
               sectors=ALL_BUT_FINANCIALS,
               filters=[_r("roe_min_5y", ">=", 0.15), _r("net_debt_ebitda", "<=", 2.0),
                        _r("eps_cagr_5y", ">=", 0.05), _r("profitable_years", ">=", 5)]),
        "Warren Buffett (1930), Berkshire Hathaway dal 1965.",
        "Lettere agli azionisti di Berkshire Hathaway; R. Hagstrom, The Warren Buffett Way (1994).",
        "Un'azienda che capisce, con un vantaggio competitivo duraturo: ROE alto per molti anni senza troppo "
        "debito, utili prevedibili e in crescita, management onesto e capace, comprata sotto il suo valore "
        "(\"owner earnings\", cioè la cassa che genera per i proprietari).",
        "ROE minimo degli ultimi 5 esercizi almeno 15%, debito netto al massimo 2 anni di EBITDA, utili per azione "
        "in crescita di almeno il 5% l'anno e mai in perdita negli ultimi 5 esercizi; classifica per rendimento del "
        "free cash flow (le \"owner earnings\") e ROIC medio. Manca tutto il giudizio: vantaggio competitivo, "
        "management, capire il business. Esclusi Financials e Utilities (che Buffett possiede: banche, "
        "assicurazioni, energia) perché i loro rapporti di debito non sono confrontabili."),
    "Munger – poche aziende eccezionali": _entry(
        _rules("Munger – poche aziende eccezionali",
               "Le aziende con il rendimento sul capitale più alto e costante, margini alti e poco debito; "
               "portafoglio concentrato.",
               [("roic_avg_5y", 1.0), ("fcf_yield", 1.0)], positions=12, max_weight=0.12, max_sector=0.40,
               rebalance_days=252,
               filters=[_r("roic_avg_5y", ">=", 0.20), _r("gross_margin", ">=", 0.40),
                        _r("net_debt_ebitda", "<=", 2.0), _r("profitable_years", ">=", 5)]),
        "Charlie Munger (1924-2023), socio di Buffett in Berkshire Hathaway; prima, la sua società di "
        "investimento (1962-1975).",
        "Discorsi raccolti in Poor Charlie's Almanack (2005).",
        "\"Un'azienda meravigliosa a un prezzo equo è meglio di un'azienda equa a un prezzo meraviglioso\": "
        "pochissime aziende di altissima qualità, con un vantaggio duraturo, tenute per decenni.",
        "ROIC medio degli ultimi 5 esercizi almeno 20%, margine lordo almeno 40%, poco debito, sempre in utile; "
        "classifica a metà tra ROIC e rendimento del free cash flow (il prezzo equo); 12 titoli, ribilanciati una "
        "volta l'anno (Munger quasi non vendeva). Manca il giudizio sul vantaggio competitivo."),
    "Lynch – crescita a prezzo ragionevole": _entry(
        _rules("Lynch – crescita a prezzo ragionevole",
               "Aziende che crescono tra il 10 e il 50% l'anno, con poco debito, pagate meno della loro crescita "
               "(PEG sotto 1).",
               [("peg", 1.0)], positions=30, max_weight=0.06, rebalance_days=63, sectors=ALL_BUT_FINANCIALS,
               filters=[_r("peg", "<=", 1.0), _r("eps_cagr_5y", ">=", 0.10), _r("eps_cagr_5y", "<=", 0.50),
                        _r("debt_to_equity", "<=", 0.8)]),
        "Peter Lynch (1944), gestore del fondo Fidelity Magellan dal 1977 al 1990.",
        "One Up on Wall Street (1989), Beating the Street (1993).",
        "P/E non più alto della crescita degli utili (PEG sotto 1, meglio sotto 0,5); crescita tra il 20 e il 25% "
        "ideale, oltre il 50% è sospetta; poco debito; scorte che non crescono più delle vendite; conoscere "
        "l'azienda e il suo \"racconto\".",
        "PEG = P/E degli ultimi 12 mesi / crescita annua degli utili per azione sui 5 esercizi; crescita tra 10 e "
        "50%; debito/patrimonio al massimo 0,8; i 30 con il PEG più basso, ogni trimestre. Mancano il controllo "
        "delle scorte e il \"racconto\". Lynch trovava molte idee tra le piccole aziende, fuori dall'S&P 500."),
    "Philip Fisher – crescita di qualità": _entry(
        _rules("Philip Fisher – crescita di qualità",
               "Aziende con ricavi in crescita da anni, margini sopra il settore e in aumento, che investono in "
               "ricerca e non diluiscono gli azionisti. Tenute a lungo.",
               [("revenue_cagr_5y", 1.0), ("operating_margin", 1.0)], positions=25, max_weight=0.06,
               rebalance_days=252,
               filters=[_r("revenue_cagr_5y", ">=", 0.08), _r("operating_margin", "top_sector", 0.5),
                        _r("margin_trend", ">=", 0.0), _r("rnd_to_revenue", ">=", 0.02),
                        _r("share_change", "<=", 0.02)]),
        "Philip A. Fisher (1907-2004), Fisher & Co. dal 1931 al 1999; tra i primi a investire nella crescita.",
        "Common Stocks and Uncommon Profits (1958), i 15 punti.",
        "Prodotti con un mercato in crescita per anni, ricerca e sviluppo efficace, margini alti e in "
        "miglioramento, buona organizzazione commerciale, management capace e onesto, niente aumenti di capitale "
        "che diluiscono. Informazioni raccolte parlando con clienti, fornitori e concorrenti.",
        "Ricavi in crescita di almeno l'8% l'anno sui 5 esercizi, margine operativo nella metà migliore del "
        "settore e in aumento rispetto ai tre anni prima, spesa in R&S almeno il 2% dei ricavi (le aziende che non "
        "la dichiarano restano fuori), azioni aumentate al massimo del 2% in un anno; ribilanciamento annuale. "
        "Manca quasi tutto il lavoro qualitativo dei 15 punti."),
    "Templeton – massimo pessimismo": _entry(
        _rules("Templeton – massimo pessimismo",
               "Aziende sane che costano molto meno del solito rispetto ai propri utili e meno del mercato.",
               [("pe_vs_history", 1.0)], positions=30, max_weight=0.06, rebalance_days=126,
               filters=[_r("pe_vs_history", "<=", 0.7), _r("pe", "top_index", 0.5),
                        _r("debt_to_equity", "<=", 1.0), _r("profitable_years", ">=", 3)]),
        "Sir John Templeton (1912-2008), Templeton Growth Fund dal 1954.",
        "Interviste e scritti raccolti in The Templeton Touch (2012) e Investing the Templeton Way (2008).",
        "Comprare nel momento di massimo pessimismo: aziende il cui prezzo è sceso molto sotto il valore, "
        "confrontando il P/E con la storia dell'azienda e con le alternative, in tutto il mondo.",
        "P/E di oggi al massimo il 70% della mediana dei suoi P/E alle chiusure degli ultimi 5 esercizi, P/E nella "
        "metà più bassa dell'indice, debito/patrimonio al massimo 1, in utile negli ultimi 3 esercizi; i 30 più "
        "sotto la propria storia, ogni sei mesi. Templeton cercava in tutto il mondo: qui solo l'S&P 500."),
    "Neff – rendimento totale a basso P/E": _entry(
        _rules("Neff – rendimento totale a basso P/E",
               "Aziende poco care, con crescita moderata e un dividendo: il massimo di (crescita + dividendo) per "
               "ogni punto di P/E.",
               [("neff_ratio", 1.0)], positions=40, max_weight=0.05, rebalance_days=126,
               filters=[_r("pe", "top_index", 0.4), _r("eps_cagr_5y", ">=", 0.07), _r("eps_cagr_5y", "<=", 0.20),
                        _r("dividend_yield", ">=", 0.015)]),
        "John Neff (1931-2019), Vanguard Windsor Fund dal 1964 al 1995.",
        "John Neff on Investing (1999).",
        "P/E basso; crescita degli utili tra il 7 e il 20% (non di più: troppo cara e fragile); dividendo; "
        "\"rendimento totale\" (crescita + dividendo) diviso il P/E il più alto possibile, idealmente il doppio "
        "del mercato.",
        "P/E nel 40% più basso dell'indice, crescita annua degli utili per azione sui 5 esercizi tra 7 e 20%, "
        "dividendo almeno 1,5%; classifica per rapporto di Neff, 40 titoli, ogni sei mesi."),
    "Dreman – contrarian": _entry(
        _rules("Dreman – contrarian",
               "Il 20% più economico dell'indice per P/E, solo se finanziariamente solido, preferendo chi paga "
               "dividendi più alti.",
               [("dividend_yield", 1.0), ("earnings_yield", 1.0)], positions=30, max_weight=0.06,
               rebalance_days=126,
               filters=[_r("pe", "top_index", 0.2), _r("current_ratio", ">=", 1.5),
                        _r("debt_to_equity", "<=", 1.0), _r("roe", ">=", 0.10)]),
        "David Dreman (1936), Dreman Value Management, fondi Kemper-Dreman dagli anni '80.",
        "Contrarian Investment Strategies: The Next Generation (1998).",
        "Comprare le azioni più impopolari (il 20% con il P/E più basso tra le grandi aziende), purché solide: "
        "current ratio alto, poco debito, ROE e dividendo sopra la media; tenerle finché il P/E torna normale.",
        "P/E nel 20% più basso dell'indice, current ratio almeno 1,5, debito/patrimonio al massimo 1, ROE almeno "
        "10%; classifica per dividendo e rendimento degli utili; 30 titoli, ogni sei mesi. Le banche escono da "
        "sole (current ratio non dichiarato)."),
    "Schloss – valore vicino ai minimi": _entry(
        _rules("Schloss – valore vicino ai minimi",
               "Aziende che costano poco rispetto al patrimonio, con poco debito, vicine ai minimi dell'anno; "
               "molti titoli, tenuti a lungo.",
               [("pb", 1.0), ("from_52w_low", 1.0)], positions=40, max_weight=0.05, rebalance_days=252,
               filters=[_r("pb", "top_index", 0.2), _r("debt_to_equity", "<=", 0.5),
                        _r("from_52w_low", "<=", 0.30)]),
        "Walter Schloss (1916-2012), allievo di Graham; la sua società dal 1955 al 2000 circa.",
        "Factors Needed to Make Money in the Stock Market (16 regole, 1994).",
        "Prezzo vicino o sotto il patrimonio netto, poco o niente debito, titoli vicini ai minimi di più anni, "
        "molti titoli (anche 100) per diversificare, pazienza: tenerli in media 4 anni. Niente previsioni, niente "
        "incontri con il management.",
        "P/B nel 20% più basso dell'indice, debito/patrimonio al massimo 0,5, prezzo al massimo il 30% sopra il "
        "minimo di 52 settimane; classifica per P/B e vicinanza al minimo; 40 titoli, ribilanciati una volta "
        "l'anno. Schloss comprava soprattutto aziende piccole e poco seguite: nell'S&P 500 ce ne sono poche così."),
    "Klarman – margine di sicurezza": _entry(
        _rules("Klarman – margine di sicurezza",
               "Le aziende che generano più cassa rispetto a quanto costa comprarle tutte, debito compreso, con un "
               "bilancio prudente.",
               [("fcf_ev_yield", 1.0)], positions=20, max_weight=0.08, rebalance_days=126,
               filters=[_r("fcf_ev_yield", ">=", 0.06), _r("net_debt_ebitda", "<=", 2.5),
                        _r("profitable_years", ">=", 3)]),
        "Seth Klarman (1957), Baupost Group dal 1982.",
        "Margin of Safety (1991).",
        "Comprare solo con un ampio margine di sicurezza tra prezzo e valore prudente; valutare l'azienda sulla "
        "cassa che genera, non sugli utili contabili; evitare di perdere soldi prima ancora di guadagnarli; "
        "tenere liquidità se non c'è niente di abbastanza economico.",
        "Free cash flow / enterprise value almeno 6%, debito netto al massimo 2,5 anni di EBITDA, in utile negli "
        "ultimi 3 esercizi; i 20 con il rendimento più alto, ogni sei mesi; se ne passano meno, il resto resta "
        "liquido. Baupost investe molto anche in debito, situazioni speciali e aziende non quotate."),
    "Zweig – crescita che accelera": _entry(
        _rules("Zweig – crescita che accelera",
               "Utili in forte crescita e in accelerazione, ricavi che crescono, prezzo in salita, P/E non "
               "eccessivo.",
               [("earnings_q_growth", 1.0), ("mom_12_1", 1.0)], positions=30, max_weight=0.05, rebalance_days=63,
               filters=[_r("earnings_q_growth", ">=", 0.20), _r("earnings_q_accel", ">=", 0.0),
                        _r("revenue_growth", ">=", 0.05), _r("pe", "<=", 40), _r("mom_12_1", "top_index", 0.5)]),
        "Martin Zweig (1942-2013), Zweig Fund e newsletter The Zweig Forecast.",
        "Winning on Wall Street (1986).",
        "Utili trimestrali in crescita sostenuta e in accelerazione, ricavi che crescono almeno quanto gli utili, "
        "P/E non oltre 3 volte quello del mercato, forza relativa del titolo; e soprattutto \"non combattere la "
        "Fed e il trend\": esposizione ridotta quando tassi e mercato sono sfavorevoli.",
        "Crescita dell'utile dell'ultimo trimestre almeno 20% e non in rallentamento, ricavi +5%, P/E al massimo "
        "40, momentum nella metà migliore; 30 titoli ogni trimestre. Manca la parte sul mercato (tassi e trend) "
        "che per Zweig decideva quanto investire."),
    "O'Neil – CAN SLIM": _entry(
        _rules("O'Neil – CAN SLIM",
               "Aziende leader: utili trimestrali e annuali in forte crescita, prezzo vicino ai massimi e tra i più "
               "forti del mercato.",
               [("mom_12_1", 1.0), ("earnings_q_growth", 1.0)], positions=25, max_weight=0.06, rebalance_days=21,
               filters=[_r("earnings_q_growth", ">=", 0.25), _r("eps_cagr_5y", ">=", 0.15),
                        _r("drawdown_52w", ">=", -0.15), _r("mom_12_1", "top_index", 0.2)]),
        "William O'Neil (1933-2023), fondatore di Investor's Business Daily.",
        "How to Make Money in Stocks (1988).",
        "C: utili dell'ultimo trimestre +25% o più; A: utili annuali +25% negli ultimi 3 anni; N: prodotto, "
        "management o massimi nuovi; S: domanda e offerta di azioni; L: leader del settore (forza relativa alta); "
        "I: comprata da investitori istituzionali; M: mercato in tendenza positiva. Vendere subito con -7/8%.",
        "C: utile dell'ultimo trimestre +25%; A: utili per azione +15% l'anno sui 5 esercizi (25% su 3 anni "
        "lascerebbe pochissime aziende); N: prezzo entro il 15% dal massimo di 52 settimane; L: momentum nel 20% "
        "migliore; ogni mese. Mancano S, I (i 13F dei fondi seguiti non bastano), M e lo stop-loss del 7-8%: è la "
        "parte di trading del metodo."),
    "Driehaus – momentum e utili": _entry(
        _rules("Driehaus – momentum e utili",
               "I titoli più forti del mercato, purché gli utili crescano e stiano accelerando. Ogni mese.",
               [("mom_12_1", 1.0), ("earnings_q_accel", 0.5)], positions=30, max_weight=0.05, rebalance_days=21,
               filters=[_r("mom_12_1", "top_index", 0.2), _r("earnings_q_growth", ">=", 0.20),
                        _r("earnings_q_accel", ">=", 0.0)]),
        "Richard Driehaus (1942-2021), Driehaus Capital Management dal 1982.",
        "Intervista in J. Schwager, The New Market Wizards (1992).",
        "\"Comprare alto e vendere più alto\": titoli in forte salita con utili in crescita e sorprese positive, "
        "vendendo appena la storia peggiora; molta rotazione.",
        "Momentum nel 20% migliore, utile dell'ultimo trimestre +20% e in accelerazione; ribilanciamento mensile. "
        "Mancano le sorprese rispetto alle stime degli analisti (dati non disponibili); Driehaus lavorava molto "
        "sulle piccole aziende."),
    "O'Shaughnessy – Trending Value": _entry(
        _rules("O'Shaughnessy – Trending Value",
               "Il 10% più economico dell'indice su sei misure di prezzo insieme; tra questi, i 25 con il "
               "momentum migliore. Una volta l'anno.",
               [("mom_12_1", 1.0)], positions=25, max_weight=0.06, rebalance_days=252,
               filters=[_r("value_composite", "top_index", 0.1)]),
        "James O'Shaughnessy (1960), O'Shaughnessy Asset Management; ricercatore di strategie quantitative.",
        "What Works on Wall Street (1996; Trending Value nella 4a edizione, 2011).",
        "Value Composite: media dei percentili di P/E, P/B, prezzo/ricavi, EV/EBITDA, prezzo/cash flow e "
        "rendimento per gli azionisti; il 10% più economico di tutte le azioni quotate USA, poi i 25 con il "
        "miglior rendimento degli ultimi 6 mesi; ribilanciamento annuale.",
        "Stesso Value Composite (prezzo/free cash flow al posto di prezzo/cash flow), solo sull'S&P 500; momentum "
        "da 12 a 1 mese invece dei 6 mesi. Le sue ricerche mostrano risultati migliori sulle aziende più piccole."),
    "Ken Fisher – prezzo/ricavi": _entry(
        _rules("Ken Fisher – prezzo/ricavi",
               "Le aziende più economiche rispetto ai ricavi, con poco debito e un margine minimo: ricavi che "
               "costano poco e possono tornare redditizi.",
               [("ps", 1.0)], positions=30, max_weight=0.06, rebalance_days=126, sectors=NON_FINANCIAL,
               filters=[_r("ps", "top_index", 0.2), _r("debt_to_equity", "<=", 0.4),
                        _r("revenue_cagr_5y", ">=", 0.03), _r("net_margin", ">=", 0.03)]),
        "Ken Fisher (1950), figlio di Philip Fisher, Fisher Investments dal 1979.",
        "Super Stocks (1984), dove introduce il rapporto prezzo/ricavi (PSR).",
        "PSR molto basso (sotto 0,75 per le aziende industriali), crescita dei ricavi, debito basso, margini "
        "che possono tornare normali: aziende in difficoltà temporanea che il mercato ha abbandonato.",
        "Prezzo/ricavi nel 20% più basso dell'indice (nell'S&P 500 quasi nessuna azienda è sotto 0,75), "
        "debito/patrimonio al massimo 0,4, ricavi in crescita di almeno il 3% l'anno, margine netto almeno 3%; "
        "esclusi Financials e Utilities; ogni sei mesi."),
    "Terry Smith – Fundsmith": _entry(
        _rules("Terry Smith – Fundsmith",
               "Aziende con rendimento sul capitale alto per anni, margini lordi alti e utili che diventano cassa; "
               "tra queste, quelle che costano meno rispetto al free cash flow. Una volta l'anno.",
               [("fcf_yield", 1.0), ("roic_avg_5y", 1.0)], positions=25, max_weight=0.06, rebalance_days=252,
               sectors=QUALITY_SECTORS,
               filters=[_r("roic_avg_5y", ">=", 0.15), _r("gross_margin", ">=", 0.40),
                        _r("cash_conversion", ">=", 0.9), _r("net_debt_ebitda", "<=", 2.0)]),
        "Terry Smith (1953), Fundsmith Equity Fund dal 2010.",
        "Lettere annuali di Fundsmith; Investing for Growth (2020).",
        "\"Compra buone aziende, non pagarle troppo, non fare nulla\": ritorno sul capitale alto e costante, "
        "margini lordi alti, utili convertiti in cassa, poco debito, crescita da reinvestimento; niente banche, "
        "materie prime, utility, auto, compagnie aeree.",
        "ROIC medio dei 5 esercizi almeno 15%, margine lordo almeno 40%, cash flow operativo almeno il 90% "
        "dell'utile, debito netto al massimo 2 anni di EBITDA; solo i settori che Smith considera (niente "
        "Financials, Energy, Materials, Utilities, Real Estate); classifica per rendimento del FCF e ROIC; 25 "
        "titoli, ogni anno. Fundsmith investe anche fuori dagli USA."),
    "Slater – Zulu": _entry(
        _rules("Slater – Zulu",
               "Crescita forte a un prezzo basso rispetto alla crescita (PEG sotto 0,75), con il titolo già in "
               "salita, utili coperti da cassa e poco debito.",
               [("peg", 1.0)], positions=25, max_weight=0.06, rebalance_days=63, sectors=ALL_BUT_FINANCIALS,
               filters=[_r("peg", "<=", 0.75), _r("eps_cagr_5y", ">=", 0.15), _r("mom_12_1", "top_index", 0.5),
                        _r("cash_conversion", ">=", 1.0), _r("debt_to_equity", "<=", 0.5)]),
        "Jim Slater (1929-2015), investitore e autore britannico.",
        "The Zulu Principle (1992), Beyond the Zulu Principle (1996).",
        "PEG sotto 0,75 (meglio sotto 0,66), crescita degli utili di almeno il 15% per 4 anni, forza relativa "
        "positiva, cash flow per azione sopra gli utili, debito basso, qualcosa di nuovo nell'azienda; "
        "specializzarsi in un campo stretto (il principio \"Zulu\").",
        "PEG sotto 0,75 con la crescita annua degli utili per azione dei 5 esercizi almeno 15%, momentum nella "
        "metà migliore, cash flow operativo almeno pari all'utile, debito/patrimonio al massimo 0,5; ogni "
        "trimestre. Slater lavorava sulle piccole aziende britanniche."),
    "Pabrai – clonare i grandi investitori": _entry(
        {**_rules("Pabrai – clonare i grandi investitori",
                  "Le aziende che i grandi investitori seguiti dall'app (config.yaml, superinvestors) hanno appena "
                  "comprato o aumentato nei 13F. Portafoglio concentrato, aggiornato a ogni trimestre di 13F.",
                  [("funds_adding", 1.0), ("funds_holding", 1.0)], positions=10, max_weight=0.12,
                  rebalance_days=63, filters=[_r("funds_adding", ">=", 1)]),
         "start": THIRTEENF_START},
        "Mohnish Pabrai (1964), Pabrai Investment Funds dal 1999.",
        "The Dhandho Investor (2007); in molte interviste spiega che \"clona\" le idee di altri grandi "
        "investitori.",
        "Non serve inventare: guardare cosa comprano i migliori (dai 13F), studiare quelle idee e comprare le "
        "migliori, concentrandosi su pochi titoli.",
        "Le aziende con almeno un fondo seguito che ha aperto o aumentato la posizione nell'ultimo 13F già "
        "pubblico; classifica per numero di fondi che comprano e che la possiedono; 10 titoli. Backtest dal "
        "2014: i 13F in formato elettronico partono dal 2013. I 13F escono fino a 45 giorni dopo il trimestre: si "
        "compra con quel ritardo. Pabrai studiava a fondo ogni idea prima di comprarla."),
}

# schede per le due strategie di investitori già nel catalogo
EXISTING_SHEETS: dict[str, dict] = {
    "Magic Formula": {
        "who": "Joel Greenblatt (1957), Gotham Capital dal 1985; professore alla Columbia.",
        "source": "The Little Book That Beats the Market (2005).",
        "original": "Classificare le aziende per rendimento sul capitale (EBIT / capitale operativo) e per "
                    "rendimento degli utili (EBIT / enterprise value), sommare le due posizioni, comprare le 20-30 "
                    "migliori, tenerle un anno; esclusi finanziari e utility.",
        "translation": "ROIC e EV/EBIT del catalogo (il ROIC usa il capitale investito invece del capitale "
                       "operativo); 30 titoli, ogni trimestre; esclusi Financials e Utilities come nell'originale.",
    },
    "Piotroski": {
        "who": "Joseph Piotroski, professore di contabilità (Chicago, poi Stanford).",
        "source": "Value Investing: The Use of Historical Financial Statement Information to Separate Winners from "
                  "Losers (Journal of Accounting Research, 2000).",
        "original": "Tra le azioni con il P/B più basso (il 20%), comprare quelle con F-score 8-9 (nove controlli "
                    "su redditività, debito e efficienza) ed evitare quelle con 0-1.",
        "translation": "Stessi controlli sugli ultimi 12 mesi contro l'anno prima; solo l'S&P 500, mentre lo "
                       "studio trovava gran parte del guadagno nelle aziende piccole e poco seguite.",
    },
}
