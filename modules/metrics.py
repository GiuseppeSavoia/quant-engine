"""
Catalogo delle metriche di un'azienda: UNA sola definizione per ogni numero, usata dalla scheda azienda,
dalla ricerca con filtri e dalle regole delle strategie.

Tutto è point-in-time: `compute_metrics(data, tickers)` usa solo ciò che `data` contiene, quindi su
`MarketData.as_of(giorno)` restituisce i numeri che si potevano conoscere quel giorno (prezzi fino a quel
giorno, bilanci già depositati). I flussi (ricavi, utili, cash flow...) sono gli ultimi 12 mesi
(`MarketData.fundamentals`); le crescite confrontano con lo stesso trimestre dell'anno prima.

Convenzioni:
- un rapporto con denominatore non significativo resta vuoto (es. P/E con utili negativi, ROE con
  patrimonio negativo): meglio vuoto che un numero fuorviante;
- `better` dice in che direzione un valore è "migliore" (per le classifiche percentili), None se non c'è;
- i bilanci più vecchi di `max_age_days` (dalla chiusura del periodo) non vengono usati.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from modules import lbo as lbo_mod

TRADING_DAYS = 252
DEFAULT_TAX_RATE = 0.21
MAX_PRICE_GAP_DAYS = 5


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str
    fmt: str            # pct | x (multiplo) | num | money | int | date
    better: str | None  # "high" | "low" | None
    help: str = ""


METRICS: list[Metric] = [
    # dimensione
    Metric("market_cap", "Capitalizzazione", "Dimensione", "money", None, "Prezzo x azioni"),
    Metric("enterprise_value", "Enterprise value", "Dimensione", "money", None,
           "Capitalizzazione + debito - cassa: quanto costa comprare tutta l'azienda"),
    # valutazione
    Metric("pe", "P/E", "Valutazione", "x", "low", "Prezzo / utili degli ultimi 12 mesi (vuoto se utili negativi)"),
    Metric("earnings_yield", "Rendimento degli utili", "Valutazione", "pct", "high", "Utili / capitalizzazione"),
    Metric("p_fcf", "Prezzo / FCF", "Valutazione", "x", "low", "Capitalizzazione / free cash flow"),
    Metric("fcf_yield", "Rendimento del FCF", "Valutazione", "pct", "high", "Free cash flow / capitalizzazione"),
    Metric("ev_ebit", "EV / EBIT", "Valutazione", "x", "low", "Enterprise value / utile operativo"),
    Metric("ev_ebitda", "EV / EBITDA", "Valutazione", "x", "low", "Enterprise value / (utile operativo + ammortamenti)"),
    Metric("ev_sales", "EV / ricavi", "Valutazione", "x", "low"),
    Metric("pb", "Prezzo / patrimonio", "Valutazione", "x", "low"),
    Metric("dividend_yield", "Rendimento da dividendi", "Valutazione", "pct", "high", "Dividendi pagati / capitalizzazione (vuoto se non risulta nessun pagamento)"),
    Metric("shareholder_yield", "Rendimento per gli azionisti", "Valutazione", "pct", "high",
           "(Dividendi + riacquisti di azioni) / capitalizzazione"),
    Metric("ps", "Prezzo / ricavi", "Valutazione", "x", "low", "Capitalizzazione / ricavi (il PSR di Ken Fisher)"),
    Metric("pe_x_pb", "P/E x P/B", "Valutazione", "num", "low",
           "Il limite di Graham per l'investitore difensivo: al massimo 22,5 (P/E 15 per P/B 1,5)"),
    Metric("peg", "PEG", "Valutazione", "num", "low",
           "P/E diviso la crescita annua degli utili per azione (5 anni, in punti %): sotto 1 = crescita a buon "
           "prezzo (Lynch)"),
    Metric("neff_ratio", "Rapporto di Neff", "Valutazione", "num", "high",
           "(Crescita annua degli utili per azione + rendimento da dividendi, in punti %) / P/E"),
    Metric("fcf_ev_yield", "FCF / enterprise value", "Valutazione", "pct", "high",
           "Free cash flow / enterprise value: il rendimento di comprare tutta l'azienda, debito compreso"),
    Metric("lbo_irr", "Rendimento di un LBO al prezzo di oggi", "Valutazione", "pct", "high",
           "Test del private equity: un fondo paga il prezzo di oggi + 30%, si indebita fino a 6 volte l'EBITDA "
           "(copertura degli interessi almeno 2), usa tutta la cassa per ripagare il debito per 5 anni e rivende al "
           "multiplo EV/EBITDA di oggi (al massimo 12x), senza crescita. Rendimento annuo del fondo: sopra il 20% "
           "l'azienda costa poco rispetto alla cassa che produce. Vuoto per banche, assicurazioni e REIT riconosciuti dalle "
           "voci di bilancio (le assicurazioni che non le dichiarano, es. ALL, BRK-B, vanno ignorate); falsato per chi "
           "ha una finanziaria interna (F, GM, CAT, DE)"),
    Metric("pe_vs_history", "P/E rispetto alla sua storia", "Valutazione", "x", "low",
           "P/E di oggi / mediana del P/E alle chiusure degli ultimi 5 esercizi (almeno 3): sotto 1 = più "
           "economica del solito"),
    Metric("value_composite", "Economicità complessiva", "Valutazione", "pct", "high",
           "Media delle posizioni percentili su P/E, P/B, prezzo/ricavi, EV/EBITDA, prezzo/FCF e rendimento per "
           "gli azionisti (1 = la più economica dell'indice), come il \"Value Composite\" di O'Shaughnessy"),
    Metric("payout_ratio", "Quota degli utili pagata in dividendi", "Valutazione", "pct", None,
           "Dividendi / utile netto (ultimi 12 mesi)"),
    # redditività
    Metric("gross_margin", "Margine lordo", "Redditività", "pct", "high"),
    Metric("operating_margin", "Margine operativo", "Redditività", "pct", "high"),
    Metric("net_margin", "Margine netto", "Redditività", "pct", "high"),
    Metric("fcf_margin", "Margine FCF", "Redditività", "pct", "high", "Free cash flow / ricavi"),
    Metric("roe", "ROE", "Redditività", "pct", "high", "Utile netto / patrimonio netto"),
    Metric("roa", "ROA", "Redditività", "pct", "high", "Utile netto / attivo totale"),
    Metric("roic", "ROIC", "Redditività", "pct", "high",
           "Utile operativo dopo le tasse / capitale investito (patrimonio + debito - cassa)"),
    Metric("gross_profitability", "Margine lordo / attivo", "Redditività", "pct", "high", "Novy-Marx"),
    Metric("rnd_to_revenue", "Ricerca e sviluppo / ricavi", "Redditività", "pct", None,
           "Spesa in R&S sui ricavi (ultimi 12 mesi): vuota se l'azienda non la dichiara"),
    # crescita
    Metric("revenue_growth", "Crescita ricavi (1 anno)", "Crescita", "pct", "high"),
    Metric("earnings_growth", "Crescita utili (1 anno)", "Crescita", "pct", "high", "Vuoto se l'anno prima era in perdita"),
    Metric("fcf_growth", "Crescita FCF (1 anno)", "Crescita", "pct", "high"),
    Metric("revenue_cagr_3y", "Crescita ricavi annua (3 anni)", "Crescita", "pct", "high", "Dagli esercizi completi"),
    Metric("share_change", "Variazione azioni (1 anno)", "Crescita", "pct", "low",
           "Negativa = l'azienda riacquista azioni; positiva = diluizione"),
    Metric("earnings_q_growth", "Crescita utile dell'ultimo trimestre", "Crescita", "pct", "high",
           "Utile netto dell'ultimo trimestre / stesso trimestre dell'anno prima - 1 (vuoto se allora era in perdita)"),
    Metric("earnings_q_accel", "Accelerazione degli utili", "Crescita", "pct", "high",
           "Crescita trimestrale di oggi meno quella del trimestre prima: positiva = utili che accelerano"),
    # storia: più esercizi, per chi cerca costanza (Graham, Buffett, Munger, Fisher...)
    Metric("eps_cagr_5y", "Crescita annua utili per azione (5 anni)", "Storia", "pct", "high",
           "Dagli esercizi completi: 5 anni, o almeno 3 se la storia è più corta; vuota se uno dei due anni è in "
           "perdita"),
    Metric("revenue_cagr_5y", "Crescita annua ricavi (5 anni)", "Storia", "pct", "high",
           "Dagli esercizi completi: 5 anni, o almeno 3"),
    Metric("roe_min_5y", "ROE minimo (5 esercizi)", "Storia", "pct", "high",
           "Il ROE più basso degli ultimi 5 esercizi (almeno 3): alto = redditività costante, non di un anno"),
    Metric("roic_avg_5y", "ROIC medio (5 esercizi)", "Storia", "pct", "high", "Media degli ultimi 5 esercizi (almeno 3)"),
    Metric("margin_trend", "Tendenza del margine operativo", "Storia", "pct", "high",
           "Margine operativo dell'ultimo esercizio meno la media dei tre precedenti"),
    Metric("profitable_years", "Esercizi consecutivi in utile", "Storia", "int", "high",
           "Ultimi esercizi di fila con utile netto positivo (al massimo 10; i dati SEC partono dal 2009)"),
    Metric("dividend_years", "Esercizi consecutivi con dividendi", "Storia", "int", "high",
           "Ultimi esercizi di fila con dividendi pagati (al massimo 10)"),
    # solidità
    Metric("debt_to_equity", "Debito / patrimonio", "Solidità", "x", "low"),
    Metric("net_debt_ebitda", "Debito netto / EBITDA", "Solidità", "x", "low", "Anni di margine per ripagare il debito"),
    Metric("current_ratio", "Current ratio", "Solidità", "x", "high", "Attività correnti / passività correnti"),
    Metric("interest_coverage", "Copertura degli interessi", "Solidità", "x", "high", "Utile operativo / interessi passivi"),
    Metric("distance_to_default", "Distanza dal default (Merton)", "Solidità", "num", "high",
           "Il capitale è un'opzione sull'attivo con prezzo d'esercizio il debito (Black-Scholes; Damodaran): quante "
           "deviazioni standard separano il valore dell'attivo dal debito (leasing compresi) tra un anno, da "
           "capitalizzazione, volatilità e rendimento dell'ultimo anno (Bharath e Shumway 2008). Sotto 2 = rischio "
           "di insolvenza che il mercato prende sul serio; 10 = nessun debito. Vuota per banche, assicurazioni e REIT"),
    Metric("accruals", "Accantonamenti / attivo", "Solidità", "pct", "low",
           "(Utile netto - cash flow operativo) / attivo: alto = utili poco coperti da cassa"),
    # qualità degli utili
    Metric("cash_conversion", "Conversione dell'utile in cassa", "Qualità degli utili", "x", "high",
           "Cash flow operativo / utile netto: sotto 1 per anni = utili poco reali"),
    Metric("sbc_to_revenue", "Compensi in azioni / ricavi", "Qualità degli utili", "pct", "low",
           "Stipendi pagati in azioni: un costo vero che il free cash flow non sottrae"),
    Metric("fcf_after_sbc_yield", "Rendimento del FCF dopo i compensi in azioni", "Qualità degli utili", "pct",
           "high", "(Free cash flow - compensi in azioni) / capitalizzazione"),
    Metric("beneish_m", "Beneish M-score", "Qualità degli utili", "num", "low",
           "Probabilità di bilanci gonfiati da 8 indici (crediti, margini, attivo, crescita...): sopra -1,78 è un "
           "campanello d'allarme, da verificare"),
    # punteggi di qualità del bilancio
    Metric("piotroski", "Piotroski F-score (0-9)", "Punteggi", "int", "high",
           "Nove controlli sull'ultimo anno: utili e cash flow positivi, redditività in aumento, utili coperti da "
           "cassa, debito in calo, liquidità in aumento, niente nuove azioni, margine lordo e rotazione dell'attivo "
           "in aumento. 8-9 = bilancio in miglioramento, 0-2 = in peggioramento"),
    Metric("altman_z", "Altman Z-score", "Punteggi", "num", "high",
           "Rischio di dissesto dai numeri di bilancio: sopra 3 zona sicura, sotto 1,8 zona di pericolo. Pensato "
           "per le aziende industriali: non significativo per banche e assicurazioni"),
    # metriche di settore: per banche, assicurazioni e REIT i rapporti generali (P/E, EV/EBIT, FCF) dicono poco
    Metric("ptbv", "Prezzo / patrimonio tangibile", "Settore", "x", "low",
           "Capitalizzazione / (patrimonio - avviamento - intangibili): il multiplo principale delle banche"),
    Metric("rotce", "ROTCE", "Settore", "pct", "high",
           "Utile netto / patrimonio tangibile: la redditività delle banche, senza l'avviamento delle acquisizioni"),
    Metric("nim", "Margine di interesse su attivo", "Settore", "pct", "high",
           "Margine di interesse (interessi attivi - passivi) / attivo totale (banche)"),
    Metric("efficiency_ratio", "Rapporto costi / ricavi (banche)", "Settore", "pct", "low",
           "Costi operativi / ricavi netti: più basso = banca più efficiente (sotto il 55-60% è buono)"),
    Metric("provision_ratio", "Accantonamenti su crediti / ricavi", "Settore", "pct", "low",
           "Quanto dei ricavi va a coprire i prestiti che potrebbero non essere rimborsati (banche)"),
    Metric("loss_ratio", "Rapporto sinistri / premi", "Settore", "pct", "low",
           "Sinistri e prestazioni / premi di competenza (assicurazioni): più basso = sottoscrizione migliore"),
    Metric("ffo_yield", "Rendimento FFO", "Settore", "pct", "high",
           "FFO (utile + ammortamenti - plusvalenze sugli immobili, approssimato) / capitalizzazione: il \"free cash "
           "flow\" dei REIT. Solo per le aziende con immobili per oltre il 30% dell'attivo"),
    Metric("p_ffo", "Prezzo / FFO", "Settore", "x", "low", "Capitalizzazione / FFO (REIT)"),
    # insider e grandi investitori (dati SEC con la data di deposito: point-in-time)
    Metric("insider_buy_value", "Acquisti degli insider (90 giorni)", "Insider e fondi", "money", "high",
           "Valore degli acquisti sul mercato (Form 4, codice P) depositati negli ultimi 90 giorni"),
    Metric("insider_buyers", "Insider che hanno comprato (90 giorni)", "Insider e fondi", "int", "high",
           "Quanti insider diversi: più acquisti insieme sono il segnale più studiato"),
    Metric("insider_net_pct", "Acquisti netti degli insider / capitalizzazione (12 mesi)", "Insider e fondi", "pct",
           "high", "(Acquisti sul mercato - vendite non programmate) degli ultimi 12 mesi, sulla capitalizzazione"),
    Metric("funds_holding", "Grandi investitori che la possiedono", "Insider e fondi", "int", "high",
           "Tra i fondi seguiti (config.yaml), quanti la hanno nell'ultimo 13F"),
    Metric("funds_adding", "Grandi investitori che comprano", "Insider e fondi", "int", "high",
           "Fondi seguiti con una posizione nuova o aumentata nell'ultimo 13F"),
    # prezzo e rischio
    Metric("ret_1m", "Rendimento 1 mese", "Prezzo", "pct", None),
    Metric("ret_1y", "Rendimento 1 anno", "Prezzo", "pct", None),
    Metric("mom_12_1", "Momentum 12-1", "Prezzo", "pct", "high", "Rendimento da 12 a 1 mese fa"),
    Metric("volatility", "Volatilità annua", "Prezzo", "pct", "low", "Ultimo anno"),
    Metric("beta", "Beta", "Prezzo", "num", None, "Sensibilità al benchmark (ultimo anno)"),
    Metric("drawdown_52w", "Distanza dal massimo a 52 settimane", "Prezzo", "pct", None),
    Metric("from_52w_low", "Distanza dal minimo a 52 settimane", "Prezzo", "pct", "low",
           "Prezzo / minimo dell'ultimo anno - 1: vicino a zero = vicino ai minimi (Schloss)"),
    Metric("vs_sma200", "Prezzo vs media a 200 giorni", "Prezzo", "pct", None),
]
# ---------------------------------------------------------------- metriche dell'utente
# Le liste qui sotto vengono AGGIORNATE SUL POSTO (non sostituite), così chi le ha importate vede sempre le
# metriche personali registrate con `set_custom` (definite nella webapp, tabella custom_metrics).
BASE_METRICS = list(METRICS)
BASE_KEYS = {m.key for m in BASE_METRICS} | {"price"}
CUSTOM_GROUP = "Le mie metriche"
JUDGMENT_GROUP = "Il mio giudizio"
# il voto della tua checklist qualitativa: esiste solo oggi, quindi mai nei backtest (lo riempie la webapp)
JUDGMENT = Metric("my_checklist", "Il mio voto (checklist, 1-5)", JUDGMENT_GROUP, "num", "high",
                  "Media pesata dei voti che hai dato nella scheda \"Il mio giudizio\"")
# a quanti investitori del tavolo (modules/investor_panel.py) l'azienda passa tutte le regole: calcolata solo sui dati di
# oggi dalla webapp; vietata nelle strategie come il voto (una strategia che usa il tavolo sarebbe circolare)
INVESTORS_LIKING = Metric("investors_liking", "Grandi investitori a cui piace", "Tavolo dei grandi investitori", "int",
                          "high", "Quanti investitori del tavolo (scheda, funzione Tavolo) la vedono passare TUTTE le "
                                  "loro regole tradotte; solo informazione, non è il loro giudizio")
JUDGMENT_KEYS = {JUDGMENT.key, INVESTORS_LIKING.key}
CUSTOM: dict[str, dict] = {}       # chiave -> {label, formula, fmt, better, description}
METRIC_BY_KEY: dict[str, Metric] = {}
GROUPS: list[str] = []
CUSTOM_FORMATS = {"pct": "percentuale", "x": "multiplo (x)", "num": "numero"}


def _rebuild() -> None:
    custom = [Metric(k, d["label"], CUSTOM_GROUP, d.get("fmt", "num"), d.get("better"), d.get("description") or
                     f"= {d['formula']}") for k, d in CUSTOM.items()]
    METRICS[:] = BASE_METRICS + custom + [JUDGMENT, INVESTORS_LIKING]
    METRIC_BY_KEY.clear()
    METRIC_BY_KEY.update({m.key: m for m in METRICS})
    GROUPS[:] = list(dict.fromkeys(m.group for m in METRICS))


def validate_custom(definition: dict) -> None:
    import re

    from modules.formulas import parse

    key = definition.get("key", "")
    if not re.fullmatch(r"my_[a-z0-9_]{1,40}", key):
        raise ValueError("chiave non valida (es. my_qualita_prezzo)")
    if not definition.get("label", "").strip():
        raise ValueError("Dai un nome alla metrica.")
    parse(definition.get("formula", ""), BASE_KEYS)
    if definition.get("fmt", "num") not in CUSTOM_FORMATS:
        raise ValueError("formato non valido")
    if definition.get("better") not in ("high", "low", None):
        raise ValueError("verso migliore non valido")


def set_custom(definitions: list[dict] | dict[str, dict]) -> None:
    """Sostituisce tutte le metriche personali registrate."""
    items = definitions.values() if isinstance(definitions, dict) else definitions
    CUSTOM.clear()
    add_custom(list(items))


def add_custom(definitions: list[dict]) -> None:
    """Aggiunge o aggiorna alcune metriche personali (es. quelle incluse nella configurazione di una strategia)."""
    for d in definitions:
        validate_custom(d)
        CUSTOM[d["key"]] = {k: d.get(k) for k in ("label", "formula", "fmt", "better", "description")}
    _rebuild()


def custom_key(label: str) -> str:
    """Chiave per una nuova metrica personale, dal nome."""
    import re
    import unicodedata

    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    return "my_" + (re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:40] or "metrica")


_rebuild()


def _div(a, b, positive: bool = True) -> float:
    """a / b, vuoto se b non è significativo (<= 0 con positive=True, altrimenti 0) o se manca qualcosa."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return np.nan
    if not (np.isfinite(a) and np.isfinite(b)) or (b <= 0 if positive else b == 0):
        return np.nan
    return a / b


def _growth(cur, prev) -> float:
    """Crescita solo da una base positiva (da perdita a utile non è una percentuale)."""
    g = _div(cur, prev)
    return g - 1 if np.isfinite(g) else np.nan


def price_metrics(data, tickers: list[str]) -> pd.DataFrame:
    cols = [t for t in tickers if t in data.close.columns]
    # ultimo prezzo degli ultimi giorni: un aggiornamento a mercato aperto dà la giornata parziale solo per alcuni
    # titoli, e gli altri non devono sparire (oltre 5 giorni senza prezzo il titolo resta vuoto)
    c = data.recent_close(cols, TRADING_DAYS + 1).ffill(limit=MAX_PRICE_GAP_DAYS)
    out = pd.DataFrame(index=pd.Index(tickers, name="ticker"))
    if len(c) < 2:
        return out
    last = c.iloc[-1]
    n = len(c)

    def back(k: int) -> pd.Series:
        return c.iloc[-k - 1] if n > k else pd.Series(np.nan, index=c.columns)

    out["price"] = last
    out["ret_1m"] = last / back(21) - 1
    out["ret_1y"] = last / back(TRADING_DAYS) - 1
    out["mom_12_1"] = back(21) / back(TRADING_DAYS) - 1
    logret = np.log(c.where(c > 0)).diff().iloc[1:]
    enough = logret.count() >= 200
    out["volatility"] = (logret.std() * np.sqrt(TRADING_DAYS)).where(enough)
    b = data.benchmark
    if b and b in data.close:
        rb = np.log(data.recent_close([b], TRADING_DAYS + 1)[b]).diff().iloc[1:].reindex(logret.index)
        cov = logret.sub(logret.mean()).mul(rb - rb.mean(), axis=0).sum() / (logret.count() - 1)
        out["beta"] = (cov / rb.var()).where(enough)
    out["drawdown_52w"] = last / c.max() - 1
    out["from_52w_low"] = last / c.min().where(c.min() > 0) - 1
    sma200 = c.iloc[-200:].mean().where(c.iloc[-200:].count() >= 200)
    out["vs_sma200"] = last / sma200 - 1
    return out


PIOTROSKI_MIN_TESTS = 7  # con meno controlli disponibili il punteggio non è affidabile


def piotroski(cur: pd.Series, latest: pd.Series, prev: pd.Series) -> float:
    """F-score di Piotroski (2000) sugli ultimi 12 mesi rispetto a un anno prima. Se mancano uno o due dei nove
    controlli (es. il margine lordo per chi non lo dichiara) il risultato è riportato a 9 e arrotondato;
    con meno di PIOTROSKI_MIN_TESTS controlli resta vuoto."""
    g = lambda row, k: float(row.get(k, np.nan)) if row is not None and len(row) else np.nan  # noqa: E731
    ni, ocf, assets = g(cur, "net_income"), g(cur, "operating_cash_flow"), g(latest, "total_assets")
    p_ni, p_assets = g(prev, "net_income"), g(prev, "total_assets")
    roa, p_roa = _div(ni, assets), _div(p_ni, p_assets)
    lev, p_lev = _div(g(latest, "total_debt"), assets), _div(g(prev, "total_debt"), p_assets)
    cr = _div(g(latest, "current_assets"), g(latest, "current_liabilities"))
    p_cr = _div(g(prev, "current_assets"), g(prev, "current_liabilities"))
    gm, p_gm = _div(g(cur, "gross_profit"), g(cur, "revenue")), _div(g(prev, "gross_profit"), g(prev, "revenue"))
    turn, p_turn = _div(g(cur, "revenue"), assets), _div(g(prev, "revenue"), p_assets)
    shares, p_shares = g(latest, "shares"), g(prev, "shares")
    tests = [
        (roa, lambda: roa > 0), (ocf, lambda: ocf > 0), (roa + p_roa, lambda: roa > p_roa),
        (ocf + ni, lambda: ocf > ni), (lev + p_lev, lambda: lev <= p_lev), (cr + p_cr, lambda: cr > p_cr),
        (shares + p_shares, lambda: shares <= p_shares * 1.001),  # tolleranza: arrotondamenti delle azioni diluite
        (gm + p_gm, lambda: gm > p_gm), (turn + p_turn, lambda: turn > p_turn),
    ]
    done = [bool(test()) for value, test in tests if np.isfinite(value)]
    if len(done) < PIOTROSKI_MIN_TESTS:
        return np.nan
    return float(round(sum(done) * 9 / len(done)))


BENEISH_THRESHOLD = -1.78
BENEISH_MAX_NEUTRAL = 3  # indici senza dati posti a 1 (neutri); con più di così il punteggio resta vuoto


def beneish(cur: pd.Series, latest: pd.Series, prev: pd.Series) -> float:
    """M-score di Beneish (1999, 8 variabili) sugli ultimi 12 mesi rispetto a un anno prima."""
    g = lambda row, k: float(row.get(k, np.nan)) if row is not None and len(row) else np.nan  # noqa: E731
    sales, p_sales = g(cur, "revenue"), g(prev, "revenue")
    if not (np.isfinite(sales) and np.isfinite(p_sales) and sales > 0 and p_sales > 0):
        return np.nan
    ta, p_ta = g(latest, "total_assets"), g(prev, "total_assets")
    gm, p_gm = _div(g(cur, "gross_profit"), sales), _div(g(prev, "gross_profit"), p_sales)
    ca, p_ca = g(latest, "current_assets"), g(prev, "current_assets")
    ppe, p_ppe = g(latest, "ppe_net"), g(prev, "ppe_net")
    dep, p_dep = g(cur, "depreciation"), g(prev, "depreciation")
    sga, p_sga = g(cur, "sga"), g(prev, "sga")
    lev = _div(g(latest, "current_liabilities") + g(latest, "total_debt"), ta)
    p_lev = _div(g(prev, "current_liabilities") + g(prev, "total_debt"), p_ta)
    idx = {
        "dsri": _div(_div(g(latest, "receivables"), sales), _div(g(prev, "receivables"), p_sales)),
        "gmi": _div(p_gm, gm),
        "aqi": _div(1 - _div(ca + ppe, ta), 1 - _div(p_ca + p_ppe, p_ta)),
        "sgi": sales / p_sales,
        "depi": _div(_div(p_dep, p_dep + p_ppe), _div(dep, dep + ppe)),
        "sgai": _div(_div(sga, sales), _div(p_sga, p_sales)),
        "lvgi": _div(lev, p_lev),
    }
    tata = _div(g(cur, "net_income") - g(cur, "operating_cash_flow"), ta, positive=True)
    if not np.isfinite(tata):
        return np.nan
    missing = [k for k, v in idx.items() if not np.isfinite(v)]
    if len(missing) > BENEISH_MAX_NEUTRAL:
        return np.nan
    v = {k: (1.0 if k in missing else x) for k, x in idx.items()}
    return float(-4.84 + 0.920 * v["dsri"] + 0.528 * v["gmi"] + 0.404 * v["aqi"] + 0.892 * v["sgi"]
                 + 0.115 * v["depi"] - 0.172 * v["sgai"] + 4.679 * tata - 0.327 * v["lvgi"])


def altman_z(latest: pd.Series, cur: pd.Series, market_cap: float) -> float:
    """Z-score di Altman (1968): 1,2 capitale circolante + 1,4 utili non distribuiti + 3,3 EBIT + 1,0 ricavi
    (tutti sull'attivo) + 0,6 capitalizzazione / passività totali."""
    g = lambda row, k: float(row.get(k, np.nan))  # noqa: E731
    assets = g(latest, "total_assets")
    liabilities = g(latest, "total_liabilities")
    if not np.isfinite(liabilities):  # molte aziende non dichiarano il totale: attivo - patrimonio netto
        liabilities = assets - g(latest, "equity")
    parts = [
        1.2 * _div(g(latest, "current_assets") - g(latest, "current_liabilities"), assets),
        1.4 * _div(g(latest, "retained_earnings"), assets),
        3.3 * _div(g(cur, "operating_income"), assets),
        0.6 * _div(market_cap, liabilities),
        1.0 * _div(g(cur, "revenue"), assets),
    ]
    return float(sum(parts)) if all(np.isfinite(parts)) else np.nan


def fundamental_metrics(data, ticker: str, price: float, max_age_days: int = 550, lbo_cfg: dict | None = None,
                        debt_rate: float = np.nan) -> dict:
    """Metriche di bilancio di un titolo (ultimi 12 mesi) al prezzo dato. `lbo_cfg`: configurazione (blocco `lbo`);
    `debt_rate`: costo del debito del test LBO alla data dei dati."""
    st = data.fundamentals(ticker)
    as_of = data.last_date
    if st.empty or as_of is None or (as_of - st.index[-1]).days > max_age_days:
        return {}
    cur = st.iloc[-1]
    prev = data.year_ago(st)
    latest = st.ffill().iloc[-1]  # patrimoniale: ultimo valore noto anche se il trimestre non lo riporta
    g = lambda row, k: row.get(k, np.nan) if row is not None else np.nan  # noqa: E731
    shares = data.last_shares(st)
    mcap = price * shares if np.isfinite(price) and np.isfinite(shares) and shares > 0 else np.nan
    debt, cash, equity, assets = (g(latest, k) for k in ("total_debt", "cash", "equity", "total_assets"))
    debt_n, cash_n = (0.0 if not np.isfinite(debt) else debt), (0.0 if not np.isfinite(cash) else cash)
    ev = mcap + debt_n - cash_n if np.isfinite(mcap) else np.nan
    ni, rev, fcf, ocf = (g(cur, k) for k in ("net_income", "revenue", "free_cash_flow", "operating_cash_flow"))
    ebit, da, gp = g(cur, "operating_income"), g(cur, "depreciation"), g(cur, "gross_profit")
    ebitda = ebit + da if np.isfinite(ebit) and np.isfinite(da) else np.nan
    div, buy = g(cur, "dividends_paid"), g(cur, "buybacks")
    tax_rate = _div(g(cur, "tax_provision"), g(cur, "pretax_income"))
    tax_rate = float(np.clip(tax_rate, 0, 0.35)) if np.isfinite(tax_rate) else DEFAULT_TAX_RATE
    invested = (equity + debt_n - cash_n) if np.isfinite(equity) else np.nan

    annual = data.statements(ticker)
    cagr = np.nan
    if "revenue" in annual:
        rev_a = annual["revenue"].dropna()
        if len(rev_a) >= 4 and rev_a.iloc[-4] > 0 and rev_a.iloc[-1] > 0:
            years = (rev_a.index[-1] - rev_a.index[-4]).days / 365.25
            cagr = (rev_a.iloc[-1] / rev_a.iloc[-4]) ** (1 / years) - 1 if years > 2.5 else np.nan
    hist = history_metrics(annual, data.prices(ticker))
    pe_median = hist.pop("_pe_median")
    pe = _div(mcap, ni)
    eps_g = hist["eps_cagr_5y"]
    dy = _div(div, mcap)
    q_growth, q_accel = quarterly_growth(data.quarterly_income(ticker), as_of)
    financial = lbo_mod.is_financial(cur, latest)
    buyout = lbo_mod.lbo(ebitda=ebitda, fcf=fcf, interest_expense=g(cur, "interest_expense"), debt=debt, cash=cash,
                         market_cap=mcap, shares=shares, tax_rate=tax_rate, rate=debt_rate, financial=financial,
                         cfg=lbo_cfg)

    return {**hist,
        "ps": _div(mcap, rev), "pe_x_pb": pe * _div(mcap, equity) if np.isfinite(pe) else np.nan,
        "peg": _div(pe, eps_g * 100) if np.isfinite(pe) and np.isfinite(eps_g) else np.nan,
        "neff_ratio": _div((eps_g + (dy if np.isfinite(dy) else 0.0)) * 100, pe) if np.isfinite(eps_g) else np.nan,
        "fcf_ev_yield": _div(fcf, ev), "payout_ratio": _div(div, ni),
        "lbo_irr": buyout.irr if buyout.ok else np.nan,
        # per la distanza dal default (serve la volatilità, calcolata con i prezzi in compute_metrics)
        "_dd_debt": debt, "_dd_leases": g(latest, "operating_leases"), "_financial": financial,
        "pe_vs_history": _div(pe, pe_median),
        "rnd_to_revenue": _div(g(cur, "rnd"), rev),
        **sector_metrics(cur, latest, mcap, ni, rev, equity, assets),
        "earnings_q_growth": q_growth, "earnings_q_accel": q_accel,
        "market_cap": mcap, "enterprise_value": ev,
        "pe": pe, "earnings_yield": _div(ni, mcap), "p_fcf": _div(mcap, fcf),
        "fcf_yield": _div(fcf, mcap), "ev_ebit": _div(ev, ebit), "ev_ebitda": _div(ev, ebitda),
        "ev_sales": _div(ev, rev), "pb": _div(mcap, equity),
        "dividend_yield": _div(div, mcap),
        "shareholder_yield": _div(np.nansum([div, buy]), mcap) if np.isfinite(div) or np.isfinite(buy) else np.nan,
        "gross_margin": _div(gp, rev), "operating_margin": _div(ebit, rev), "net_margin": _div(ni, rev),
        "fcf_margin": _div(fcf, rev), "roe": _div(ni, equity), "roa": _div(ni, assets),
        "roic": _div(ebit * (1 - tax_rate), invested) if np.isfinite(ebit) else np.nan,
        "gross_profitability": _div(gp, assets),
        "revenue_growth": _growth(rev, g(prev, "revenue")), "earnings_growth": _growth(ni, g(prev, "net_income")),
        "fcf_growth": _growth(fcf, g(prev, "free_cash_flow")), "revenue_cagr_3y": cagr,
        "share_change": _growth(shares, g(prev, "shares")),
        "debt_to_equity": _div(debt, equity), "net_debt_ebitda": _div(debt_n - cash_n, ebitda),
        "current_ratio": _div(g(latest, "current_assets"), g(latest, "current_liabilities")),
        "interest_coverage": _div(ebit, abs(g(cur, "interest_expense"))),
        "accruals": _div(ni - ocf, assets) if np.isfinite(ni) and np.isfinite(ocf) else np.nan,
        "cash_conversion": _div(ocf, ni),
        "sbc_to_revenue": _div(g(cur, "stock_compensation"), rev),
        "fcf_after_sbc_yield": _div(fcf - g(cur, "stock_compensation"), mcap),
        "beneish_m": beneish(cur, latest, prev),
        "piotroski": piotroski(cur, latest, prev),
        "altman_z": altman_z(latest, cur, mcap),
        "statement_date": st.index[-1],
    }


REIT_MIN_REAL_ESTATE = 0.30  # quota dell'attivo in immobili oltre la quale si calcola l'FFO


def sector_metrics(cur: pd.Series, latest: pd.Series, mcap: float, ni: float, rev: float, equity: float,
                   assets: float) -> dict:
    """Metriche di banche, assicurazioni e REIT (vuote per chi non ha le voci di quel settore)."""
    g = lambda row, k: float(row.get(k, np.nan)) if row is not None else np.nan  # noqa: E731
    tangible = equity - np.nan_to_num(g(latest, "goodwill")) - np.nan_to_num(g(latest, "intangibles")) \
        if np.isfinite(equity) else np.nan
    nii = g(cur, "net_interest_income")
    bank = np.isfinite(nii) and np.isfinite(g(cur, "noninterest_expense"))
    out = {"ptbv": _div(mcap, tangible), "rotce": _div(ni, tangible),
           "nim": _div(nii, assets) if bank else np.nan,
           "efficiency_ratio": _div(g(cur, "noninterest_expense"), rev) if bank else np.nan,
           "provision_ratio": _div(g(cur, "loan_provisions"), rev) if bank else np.nan,
           "loss_ratio": _div(g(cur, "claims"), g(cur, "premiums")), "ffo_yield": np.nan, "p_ffo": np.nan}
    re_assets = g(latest, "real_estate_assets")
    if np.isfinite(re_assets) and np.isfinite(assets) and assets > 0 and re_assets / assets >= REIT_MIN_REAL_ESTATE:
        dep = g(cur, "depreciation")
        if np.isfinite(ni) and np.isfinite(dep):
            ffo = ni + dep - np.nan_to_num(g(cur, "real_estate_gains"))
            out.update(ffo_yield=_div(ffo, mcap), p_ffo=_div(mcap, ffo))
    return out


HISTORY_YEARS = 5      # finestra delle metriche "Storia"
MIN_HISTORY_YEARS = 3  # con meno esercizi restano vuote
STREAK_MAX = 10


def _cagr(values: np.ndarray, years_back: np.ndarray) -> float:
    """Crescita annua dall'esercizio di ~5 anni prima (o il più vecchio ad almeno 3 anni) all'ultimo.
    `years_back`: anni di ogni esercizio prima dell'ultimo (crescenti verso il passato)."""
    ok = np.isfinite(values)
    if not ok[-1]:
        return np.nan
    window = ok & (years_back >= MIN_HISTORY_YEARS - 0.25) & (years_back <= HISTORY_YEARS + 0.5)
    if not window.any():
        return np.nan
    i = int(np.flatnonzero(window)[0])  # il più lontano dentro la finestra
    first, last = values[i], values[-1]
    if first <= 0 or last <= 0:
        return np.nan
    return float((last / first) ** (1 / years_back[i]) - 1)


def _streak(values: np.ndarray) -> float:
    """Quanti valori positivi di fila alla fine."""
    bad = np.flatnonzero(~(np.nan_to_num(values, nan=-1.0) > 0))
    return float(len(values) - 1 - bad[-1]) if len(bad) else float(len(values))


def history_metrics(annual: pd.DataFrame, prices: pd.Series) -> dict:
    """Metriche su più esercizi completi (ultima versione depositata di ciascuno, point-in-time).
    Con numpy: si calcolano per ogni azienda a ogni ribilanciamento dei backtest."""
    out = dict.fromkeys(["eps_cagr_5y", "revenue_cagr_5y", "roe_min_5y", "roic_avg_5y", "margin_trend",
                         "profitable_years", "dividend_years", "_pe_median"], np.nan)
    if annual.empty:
        return out
    a = annual.tail(STREAK_MAX)
    n = len(a)
    col = lambda k: a[k].to_numpy(dtype=float) if k in a else np.full(n, np.nan)  # noqa: E731
    pos = lambda x: np.where(x > 0, x, np.nan)  # noqa: E731
    ni, eq, rev, shares = col("net_income"), col("equity"), col("revenue"), col("shares")
    dates = a.index.to_numpy()
    years_back = (dates[-1] - dates).astype("timedelta64[D]").astype(float) / 365.25
    recent = years_back < HISTORY_YEARS - 30 / 365.25

    with np.errstate(invalid="ignore", divide="ignore"):
        if np.isfinite(ni).any():
            out["profitable_years"] = _streak(ni)
            out["dividend_years"] = _streak(np.nan_to_num(col("dividends_paid"), nan=0.0))
        out["eps_cagr_5y"] = _cagr(ni / pos(shares), years_back)
        out["revenue_cagr_5y"] = _cagr(rev, years_back)
        roe = (ni / pos(eq))[recent]
        roe = roe[np.isfinite(roe)]
        if len(roe) >= MIN_HISTORY_YEARS:
            out["roe_min_5y"] = float(roe.min())
        tax = np.clip(col("tax_provision") / pos(col("pretax_income")), 0, 0.35)
        tax = np.where(np.isfinite(tax), tax, DEFAULT_TAX_RATE)
        invested = eq + np.nan_to_num(col("total_debt")) - np.nan_to_num(col("cash"))
        roic = (col("operating_income") * (1 - tax) / pos(invested))[recent]
        roic = roic[np.isfinite(roic)]
        if len(roic) >= MIN_HISTORY_YEARS:
            out["roic_avg_5y"] = float(roic.mean())
        margin = col("operating_income") / pos(rev)
        margin = margin[np.isfinite(margin)]
        if len(margin) >= 3:
            out["margin_trend"] = float(margin[-1] - margin[-4:-1].mean())
        if not prices.empty:  # P/E alle chiusure d'esercizio passate (prezzi rettificati, azioni sulla stessa base)
            i = np.searchsorted(prices.index.to_numpy(), dates, side="right") - 1
            px = np.where(i >= 0, prices.to_numpy()[np.maximum(i, 0)], np.nan)
            pe_hist = (px * shares / pos(ni))[recent]
            pe_hist = pe_hist[np.isfinite(pe_hist)]
            if len(pe_hist) >= MIN_HISTORY_YEARS:
                out["_pe_median"] = float(np.median(pe_hist))
    return out


QUARTER_MAX_AGE_DAYS = 200  # un trimestre chiuso da più di così: è saltato almeno un 10-Q, il dato non è "l'ultimo"


def quarterly_growth(q: pd.Series, as_of, max_age_days: int = QUARTER_MAX_AGE_DAYS) -> tuple[float, float]:
    """(crescita dell'utile dell'ultimo trimestre sullo stesso trimestre dell'anno prima, accelerazione = quella
    crescita meno la crescita del trimestre precedente). `q`: utile netto per singolo trimestre."""
    q = q.dropna()
    if len(q) < 5 or as_of is None or (as_of - q.index[-1]).days > max_age_days:
        return np.nan, np.nan

    def yoy(i: int) -> float:
        end = q.index[i]
        prev = q[(q.index <= end - pd.Timedelta(days=355)) & (q.index >= end - pd.Timedelta(days=375))]
        return _growth(q.iloc[i], prev.iloc[-1]) if not prev.empty else np.nan

    now, before = yoy(-1), yoy(-2)
    return now, (now - before if np.isfinite(now) and np.isfinite(before) else np.nan)


def value_composite(metrics: pd.DataFrame) -> pd.Series:
    """Media delle posizioni percentili (1 = più economica) su sei misure di prezzo; almeno 3 disponibili."""
    cols = {"pe": True, "pb": True, "ps": True, "ev_ebitda": True, "p_fcf": True, "shareholder_yield": False}
    ranks = pd.DataFrame({c: (-metrics[c] if low else metrics[c]).rank(pct=True)
                          for c, low in cols.items() if c in metrics})
    return ranks.mean(axis=1).where(ranks.notna().sum(axis=1) >= 3)


def ownership_metrics(data, tickers: list[str], window_days: int = 90) -> pd.DataFrame:
    """Metriche su insider e grandi investitori all'ultima data di `data` (solo depositi già pubblici)."""
    out = pd.DataFrame(index=pd.Index(tickers, name="ticker"))
    as_of = data.last_date
    tx = getattr(data, "insiders", pd.DataFrame())
    # dati scaricati (anche se a questa data nessuna operazione è già pubblica: allora zero, non vuoto)
    if as_of is not None and tx is not None and "filed" in tx.columns:
        from modules.insiders import summary

        short = summary(tx, as_of, window_days).reindex(out.index)
        year = summary(tx, as_of, 365).reindex(out.index)
        out["insider_buy_value"] = short["buy_value"].fillna(0.0)
        out["insider_buyers"] = short["buyers"].fillna(0)
        out["_insider_net_12m"] = (year["buy_value"].fillna(0) - year["sell_value"].fillna(0))
    hold = getattr(data, "holdings", pd.DataFrame())
    if hold is not None and "filed" in hold.columns:
        from modules.superinvestors import fund_positions

        pos = fund_positions(hold)
        out["funds_holding"] = out["funds_adding"] = 0.0
        if not pos.empty:
            live = pos[pos["change"] != "uscita"]
            out["funds_holding"] = live.groupby("ticker")["fund_cik"].nunique().reindex(out.index).fillna(0)
            adding = pos[pos["change"].isin(["nuova", "aumentata"])]
            out["funds_adding"] = adding.groupby("ticker")["fund_cik"].nunique().reindex(out.index).fillna(0)
    return out


def compute_metrics(data, tickers: list[str], max_age_days: int = 550, insider_days: int = 90,
                    cfg: dict | None = None) -> pd.DataFrame:
    """Tutte le metriche dei titoli all'ultima data di `data`. Righe = ticker; colonne = chiavi di METRICS,
    più `price` e `statement_date` (chiusura dell'ultimo periodo di bilancio usato). `cfg`: configurazione (solo
    il blocco `lbo`, facoltativo: senza, i parametri di modules/lbo.py)."""
    tickers = list(tickers)
    pm = price_metrics(data, tickers)
    prices = pm["price"] if "price" in pm else pd.Series(np.nan, index=pm.index)
    lbo_p = lbo_mod.settings(cfg)
    rate = lbo_mod.debt_rate(data, lbo_p)
    rows = {t: fundamental_metrics(data, t, float(prices.get(t, np.nan)), max_age_days, cfg, rate) for t in tickers}
    fm = pd.DataFrame.from_dict(rows, orient="index").reindex(pm.index)
    out = pm.join(fm.drop(columns=[c for c in fm.columns if c in pm.columns]), how="left")
    if "_dd_debt" in out:
        out["distance_to_default"] = [
            lbo_mod.distance_to_default(*(r.get(k) for k in ("market_cap", "_dd_debt", "_dd_leases", "volatility",
                                                              "ret_1y")),
                                        financial=bool(r.get("_financial") == True), cap=lbo_p["dd_cap"])  # noqa: E712
            for _, r in out.iterrows()]
    own = ownership_metrics(data, tickers, insider_days)
    if "_insider_net_12m" in own:
        own["insider_net_pct"] = own.pop("_insider_net_12m") / out["market_cap"].where(out["market_cap"] > 0)
    out = out.join(own, how="left")
    out["value_composite"] = value_composite(out)
    cols = ["price"] + [m.key for m in METRICS] + ["statement_date"]
    numeric = [c for c in cols if c != "statement_date"]
    out = out.reindex(columns=cols)
    out[numeric] = out[numeric].astype(float).replace([np.inf, -np.inf], np.nan)
    if CUSTOM:  # le metriche personali sono formule sulle altre, alla stessa data: point-in-time anche loro
        from modules.formulas import evaluate

        base = out[[c for c in out.columns if c in BASE_KEYS]]
        for key, d in CUSTOM.items():
            out[key] = evaluate(d["formula"], base, BASE_KEYS)
    return out


def percentile_ranks(metrics: pd.DataFrame, sectors: dict[str, str] | None = None) -> pd.DataFrame:
    """Posizione di ogni titolo da 0 (peggiore) a 1 (migliore) per ogni metrica con una direzione.
    Con `sectors` il confronto è solo con le aziende dello stesso settore."""
    keys = [m.key for m in METRICS if m.better and m.key in metrics]
    vals = metrics[keys].astype(float)
    flip = [k for k in keys if METRIC_BY_KEY[k].better == "low"]
    vals[flip] = -vals[flip]
    if sectors is None:
        return vals.rank(pct=True)
    groups = pd.Series({t: sectors.get(t) for t in vals.index})
    return vals.groupby(groups).rank(pct=True)


def format_value(key: str, value) -> str:
    """Valore leggibile secondo il formato della metrica."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "–"
    fmt = METRIC_BY_KEY[key].fmt if key in METRIC_BY_KEY else "num"
    if fmt == "pct":
        return f"{value:.1%}"
    if fmt == "x":
        return f"{value:.1f}x"
    if fmt == "money":
        for div, unit in ((1e12, " T$"), (1e9, " mld $"), (1e6, " mln $")):
            if abs(value) >= div:
                return f"{value / div:,.1f}{unit}"
        return f"{value:,.0f} $"
    if fmt == "int":
        return f"{int(value)}"
    return f"{value:.2f}"
