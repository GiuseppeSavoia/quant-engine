"""
La barra dei comandi della webapp (stile terminale): una riga di testo diventa una pagina da aprire.

    AAPL                 scheda di Apple (anche con il nome: APPLE, "coca cola")
    AAPL FA              una funzione della scheda (FA = bilanci, RV = concorrenti...: vedi FUNCTIONS)
    FA, 3                la funzione (o la voce 3 del menu) dell'azienda già aperta
    AAPL MSFT GOOGL      confronto tra le aziende (da 2 a 5)
    CMP AAPL MSFT        idem, con il comando esplicito (anche CONFRONTA)
    SEGUI AAPL           aggiunge Apple alle tue aziende (TOGLI per toglierla)
    FONDI, ESPLORA, ...  le pagine (vedi PAGES), anche con le sigle (EQS, 13F, BT...)
    AIUTO                l'elenco dei comandi

Solo logica, niente interfaccia: `parse` restituisce cosa fare e app.py lo esegue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# pagina (url_path di app.py) -> parole che la aprono (niente sigle di una lettera: sono ticker, es. A, D, T)
PAGES: dict[str, tuple[str, ...]] = {
    "": ("OGGI", "MONITOR", "MON", "HOME", "BLP", "LAUNCHPAD", "TODAY"),
    "mie-aziende": ("MIE", "WATCH", "LISTA", "WATCHLIST", "MINE"),
    "pipeline": ("PIPELINE", "PIPE", "IDEE", "IDEAS"),
    "notizie": ("NOTIZIE", "NEWS", "TOP", "CN", "8K", "8-K"),
    "azienda": ("SCHEDA", "COMPANY"),
    "portafoglio": ("PORT", "PORTAFOGLIO", "PRT", "PORTFOLIO"),
    "diario": ("DIARIO", "JOURNAL", "DECISIONI", "DECISIONS"),
    "esplora": ("ESPLORA", "EQS", "SCREEN", "FILTRI", "EXPLORE", "SCREENER"),
    "confronta": ("CONFRONTA", "CMP", "COMP", "COMPARE"),
    "fondi": ("FONDI", "13F", "INVESTITORI", "GURU", "FUNDS"),
    "metodo": ("METODO", "CHECKLIST", "METHOD"),
    "dati": ("DATI", "INSERISCI", "DATAENTRY"),
    "strategie": ("STRATEGIE", "STRAT", "STR", "STRATEGIES"),
    "strategia-automatica": ("AUTO", "AUTOMATICA", "ORDINI", "ORDERS"),
    "backtest": ("BACKTEST", "BT", "RICERCA", "RESEARCH"),
    "laboratorio": ("LAB", "LABORATORIO", "LABORATORY"),
    "qualita": ("QUALITA", "QUALITÀ", "DQ", "DEPOSITI", "SEC", "QUALITY", "FILINGS"),
    "limiti": ("LIMITI", "CAUTELE", "AVVERTENZE", "LIMITS", "CAVEATS", "DISCLAIMER"),
}
# funzioni della scheda azienda, nell'ordine del menu numerato (1 = DES...): codice -> nome
FUNCTIONS: list[tuple[str, str]] = [
    ("PRV", "Prove"), ("FA", "Bilanci"), ("GP", "Grafico"), ("DCF", "Valutazione"), ("RV", "Concorrenti"),
    ("CF", "Notizie e documenti"), ("ERN", "Trimestri"), ("DVD", "Dividendi e riacquisti"), ("HDS", "Insider e fondi"),
    ("NOTE", "La mia ricerca"), ("DES", "Tutte le metriche"), ("AUTO", "Strategia automatica (sperimentale)"),
    ("EE", "Stime degli analisti"), ("TAV", "Tavolo dei grandi investitori"),
]
# i codici non cambiano mai (AAPL FA, AAPL DCF... restano validi); i numeri seguono l'ordine del menu (dal 2026-11-14
# le prime sei sono le voci principali della scheda, le altre stanno in "Altro")
FUNCTION_ALIASES = {"INV": "TAV", "TAVOLO": "TAV", "INVESTITORI": "TAV", "STIME": "EE", "EST": "EE", "CONSENSO": "EE", "INS": "HDS", "OWN": "HDS", "DIV": "DVD", "PEER": "RV", "PEERS": "RV", "VAL": "DCF",
                    "BIL": "FA", "CN": "CF", "NEWS": "CF", "8K": "CF", "8-K": "CF",
                    "PROVE": "PRV", "VERDETTO": "PRV", "GIUDIZIO": "PRV", "METRICHE": "DES", "PROFILO": "DES",
                    # in inglese (interfaccia in inglese, 2026-11-24)
                    "EVIDENCE": "PRV", "VERDICT": "PRV", "FIN": "FA", "CHART": "GP", "VALUATION": "DCF",
                    "DOCS": "CF", "EARNINGS": "ERN", "QUARTERS": "ERN", "DIVIDENDS": "DVD", "INSIDERS": "HDS",
                    "NOTES": "NOTE", "METRICS": "DES", "ESTIMATES": "EE", "INVESTORS": "TAV", "PANEL": "TAV"}
FUNCTION_CODES = {code for code, _ in FUNCTIONS}
HELP_WORDS = ("AIUTO", "HELP", "?", "COMANDI")
FOLLOW_WORDS = {"SEGUI": True, "FOLLOW": True, "TOGLI": False, "UNFOLLOW": False}
MAX_COMPARE = 5


@dataclass
class Command:
    page: str | None = None             # url_path della pagina da aprire ("" = home)
    tickers: list[str] = field(default_factory=list)
    follow: bool | None = None          # True/False: segui o togli `tickers`
    section: str | None = None          # funzione della scheda azienda (codice di FUNCTIONS); senza ticker:
                                        # l'azienda già aperta
    help: bool = False
    error: str | None = None


def _function_of(word: str) -> str | None:
    return word if word in FUNCTION_CODES else FUNCTION_ALIASES.get(word)


def _page_of(word: str) -> str | None:
    for page, words in PAGES.items():
        if word in words:
            return page
    return None


def _resolve(token: str, tickers: set[str], names: dict[str, str]) -> list[str]:
    """Ticker esatto (BRK.B = BRK-B), altrimenti le aziende il cui nome contiene il testo."""
    t = token.upper()
    for cand in (t, t.replace(".", "-"), t.replace("-", ".")):
        if cand in tickers:
            return [cand]
    words = t.split()
    return sorted(k for k, n in names.items() if all(w in str(n).upper() for w in words))


def parse(text: str, tickers: list[str], names: dict[str, str]) -> Command:
    """Interpreta il comando `text`. `tickers`: quelli disponibili; `names`: ticker -> nome dell'azienda."""
    text = (text or "").strip().lstrip("/").strip()  # "/" porta alla barra: se finisce nel testo, si ignora
    if not text:
        return Command(error="Scrivi un ticker, un nome o un comando (AIUTO per l'elenco).")
    known = set(tickers)
    # tra virgolette: un nome con spazi ("coca cola")
    quoted = re.findall(r'"([^"]+)"', text)
    words = re.sub(r'"[^"]+"', " ", text).upper().split()
    if any(w in HELP_WORDS for w in words):
        return Command(help=True)
    # il nome completo così come lo mostra l'app ("Demo AMZN", "Bank of America Corp"): prima di spezzarlo in parole,
    # che potrebbero essere anche ticker (AMZN) o nomi di altre aziende
    whole = " ".join(re.sub(r'"', " ", text).upper().split())
    exact = [t for t, n in names.items() if n and " ".join(str(n).upper().split()) == whole and t in known]
    if len(exact) == 1 and len(words) > 1:
        return Command(page="azienda", tickers=exact)
    if len(words) == 1 and not quoted and words[0].isdigit():  # voce del menu numerato della scheda
        n = int(words[0])
        if not 1 <= n <= len(FUNCTIONS):
            return Command(error=f"Il menu della scheda va da 1 a {len(FUNCTIONS)}.")
        return Command(page="azienda", section=FUNCTIONS[n - 1][0])
    follow = next((FOLLOW_WORDS[w] for w in words if w in FOLLOW_WORDS), None)
    functions = [f for f in (_function_of(w) for w in words) if f is not None]
    pages = [p for p in (_page_of(w) for w in words) if p is not None]
    rest = [w for w in words if w not in FOLLOW_WORDS and _page_of(w) is None and _function_of(w) is None]

    found: list[str] = []
    unknown: list[str] = []
    for token in rest:
        hit = _resolve(token, known, {})
        (found if hit else unknown).append(hit[0] if hit else token)
    # parole che non sono ticker: forse un nome di più parole ("COCA COLA") o tra virgolette
    for name in quoted + ([" ".join(unknown)] if unknown else []):
        hits = _resolve(name, set(), names)
        if len(hits) == 1:
            found.append(hits[0])
            unknown = [] if name == " ".join(unknown) else unknown
        elif len(hits) > 1:
            shown = ", ".join(f"{h} ({names[h]})" for h in hits[:6])
            return Command(error=f"\"{name.title()}\" corrisponde a più aziende: {shown}"
                                 + ("…" if len(hits) > 6 else "") + ". Scrivi il ticker.")
        else:
            return Command(error=f"Non trovo \"{name}\": né un ticker né un nome di azienda né un comando.")
    found = list(dict.fromkeys(found))

    if follow is not None:
        if not found:
            return Command(error="Segui o togli quale azienda? Es. SEGUI AAPL")
        return Command(page="", tickers=found, follow=follow)
    # funzione della scheda: con un ticker, o da sola se non è anche il nome di una pagina (AUTO, CN)
    ambiguous = {w for w in words if _function_of(w) and _page_of(w) is not None}
    if functions and (found or not ambiguous):
        return Command(page="azienda", tickers=found[:1], section=functions[0])
    page = pages[0] if pages else None
    if page == "confronta" or (page is None and len(found) >= 2):
        if len(found) > MAX_COMPARE:
            return Command(error=f"Si confrontano al massimo {MAX_COMPARE} aziende.")
        return Command(page="confronta", tickers=found)
    if found:
        return Command(page="azienda" if page in (None, "azienda") else page, tickers=found[:1])
    if page is not None:
        return Command(page=page)
    return Command(error="Comando non riconosciuto (AIUTO per l'elenco).")


HELP_TEXT = """\
**Cerca**: scrivi nella barra in alto e premi Invio. Funziona con il ticker, il nome o un comando; maiuscole e
minuscole non contano.

| Scrivi | Cosa fa |
|---|---|
| `AAPL` oppure `apple` o `"coca cola"` | apre la scheda dell'azienda |
| `AAPL DCF`, `AAPL FA`, `AAPL RV`... | apre una funzione della scheda (elenco sotto) |
| `DCF` oppure `4` | la funzione (o la voce del menu) dell'azienda già aperta |
| `AAPL MSFT GOOGL` oppure `CMP AAPL MSFT` | confronta le aziende (da 2 a 5) |
| `SEGUI AAPL` / `TOGLI AAPL` | aggiunge o toglie l'azienda dalle tue, senza cambiare pagina |
| `OGGI`, `MIE`, `PORT`, `DIARIO`, `NOTIZIE` (`CN`), `ESPLORA` (`EQS`), `FONDI` (`13F`), `STRATEGIE`, `BT`, `LAB`, `DQ` | apre la pagina |

**Tasti**

| Tasto | Pagina |
|---|---|
| `F1` | Oggi (le tue aziende, avvisi, notizie, mercati) |
| `F2` | Le mie aziende |
| `F3` | Scheda azienda |
| `F4` | Esplora (ricerca con filtri) |
| `F5` | Confronta |
| `F6` | Portafoglio |
| `F7` | Diario delle decisioni |
| `F8` | Notizie |
| `/` | torna alla barra di ricerca |

Sul Mac i tasti F1-F8 di solito vanno premuti insieme a `fn` (senza, regolano luminosità e volume); in alternativa
`Alt` (⌥) + `1`…`8` apre le stesse pagine. Strategie, Backtest, Grandi investitori, Laboratorio e le altre pagine
stanno nel menu in alto. Un ticker scritto in azzurro nelle tabelle è un link: un clic apre la scheda.

Funzioni della scheda: """ + " · ".join(f"`{i}` `{c}` {n}" for i, (c, n) in enumerate(FUNCTIONS, 1)) + """ (anche `INS` per gli
insider, `CN` per le notizie dell'azienda, `DIV` per i dividendi, `VERDETTO` per le prove del giudizio).
"""
