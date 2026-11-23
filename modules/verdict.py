"""
Il giudizio su un'azienda: le metriche del catalogo (modules/metrics.py) riassunte in cinque pilastri e in una
sola frase ("Candidata all'acquisto", "Buona azienda, prezzo alto: aspettare"...), con le prove in chiaro.

Non è una raccomandazione: è la sintesi di criteri fissati qui sotto (DEFAULTS, sovrascrivibili con la sezione
`verdict:` di config.yaml) applicati ai dati. Nessuna promessa di rendimento.

Come funziona
- Ogni PROVA è una metrica (o un confronto tra metriche) con due soglie fissate a priori: `good` (da qui la prova
  è pienamente favorevole) e `bad` (da qui è pienamente contraria); in mezzo il punteggio va in linea retta da 0 a
  1. Esito: favorevole sopra `favorable`, contrario sotto `contrary`, altrimenti neutro.
- Un PILASTRO è la media pesata delle sue prove disponibili. La copertura è la quota (pesata) delle prove
  disponibili. REGOLA FERMA: un dato mancante non conta mai come positivo. Il punteggio viene tirato verso il
  neutro (0,5) in proporzione ai dati mancanti solo se è sopra il neutro; se è sotto resta dov'è:
      punteggio = copertura x media + (1 - copertura) x min(media, 0,5)
  Sotto `min_pillar_coverage` il pilastro non ha punteggio ("Dati insufficienti").
- Pilastri: Qualità del business, Solidità finanziaria, Prezzo, Segnali d'allarme (punteggio alto = pochi allarmi),
  Chi è dentro (insider e grandi investitori: solo contesto, non cambia il giudizio).
- Il GIUDIZIO viene da regole in ordine (`_decide`), scritte in chiaro nel risultato.
- Settori: banche, assicurazioni e REIT hanno prove proprie (P/TBV, ROTCE, efficienza, sinistri/premi, P/FFO...):
  ROIC, debito/EBITDA, Altman e DCF lì non hanno senso. Le altre finanziarie (pagamenti, borse, gestori) usano le
  prove generali senza Altman e senza DCF (come `valuation.skip_sectors`).

Le soglie vengono da principi classici dell'analisi fondamentale (citati nei commenti), NON da backtest: sono
punti di riferimento ragionevoli, da cambiare in config.yaml se il tuo metodo è diverso.

Funzione pura: `judge(VerdictInput, cfg)`. Comodità sui dati dell'app: `assess_ticker(d, ticker)` e
`assess_many(d, tickers)` (tabella per Esplora e "Le mie aziende").
"""
from __future__ import annotations

import copy
import functools
import logging
import math
import re
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from modules.metrics import METRIC_BY_KEY, format_value

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------------------------ categorie
CANDIDATE = "candidata"
WAIT = "aspettare"
VALUE_TRAP = "qualita_dubbia"
AVOID = "evitare"
NEUTRAL = "neutra"
NO_DATA = "dati_insufficienti"
LABELS = {
    CANDIDATE: "Candidata all'acquisto",
    WAIT: "Buona azienda, prezzo alto: aspettare",
    VALUE_TRAP: "Prezzo basso ma qualità dubbia",
    NEUTRAL: "Nessun segnale netto: da approfondire",
    AVOID: "Da evitare",
    NO_DATA: "Dati insufficienti per giudicare",
}
ORDER = [CANDIDATE, WAIT, NEUTRAL, VALUE_TRAP, AVOID, NO_DATA]  # per ordinare le tabelle

QUALITY, SOLIDITY, PRICE, FLAGS, INSIDERS = "quality", "solidity", "price", "flags", "insiders"
PILLAR_LABELS = {QUALITY: "Qualità del business", SOLIDITY: "Solidità finanziaria", PRICE: "Prezzo",
                 FLAGS: "Segnali d'allarme", INSIDERS: "Chi è dentro (insider e fondi)"}
# voto in parole: (soglia minima del punteggio, parola), dal migliore
GRADES = {
    QUALITY: [(0.75, "Eccellente"), (0.60, "Buona"), (0.40, "Nella media"), (0.25, "Debole"), (0.0, "Scarsa")],
    SOLIDITY: [(0.75, "Molto solida"), (0.50, "Solida"), (0.35, "Da seguire"), (0.20, "Fragile"), (0.0, "A rischio")],
    PRICE: [(0.75, "Prezzo basso"), (0.60, "Prezzo interessante"), (0.40, "Prezzo pieno"), (0.25, "Prezzo alto"),
            (0.0, "Prezzo molto alto")],
    FLAGS: [(0.75, "Nessun allarme"), (0.50, "Qualche dubbio"), (0.30, "Allarmi da verificare"),
            (0.0, "Allarmi seri")],
    INSIDERS: [(0.70, "Segnali a favore"), (0.45, "Nessun segnale"), (0.0, "Segnali contro")],
}
PROFILE_LABELS = {"general": "Azienda industriale o di servizi", "bank": "Banca", "insurer": "Assicurazione",
                  "reit": "Società immobiliare (REIT)", "financial": "Finanziaria non bancaria"}

DISCLAIMER = ("Sintesi automatica dei criteri impostati applicati ai dati disponibili: non è una raccomandazione "
              "personalizzata né una previsione di rendimento.")

# ------------------------------------------------------------------------------------------------ parametri
# Soglie (good, bad): good = pienamente favorevole, bad = pienamente contraria. Se good < bad, più basso è meglio.
DEFAULTS: dict = {
    "min_coverage": 0.60,          # sotto il 60% delle prove (pesate) di Qualità+Solidità+Prezzo+Allarmi: non si giudica
    "min_pillar_coverage": 0.40,   # e ognuno di Qualità, Solidità, Prezzo deve avere almeno il 40% delle sue prove
    "min_sector_peers": 5,         # confronti con il settore solo con almeno 5 aziende simili
    "favorable": 0.67,             # esito della singola prova: favorevole da qui
    "contrary": 0.33,              # contraria fino a qui
    "quality_good": 0.60, "quality_bad": 0.40,
    "solidity_good": 0.50, "solidity_bad": 0.35,
    "price_good": 0.60,
    # ruolo del pilastro Prezzo per "Candidata": "brake" (freno, dal 2026-11-23) = basta che il prezzo non sia alto
    # (punteggio >= price_brake, confine già fissato tra "Prezzo pieno" e "Prezzo alto"); "gate" (regola di prima) =
    # serve un prezzo interessante (>= price_good). Motivo: nella prova storica 2013-2026 le Candidate scelte anche per
    # il prezzo basso non hanno battuto l'indice e il pilastro Prezzo aveva IC negativo. Il freno non è tarato su quei
    # dati: la soglia è un confine che esisteva già; si misura in avanti con lo storico dei giudizi.
    "price_mode": "brake",
    "price_brake": 0.40,
    "flags_ok": 0.50,              # sotto: niente "Candidata" finché gli allarmi non sono verificati
    "user_fair_value_share": 0.60,  # quanto pesa nel Prezzo il TUO fair value, quando c'è
    "max_price_age_days": 7,       # prezzo più vecchio di così rispetto ai dati: non si giudica
    "overall_weights": {QUALITY: 0.35, SOLIDITY: 0.20, PRICE: 0.35, FLAGS: 0.10},  # solo per ordinare le tabelle
    "implied_growth_grid": [-0.10, 0.60, 141],  # crescite provate dal DCF inverso (da, a, punti)
    "thresholds": {
        # --- qualità: rendere più del costo del capitale, con costanza (Buffett, Munger, Greenblatt)
        "roic": (0.15, 0.06),             # 15% = soglia classica di un vantaggio competitivo; 6% ~ sotto il costo del capitale
        "roic_avg_5y": (0.15, 0.06),      # la stessa, in media su 5 esercizi: costanza, non un anno buono
        "roe_min_5y": (0.12, 0.04),       # nemmeno l'anno peggiore sotto il 12% (Buffett); sotto il 4% non copre il rischio
        "margin_sector": (0.70, 0.30),    # margine operativo nel 30% migliore / peggiore del settore
        "cash_conversion": (1.00, 0.70),  # utili che diventano cassa (Sloan 1996: gli accantonamenti alti anticipano delusioni)
        "revenue_cagr": (0.06, 0.00),     # ricavi che crescono più dell'economia nominale / ricavi in calo
        "eps_cagr_5y": (0.08, 0.00),      # utile per azione in crescita (Fisher, Lynch)
        "margin_trend": (0.01, -0.03),    # margine operativo in miglioramento / in calo di 3 punti
        "profitable_years": (8, 3),       # utile in quasi tutti gli ultimi 10 esercizi (Graham: nessuna perdita in 10 anni)
        # --- solidità (Graham, Altman 1968, Piotroski 2000, rating delle agenzie)
        "net_debt_ebitda": (1.0, 3.5),    # sotto 1 anno di margine il debito non è un tema; sopra 3,5 ~ fuori dall'investment grade
        "interest_coverage": (8.0, 3.0),  # copertura da rating A in su / da rating speculativo (tabelle di Damodaran)
        "altman_z": (3.0, 1.8),           # le zone di Altman: sicura sopra 3, pericolo sotto 1,8
        "piotroski": (7, 3),              # 7-9 bilancio in miglioramento, 0-3 in peggioramento
        "current_ratio": (1.5, 0.8),      # Graham chiedeva 2; peso basso: molte buone aziende vivono sotto 1
        # --- prezzo: margine di sicurezza (Graham), aspettative implicite (Mauboussin, Damodaran)
        "fv_margin": (0.25, 0.00),        # prezzo almeno il 25% sotto il TUO fair value / sopra il fair value
        "implied_growth_gap": (-0.02, 0.05),  # il prezzo sconta 2 punti in meno della crescita storica / 5 punti in più
        "fcf_spread": (0.02, -0.03),      # rendimento del FCF meno (costo del capitale - crescita di lungo periodo)
        "pe_vs_history": (0.85, 1.25),    # P/E il 15% sotto la sua mediana di 5 anni / il 25% sopra
        "multiples_sector": (0.65, 0.35),  # più economica del 65% del settore sui multipli / più cara del 65%
        # --- banche e assicurazioni
        "rotce": (0.15, 0.08),            # rendimento sul patrimonio tangibile: 15% ottimo, sotto l'8% sotto il costo
        "efficiency_ratio": (0.55, 0.70),  # costi/ricavi: sotto il 55% banca efficiente, sopra il 70% pesante
        "provision_ratio": (0.05, 0.15),  # accantonamenti su crediti / ricavi
        "loss_ratio": (0.60, 0.75),       # sinistri/premi: senza spese, sopra il 75% la sottoscrizione rende poco
        "tce_assets": (0.07, 0.04),       # patrimonio tangibile / attivo: 4% = il minimo guardato negli stress test 2009, 7% comodo
        "equity_assets": (0.12, 0.05),    # patrimonio / attivo (assicurazioni)
        "ptbv_justified": (0.80, 1.30),   # P/TBV / P/TBV giustificato = (ROTCE - g) / (costo dell'equity - g)
        # --- REIT
        "p_ffo": (13.0, 22.0),            # prezzo / FFO: media storica dei REIT USA ~15-17
        "reit_net_debt_ebitda": (5.5, 8.0),  # i REIT vivono a leva più alta: 5-6x è la norma per le agenzie
        "reit_interest_coverage": (4.0, 2.0),
        "reit_debt_to_equity": (1.0, 2.0),  # debito fino al patrimonio contabile (prudente: gli immobili sono ammortizzati)
        "dividend_years": (8, 3),
        # --- segnali d'allarme (punteggio alto = nessun allarme)
        "beneish_m": (-2.22, -1.78),      # Beneish: -1,78 soglia del modello a 8 variabili, -2,22 quella prudente
        "share_change": (0.00, 0.03),     # azioni in calo / diluizione oltre il 3% l'anno
        "sbc_to_revenue": (0.02, 0.08),   # compensi in azioni: costo vero che il FCF non sottrae
        "fcf_margin": (0.05, 0.00),       # free cash flow positivo
        "statement_age_days": (200, 450),  # ultimo bilancio entro un trimestre e mezzo / oltre un anno e un trimestre
        "fcf_lag_days": (120, 400),       # FCF più vecchio dell'ultimo bilancio (stessa soglia di valuation.max_fcf_lag_days)
        "data_warnings": (0, 1),          # avvisi del controllo di qualità che toccano questi numeri
        # --- chi è dentro: l'assenza di acquisti non è un segnale contrario (prove "a un lato")
        "insider_buyers": (2, 0),         # più insider che comprano insieme: il segnale più studiato (Lakonishok-Lee 2001)
        "insider_net_pct": (0.0005, -0.005),  # acquisti netti / vendite non programmate oltre lo 0,5% della capitalizzazione
        "funds_holding": (3, 0),
        "funds_adding": (2, 0),
    },
    "weights": {
        "roic": 2.0, "roic_avg_5y": 2.0, "roe_min_5y": 1.0, "margin_sector": 1.0, "cash_conversion": 1.0,
        "revenue_cagr": 1.0, "eps_cagr_5y": 1.0, "margin_trend": 0.5, "profitable_years": 1.0,
        "net_debt_ebitda": 2.0, "interest_coverage": 1.5, "altman_z": 1.5, "piotroski": 1.0, "current_ratio": 0.5,
        "implied_growth_gap": 2.0, "fcf_spread": 1.5, "pe_vs_history": 1.0, "multiples_sector": 1.5,
        "rotce": 2.0, "efficiency_ratio": 1.5, "provision_ratio": 1.0, "loss_ratio": 1.5, "tce_assets": 2.0,
        "equity_assets": 1.0, "ptbv_justified": 2.0, "p_ffo": 2.0, "reit_net_debt_ebitda": 2.0,
        "reit_interest_coverage": 1.5, "reit_debt_to_equity": 1.0, "dividend_years": 1.0,
        "beneish_m": 1.0, "share_change": 1.0, "sbc_to_revenue": 1.0, "fcf_margin": 1.0, "statement_age_days": 1.0,
        "fcf_lag_days": 0.5, "data_warnings": 1.0,
        "insider_buyers": 1.0, "insider_net_pct": 1.0, "funds_holding": 1.0, "funds_adding": 1.0,
    },
    # allarmi: i gravi da soli portano a "Da evitare"; quelli "da verificare" impediscono solo "Candidata"
    "alarms": {
        "distress_altman": 1.8, "distress_coverage": 1.5,     # zona di pericolo di Altman E interessi coperti a fatica
        "debt_trap_coverage": 1.0, "debt_trap_leverage": 4.0,  # l'utile operativo non paga gli interessi, debito alto
        "beneish": -1.78, "beneish_accruals": 0.05,           # Beneish oltre la soglia E utili poco coperti da cassa
    },
    # prove di ogni pilastro per tipo di azienda
    "profiles": {
        "general": {
            QUALITY: ["roic", "roic_avg_5y", "roe_min_5y", "margin_sector", "cash_conversion", "revenue_cagr",
                      "eps_cagr_5y", "margin_trend", "profitable_years"],
            SOLIDITY: ["net_debt_ebitda", "interest_coverage", "altman_z", "piotroski", "current_ratio"],
            PRICE: ["implied_growth_gap", "fcf_spread", "pe_vs_history", "multiples_sector"],
            FLAGS: ["beneish_m", "share_change", "sbc_to_revenue", "fcf_margin", "statement_age_days",
                    "fcf_lag_days", "data_warnings"],
        },
        "financial": {
            QUALITY: ["roic", "roic_avg_5y", "roe_min_5y", "margin_sector", "cash_conversion", "revenue_cagr",
                      "eps_cagr_5y", "margin_trend", "profitable_years"],
            SOLIDITY: ["net_debt_ebitda", "interest_coverage", "piotroski", "current_ratio"],
            PRICE: ["fcf_spread", "pe_vs_history", "multiples_sector"],
            FLAGS: ["beneish_m", "share_change", "sbc_to_revenue", "statement_age_days", "data_warnings"],
        },
        "bank": {
            QUALITY: ["rotce", "roe_min_5y", "efficiency_ratio", "provision_ratio", "eps_cagr_5y", "profitable_years"],
            SOLIDITY: ["tce_assets"],
            PRICE: ["ptbv_justified", "pe_vs_history", "multiples_sector"],
            FLAGS: ["share_change", "statement_age_days", "data_warnings"],
        },
        "insurer": {
            QUALITY: ["rotce", "roe_min_5y", "loss_ratio", "eps_cagr_5y", "profitable_years"],
            SOLIDITY: ["equity_assets", "interest_coverage"],
            PRICE: ["ptbv_justified", "pe_vs_history", "multiples_sector"],
            FLAGS: ["share_change", "statement_age_days", "data_warnings"],
        },
        "reit": {
            QUALITY: ["revenue_cagr", "margin_sector", "cash_conversion", "profitable_years", "dividend_years",
                      "margin_trend"],
            SOLIDITY: ["reit_net_debt_ebitda", "reit_interest_coverage", "reit_debt_to_equity", "piotroski"],
            PRICE: ["p_ffo", "multiples_sector"],
            FLAGS: ["sbc_to_revenue", "statement_age_days", "data_warnings"],
        },
    },
    # multipli confrontati con il settore, per tipo di azienda
    "sector_multiples": {
        "general": ["pe", "ev_ebit", "p_fcf", "ev_sales"], "financial": ["pe", "ev_ebit", "p_fcf", "ev_sales"],
        "bank": ["pe", "ptbv"], "insurer": ["pe", "ptbv"], "reit": ["p_ffo", "pb"],
    },
    "insider_evidence": ["insider_buyers", "insider_net_pct", "funds_holding", "funds_adding"],
}
ONE_SIDED = {"insider_buyers", "funds_holding", "funds_adding"}  # da 0,5 (nessun segnale) a 1, mai contrarie
CENTERED = {"insider_net_pct"}  # zero = neutro (0,5); sopra verso `good`, sotto verso `bad`
# avvisi del controllo di qualità che rendono meno affidabili i numeri del giudizio
WARNING_CHECKS = {"debito forse incompleto", "liquidità incoerente", "azioni vecchie", "azioni discontinue"}
BANK_SUBINDUSTRIES = ("Bank", "Brokerage", "Consumer Finance")
SECTOR_PEER_METRICS = ["operating_margin", "pe", "ev_ebit", "p_fcf", "ev_sales", "ptbv", "p_ffo", "pb"]
# metriche che servono al giudizio (per la funzione di comodo)
INPUT_METRICS = sorted({
    "price", "market_cap", "enterprise_value", "roic", "roic_avg_5y", "roe_min_5y", "roe", "roa", "operating_margin",
    "net_margin", "cash_conversion", "revenue_cagr_5y", "revenue_cagr_3y", "revenue_growth", "eps_cagr_5y",
    "margin_trend", "profitable_years", "dividend_years", "net_debt_ebitda", "interest_coverage", "altman_z",
    "piotroski", "current_ratio", "debt_to_equity", "fcf_yield", "pe", "pe_vs_history", "ev_ebit", "p_fcf", "ev_sales", "pb", "ptbv",
    "rotce", "efficiency_ratio", "provision_ratio", "loss_ratio", "p_ffo", "ffo_yield", "beneish_m", "accruals",
    "share_change", "sbc_to_revenue", "fcf_margin", "insider_buyers", "insider_net_pct", "funds_holding",
    "funds_adding",
})


def settings(cfg: dict | None = None) -> dict:
    """DEFAULTS fusi con `cfg["verdict"]` (dizionari annidati fusi chiave per chiave)."""
    out = copy.deepcopy(DEFAULTS)
    user = (cfg or {}).get("verdict", {}) or {}

    def merge(base: dict, extra: dict) -> None:
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = v
    merge(out, user)
    out["thresholds"] = {k: tuple(v) for k, v in out["thresholds"].items()}
    return out


# ------------------------------------------------------------------------------------------------ strutture
@dataclass
class VerdictInput:
    """Tutto ciò che serve per giudicare un'azienda. `metrics`: riga di `compute_metrics` (chiavi del catalogo)."""
    ticker: str
    metrics: dict
    sector: str | None = None
    sub_industry: str | None = None
    name: str | None = None
    currency: str = "USD"
    sec_filer: bool = True               # False per le europee: niente insider né 13F
    as_of: pd.Timestamp | None = None
    price_date: pd.Timestamp | None = None
    statement_date: pd.Timestamp | None = None
    user_fair_value: float | None = None
    wacc: float | None = None            # costo del capitale (Damodaran, modules/cost_of_capital.py)
    cost_of_equity: float | None = None
    terminal_growth: float = 0.025
    # DCF inverso: valore per azione per ogni crescita dei ricavi negli anni 1-5 (curva di valuation_model.value)
    dcf_growths: np.ndarray | None = None
    dcf_values: np.ndarray | None = None
    sector_peers: dict = field(default_factory=dict)  # metrica -> valori delle aziende simili (senza questa)
    sector_group: str | None = None      # descrizione del gruppo di confronto
    fcf_lag_days: float | None = None    # giorni tra l'ultimo FCF e l'ultimo bilancio
    quality_issues: list | None = None   # [(controllo, gravità, dettaglio)]; None = controllo non disponibile
    profile: str | None = None           # forza il tipo di azienda (altrimenti dal settore)
    data_notes: list = field(default_factory=list)  # cautele sui dati (es. bilancio da fonte di riserva): non decidono


@dataclass
class Evidence:
    key: str
    label: str
    pillar: str
    value: float | None
    shown: str            # valore leggibile
    reference: str        # soglia o termine di confronto
    outcome: str          # favorevole | neutro | contrario | mancante
    score: float | None   # 0-1
    weight: float
    text: str             # frase breve


@dataclass
class Pillar:
    key: str
    label: str
    score: float | None   # dopo la regola dei dati mancanti; None = dati insufficienti
    mean: float | None    # media delle sole prove disponibili
    coverage: float
    grade: str
    evidence: list[Evidence]
    missing: list[Evidence]


@dataclass
class Verdict:
    ticker: str
    category: str
    label: str
    rule: str
    profile: str
    pillars: dict[str, Pillar]
    coverage: float
    overall: float | None
    alarms: list[str]          # gravi
    checks: list[str]          # da verificare
    changes: list[str]
    data_issues: list[str]
    price: float | None
    as_of: pd.Timestamp | None
    disclaimer: str = DISCLAIMER
    data_notes: list[str] = field(default_factory=list)  # cautele sui dati: da leggere, non cambiano il giudizio

    @property
    def profile_label(self) -> str:
        return PROFILE_LABELS.get(self.profile, self.profile)

    def score(self, pillar: str) -> float | None:
        p = self.pillars.get(pillar)
        return p.score if p else None

    def to_row(self) -> dict:
        """Una riga per le tabelle (Esplora, Le mie aziende)."""
        return {"ticker": self.ticker, "verdict": self.label, "category": self.category, "profile": self.profile,
                **{k: self.score(k) for k in (QUALITY, SOLIDITY, PRICE, FLAGS, INSIDERS)},
                "coverage": self.coverage, "overall": self.overall, "alarms": len(self.alarms), "checks": len(self.checks),
                "rule": self.rule}

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """Il giudizio in poche righe di testo."""
        lines = [f"{self.ticker}: {self.label} ({self.profile_label}, copertura {_pct(self.coverage, 0)})",
                 f"Regola: {self.rule}"]
        for p in self.pillars.values():
            s = "–" if p.score is None else _sc(p.score)
            lines.append(f"  {p.label}: {s} {p.grade}")
        lines += [f"  ! {a}" for a in self.alarms] + [f"  ? {a}" for a in self.checks]
        lines += [f"  > {c}" for c in self.changes]
        return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ utilità
def _f(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return np.nan
    return x if np.isfinite(x) else np.nan


def _ok(x) -> bool:
    return np.isfinite(_f(x))


# Le frasi escono già in italiano (virgola decimale, punto delle migliaia, ≥): l'interfaccia le mostra così come
# sono. Solo il formato del testo: regole, soglie e punteggi non dipendono da qui.
MINUS = "\u2212"  # meno tipografico, come ui/components.fmt


def _it(text: str) -> str:
    """Numero scritto all'inglese -> italiano: "1,234.5" -> "1.234,5", "12.5%" -> "12,5%", "-6.5%" -> "−6,5%"
    (meno tipografico; mai "−0,0": uno zero arrotondato perde il segno)."""
    out = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    if "-" in out:
        out = out.replace("-", "", 1) if not re.search(r"[1-9]", out) else out
        out = out.replace("-", MINUS)
    return out


# numeri all'inglese dentro testi di altri moduli (dettagli di data_quality, in inglese anche nei rapporti)
_EN_NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+")


def _it_text(text: str) -> str:
    """Testo di un altro modulo con i numeri in italiano (solo quelli con decimali o migliaia)."""
    return _EN_NUMBER.sub(lambda m: _it(m.group(0)), str(text)) if text else text


def _pct(x: float, digits: int = 1, signed: bool = False) -> str:
    return _it(f"{x:+,.{digits}%}" if signed else f"{x:,.{digits}%}")


def _num(x: float, digits: int = 1) -> str:
    return _it(f"{x:,.{digits}f}")


def _sc(x: float) -> str:
    """Punteggio o soglia 0-1: "0,93"."""
    return _num(x, 2)


def _date(x) -> str:
    return f"{pd.Timestamp(x):%d/%m/%Y}"


def _fmt(key: str, value: float) -> str:
    """Valore leggibile: formato del catalogo se è una metrica, altrimenti quello della prova."""
    if not _ok(value):
        return "–"
    kind = EVIDENCE_FORMAT.get(key)
    if kind is None and key in METRIC_BY_KEY:
        return _it(format_value(key, value))
    if kind == "pct":
        return _pct(value)
    if kind == "pp":
        return _it(f"{value * 100:+.1f}") + " punti"
    if kind == "x":
        return _num(value) + "x"
    if kind == "int":
        return f"{int(round(value))}"
    if kind == "days":
        return f"{int(round(value))} giorni"
    if kind == "rank":
        return _pct(value, 0)
    return _num(value, 2)


EVIDENCE_FORMAT = {
    "revenue_cagr": "pct", "margin_sector": "rank", "multiples_sector": "rank", "fv_margin": "pct",
    "implied_growth_gap": "pp", "fcf_spread": "pp", "tce_assets": "pct", "equity_assets": "pct",
    "ptbv_justified": "x", "reit_net_debt_ebitda": "x", "reit_debt_to_equity": "x", "reit_interest_coverage": "x",
    "statement_age_days": "days", "fcf_lag_days": "days", "data_warnings": "int",
}
EVIDENCE_LABELS = {
    "revenue_cagr": "Crescita annua dei ricavi (5 anni, o 3)", "margin_sector": "Margine operativo nel settore",
    "multiples_sector": "Multipli rispetto al settore", "fv_margin": "Margine rispetto al tuo fair value",
    "implied_growth_gap": "Crescita implicita nel prezzo vs crescita storica",
    "fcf_spread": "Rendimento del FCF vs costo del capitale", "tce_assets": "Patrimonio tangibile / attivo",
    "equity_assets": "Patrimonio / attivo", "ptbv_justified": "P/TBV rispetto a quello giustificato dal ROTCE",
    "reit_net_debt_ebitda": METRIC_BY_KEY["net_debt_ebitda"].label,
    "reit_debt_to_equity": METRIC_BY_KEY["debt_to_equity"].label,
    "reit_interest_coverage": METRIC_BY_KEY["interest_coverage"].label,
    "statement_age_days": "Età dell'ultimo bilancio", "fcf_lag_days": "Ritardo del free cash flow",
    "data_warnings": "Avvisi sulla qualità dei dati",
}


def _label(key: str) -> str:
    return EVIDENCE_LABELS.get(key) or (METRIC_BY_KEY[key].label if key in METRIC_BY_KEY else key)


def _linear(value: float, good: float, bad: float) -> float:
    if good == bad:
        return 1.0 if value == good else 0.0
    return float(np.clip((value - bad) / (good - bad), 0.0, 1.0))


def _percentile_vs_peers(value: float, peers: np.ndarray, better: str) -> float:
    """Quota delle aziende simili che fanno peggio (pareggi a metà): 1 = la migliore."""
    if better == "high":
        return float(((peers < value).sum() + 0.5 * (peers == value).sum()) / len(peers))
    return float(((peers > value).sum() + 0.5 * (peers == value).sum()) / len(peers))


def profile_of(sector: str | None, sub_industry: str | None, metrics: dict) -> str:
    """Tipo di azienda per scegliere le prove: banca, assicurazione, REIT, altra finanziaria o generale.
    Settori GICS per l'S&P 500, ICB per le europee ("Banks", "Insurance", "Financial Services", "Real Estate")."""
    sector, sub = sector or "", sub_industry if isinstance(sub_industry, str) else ""
    financial = sector in ("Financials", "Financial Services", "Banks", "Insurance")
    if sector == "Banks" or (financial and any(w in sub for w in BANK_SUBINDUSTRIES)) or \
            (financial and _ok(metrics.get("efficiency_ratio"))):
        return "bank"
    if sector == "Insurance" or (financial and "Insurance" in sub and "Brokers" not in sub) or "Reinsurance" in sub:
        return "insurer"
    if financial:
        return "financial"
    if (sector == "Real Estate" and "REIT" in sub) or _ok(metrics.get("p_ffo")):
        return "reit"
    return "general"


# ------------------------------------------------------------------------------------------------ prove
def _implied_growth(inp: VerdictInput, price: float) -> tuple[float, str]:
    """Crescita dei ricavi (anni 1-5) che giustifica `price` sulla curva del DCF. Restituisce (crescita, posizione):
    posizione "sotto" = anche la crescita più bassa provata vale più del prezzo, "sopra" = nemmeno la più alta basta."""
    g, v = inp.dcf_growths, inp.dcf_values
    if g is None or v is None or len(g) < 2 or not _ok(price) or price <= 0:
        return np.nan, ""
    g, v = np.asarray(g, float), np.asarray(v, float)
    ok = np.isfinite(v)
    g, v = g[ok], v[ok]
    if len(v) < 2 or v[-1] <= v[0]:  # la crescita non crea valore: il DCF inverso non dice nulla
        return np.nan, ""
    diff = v - price
    if diff[0] >= 0:
        return float(g[0]), "sotto"
    if (diff < 0).all():
        return float(g[-1]), "sopra"
    i = int(np.argmax(diff >= 0))  # primo incrocio, come la bisezione di valuation_model.implied_growth
    x0, x1, y0, y1 = g[i - 1], g[i], diff[i - 1], diff[i]
    return float(x0 + (x1 - x0) * (-y0) / (y1 - y0)), ""


def _historical_growth(m: dict) -> tuple[float, str]:
    for key, label in (("revenue_cagr_5y", "degli ultimi 5 anni"), ("revenue_cagr_3y", "degli ultimi 3 anni")):
        if _ok(m.get(key)):
            return _f(m[key]), label
    return np.nan, ""


def _raw(inp: VerdictInput, key: str, cfg: dict, k: float = 1.0) -> tuple[float, str]:
    """Valore di una prova e, se serve, il termine di confronto in parole. `k` = prezzo ipotetico / prezzo di oggi
    (le prove di prezzo si ricalcolano scalando i multipli: serve a "cosa farebbe cambiare il giudizio").
    `cfg`: configurazione del giudizio (`settings`)."""
    m = inp.metrics
    price = _f(m.get("price"))
    if key == "revenue_cagr":
        g, label = _historical_growth(m)
        return g, label
    if key == "margin_sector":
        return _sector_rank(inp, "operating_margin", _f(m.get("operating_margin")), "high", cfg)
    if key == "multiples_sector":
        return _multiples_rank(inp, k, cfg)
    if key == "fv_margin":
        fv = _f(inp.user_fair_value)
        if not (_ok(fv) and _ok(price) and price > 0):
            return np.nan, ""
        p = price * k
        return fv / p - 1, f"il tuo fair value {_num(fv, 2)} contro un prezzo di {_num(p, 2)} {inp.currency}"
    if key == "implied_growth_gap":
        hist, hist_label = _historical_growth(m)
        if not _ok(hist):
            return np.nan, ""
        g, where = _implied_growth(inp, price * k)
        if not _ok(g):
            return np.nan, ""
        bound = {"sotto": "meno di ", "sopra": "più di "}.get(where, "")
        return g - hist, (f"il prezzo sconta una crescita dei ricavi di {bound}{_pct(g)} l'anno per 5 anni, "
                          f"contro il {_pct(hist)} {hist_label}")
    if key == "fcf_spread":
        fy, w = _f(m.get("fcf_yield")), _f(inp.wacc)
        if not (_ok(fy) and _ok(w)):
            return np.nan, ""
        required = w - inp.terminal_growth
        return fy / k - required, (f"rendimento del FCF {_pct(fy / k)} contro {_pct(required)} = costo del capitale "
                                   f"{_pct(w)} meno la crescita di lungo periodo {_pct(inp.terminal_growth)}")
    if key == "pe_vs_history":
        return _f(m.get("pe_vs_history")) * k, ""
    if key == "p_ffo":
        return _f(m.get("p_ffo")) * k, ""
    if key == "ptbv_justified":
        ptbv, rotce, ke, g = _f(m.get("ptbv")), _f(m.get("rotce")), _f(inp.cost_of_equity), inp.terminal_growth
        if not (_ok(ptbv) and _ok(rotce) and _ok(ke)) or ke <= g + 0.005:
            return np.nan, ""
        justified = (rotce - g) / (ke - g)
        if justified <= 0:  # rende meno della crescita: nessun prezzo sopra il patrimonio è giustificato
            return 9.99, (f"P/TBV {_num(ptbv * k)}x con un ROTCE del {_pct(rotce)}: sotto la crescita di lungo "
                          "periodo, nessun premio sul patrimonio è giustificato")
        return ptbv * k / justified, (f"P/TBV {_num(ptbv * k)}x contro {_num(justified)}x giustificato da ROTCE "
                                      f"{_pct(rotce)}, costo dell'equity {_pct(ke)}, crescita {_pct(g)}")
    if key == "tce_assets":
        roa, rotce = _f(m.get("roa")), _f(m.get("rotce"))
        return (roa / rotce if _ok(roa) and _ok(rotce) and rotce != 0 and roa * rotce > 0 else np.nan), \
            "da ROA / ROTCE"
    if key == "equity_assets":
        roa, roe = _f(m.get("roa")), _f(m.get("roe"))
        return (roa / roe if _ok(roa) and _ok(roe) and roe != 0 and roa * roe > 0 else np.nan), "da ROA / ROE"
    if key == "reit_net_debt_ebitda":
        return _f(m.get("net_debt_ebitda")), ""
    if key == "reit_interest_coverage":
        return _f(m.get("interest_coverage")), ""
    if key == "reit_debt_to_equity":
        return _f(m.get("debt_to_equity")), ""
    if key == "statement_age_days":
        if inp.statement_date is None or inp.as_of is None or pd.isna(inp.statement_date):
            return np.nan, ""
        return float((pd.Timestamp(inp.as_of) - pd.Timestamp(inp.statement_date)).days), \
            f"chiusura del periodo {_date(inp.statement_date)}"
    if key == "fcf_lag_days":
        return _f(inp.fcf_lag_days), ""
    if key == "data_warnings":
        if inp.quality_issues is None:
            return np.nan, ""
        hits = [f"{c}: {_it_text(d)}" for c, s, d in inp.quality_issues if c in WARNING_CHECKS]
        return float(len(hits)), "; ".join(hits)
    if key in ("insider_buyers", "insider_net_pct", "funds_holding", "funds_adding") and not inp.sec_filer:
        return np.nan, ""
    return _f(m.get(key)), ""


def _sector_rank(inp: VerdictInput, metric: str, value: float, better: str, cfg: dict) -> tuple[float, str]:
    peers = inp.sector_peers.get(metric)
    if not _ok(value) or peers is None:
        return np.nan, ""
    peers = np.asarray(peers, float)
    peers = peers[np.isfinite(peers)]
    if len(peers) < int(cfg.get("min_sector_peers", DEFAULTS["min_sector_peers"])):
        return np.nan, ""
    r = _percentile_vs_peers(value, peers, better)
    return r, f"meglio del {_pct(r, 0)} delle {len(peers)} aziende {inp.sector_group or 'del settore'}"


def _multiples_rank(inp: VerdictInput, k: float, cfg: dict) -> tuple[float, str]:
    m = inp.metrics
    mcap, ev = _f(m.get("market_cap")), _f(m.get("enterprise_value"))
    ev_k = ev + mcap * (k - 1) if _ok(ev) and _ok(mcap) else np.nan
    ranks, names = [], []
    for key in cfg.get("sector_multiples", {}).get(_profile(inp), []):
        v = _f(m.get(key))
        if key.startswith("ev_"):
            v = v * ev_k / ev if _ok(v) and _ok(ev_k) and ev > 0 and ev_k > 0 else np.nan
        else:
            v = v * k
        r, _ = _sector_rank(inp, key, v, "low", cfg)
        if _ok(r):
            ranks.append(r)
            names.append(f"{METRIC_BY_KEY[key].label} {_it(format_value(key, v))}")
    if len(ranks) < 2:
        return np.nan, ""
    r = float(np.mean(ranks))
    return r, f"più economica del {_pct(r, 0)} delle aziende {inp.sector_group or 'del settore'} in media ({', '.join(names)})"


def _profile(inp: VerdictInput) -> str:
    return inp.profile or profile_of(inp.sector, inp.sub_industry, inp.metrics)


def _score(key: str, value: float, good: float, bad: float) -> float:
    """Punteggio 0-1 di una prova: lineare tra `bad` (0) e `good` (1); CENTERED con lo zero a 0,5; ONE_SIDED da 0,5."""
    s = _linear(value, good, bad)
    if key in CENTERED:
        s = 0.5 + 0.5 * _linear(value, good, 0.0) if value >= 0 else 0.5 * _linear(value, 0.0, bad)
    if key in ONE_SIDED:
        s = 0.5 + 0.5 * s
    return s


def _outcome_bound(key: str, good: float, bad: float, target: float, favorable: bool) -> float | None:
    """Valore da cui la prova diventa favorevole (punteggio >= target) o fino a cui è contraria (<= target),
    cercato sulla stessa funzione `_score` (monotona da `bad` a `good`). None se l'esito non è raggiungibile."""
    at = lambda t: _score(key, bad + t * (good - bad), good, bad)  # noqa: E731
    if favorable and at(1.0) < target or not favorable and at(0.0) > target:
        return None
    lo, hi = 0.0, 1.0  # favorevole: il t più piccolo con at >= target; contraria: il più grande con at <= target
    for _ in range(60):
        mid = (lo + hi) / 2
        if favorable:
            lo, hi = (lo, mid) if at(mid) >= target else (mid, hi)
        else:
            lo, hi = (mid, hi) if at(mid) <= target else (lo, mid)
    return bad + (hi if favorable else lo) * (good - bad)


def _is_integer(key: str) -> bool:
    kind = EVIDENCE_FORMAT.get(key)
    return kind in ("int", "days") or (kind is None and key in METRIC_BY_KEY and METRIC_BY_KEY[key].fmt == "int")


def _fmt_bound(key: str, value: float, others: tuple) -> str:
    """Soglia d'esito leggibile; con un decimale in più se arrotondata si confonderebbe con l'altra soglia o con lo
    zero (es. 0,03% non diventa "0,0%", che darebbe un esito diverso)."""
    text = _fmt(key, value)
    if _is_integer(key) or not any(o is not None and o != value and _fmt(key, o) == text for o in others):
        return text
    kind = EVIDENCE_FORMAT.get(key) or (METRIC_BY_KEY[key].fmt if key in METRIC_BY_KEY else "num")
    if kind == "pct":
        return _pct(value, 2)
    if kind == "rank":
        return _pct(value, 1)
    if kind == "pp":
        return _it(f"{value * 100:+.2f}") + " punti"
    if kind == "x":
        return _num(value, 2) + "x"
    if kind == "money":
        return text
    return _num(value, 3)


def _reference(key: str, good: float, bad: float, cfg: dict) -> str:
    """Riferimento di una prova: le soglie dell'ESITO (favorevole da punteggio `favorable`, contrario fino a
    `contrary`) e da dove il punteggio è pieno. Solo testo: l'esito si decide dal punteggio in `_evidence`."""
    return _reference_cached(key, float(good), float(bad), float(cfg["favorable"]), float(cfg["contrary"]))


@functools.lru_cache(maxsize=1024)
def _reference_cached(key: str, good: float, bad: float, favorable: float, contrary: float) -> str:
    up = good > bad  # più alto è meglio
    fav = _outcome_bound(key, good, bad, favorable, True)
    con = _outcome_bound(key, good, bad, contrary, False)
    if _is_integer(key):  # valori interi: il primo intero che dà davvero quell'esito
        if fav is not None:
            fav = float(math.ceil(fav - 1e-9) if up else math.floor(fav + 1e-9))
        if con is not None:
            con = float(math.floor(con + 1e-9) if up else math.ceil(con - 1e-9))
    parts = []
    if fav is not None:
        parts.append(f"favorevole {'da' if up else 'fino a'} {_fmt_bound(key, fav, (con, 0.0))}")
    parts.append(f"contrario {'fino a' if up else 'da'} {_fmt_bound(key, con, (fav, 0.0))}" if con is not None
                 else "mai contrario")
    return ", ".join(parts) + f"; punteggio pieno {'da' if up else 'fino a'} {_fmt(key, good)}"


def _evidence(inp: VerdictInput, key: str, pillar: str, cfg: dict, k: float = 1.0,
              weight: float | None = None) -> Evidence:
    good, bad = cfg["thresholds"][key]
    w = float(cfg["weights"].get(key, 1.0) if weight is None else weight)
    value, context = _raw(inp, key, cfg, k)
    label = _label(key)
    if not _ok(value):
        return Evidence(key, label, pillar, None, "–", "", "mancante", None, w, f"{label}: dato non disponibile")
    s = _score(key, value, good, bad)
    outcome = "favorevole" if s >= cfg["favorable"] else "contrario" if s <= cfg["contrary"] else "neutro"
    if key in ONE_SIDED | CENTERED and s == 0.5:
        outcome = "neutro"
    if key in ("margin_sector", "multiples_sector"):
        ref = context
        context = ""
    else:
        ref = _reference(key, good, bad, cfg)
    shown = _fmt(key, value)
    text = f"{label}: {shown}, {outcome} ({ref})" + (f"; {context}" if context else "")
    return Evidence(key, label, pillar, float(value), shown, ref, outcome, s, w, text)


def _pillar(key: str, evidence: list[Evidence], cfg: dict) -> Pillar:
    avail = [e for e in evidence if e.score is not None]
    missing = [e for e in evidence if e.score is None]
    total = sum(e.weight for e in evidence)
    have = sum(e.weight for e in avail)
    coverage = have / total if total > 0 else 0.0
    mean = sum(e.score * e.weight for e in avail) / have if have > 0 else None
    score = None
    if mean is not None and coverage >= cfg["min_pillar_coverage"]:
        score = coverage * mean + (1 - coverage) * min(mean, 0.5)  # un dato mancante non alza mai il punteggio
    grade = "Dati insufficienti"
    if score is not None:
        grade = next(word for floor, word in GRADES[key] if score >= floor)
    return Pillar(key, PILLAR_LABELS[key], score, mean, coverage, grade, evidence, missing)


def _price_pillar(inp: VerdictInput, cfg: dict, k: float = 1.0) -> Pillar:
    keys = cfg["profiles"][_profile(inp)][PRICE]
    ev = [_evidence(inp, key, PRICE, cfg, k) for key in keys]
    fv = _evidence(inp, "fv_margin", PRICE, cfg, k, weight=0.0)
    if fv.score is not None:  # il TUO fair value pesa `user_fair_value_share` del pilastro
        share = float(cfg["user_fair_value_share"])
        others = sum(e.weight for e in ev)
        fv.weight = others * share / (1 - share) if share < 1 else 1.0
        if share >= 1:
            for e in ev:
                e.weight = 0.0
        ev = [fv] + ev
    return _pillar(PRICE, ev, cfg)


def _alarms(inp: VerdictInput, profile: str, cfg: dict) -> tuple[list[str], list[str]]:
    """(allarmi gravi, allarmi da verificare). Grave = da solo porta a "Da evitare" (dissesto, debito che non si
    ripaga, perdite con cassa bruciata). Da verificare = indizio statistico (Beneish) che va chiarito leggendo i
    bilanci: impedisce "Candidata" ma non condanna (le aziende in crescita molto rapida lo fanno scattare spesso)."""
    m, a = inp.metrics, cfg["alarms"]
    g = lambda k: _f(m.get(k))  # noqa: E731
    out, check = [], []
    if profile in ("general", "reit", "financial"):
        z, cov, lev = g("altman_z"), g("interest_coverage"), g("net_debt_ebitda")
        if profile == "general" and _ok(z) and _ok(cov) and z < a["distress_altman"] and cov < a["distress_coverage"]:
            out.append(f"Rischio di dissesto: Altman Z {_num(z, 2)} (zona di pericolo sotto {_num(a['distress_altman'])}) "
                       f"e interessi coperti solo {_num(cov)} volte")
        if _ok(cov) and cov < a["debt_trap_coverage"] and _ok(lev) and lev > a["debt_trap_leverage"]:
            out.append(f"L'utile operativo non copre gli interessi ({_num(cov)}x) con un debito di {_num(lev)} anni di "
                       "EBITDA")
    if profile in ("general", "financial"):
        fm, nm = g("fcf_margin"), g("net_margin")
        if _ok(fm) and _ok(nm) and fm < 0 and nm < 0:
            out.append(f"In perdita ({_pct(nm)} dei ricavi) e brucia cassa (free cash flow {_pct(fm)} dei ricavi)")
        b, acc = g("beneish_m"), g("accruals")
        if _ok(b) and _ok(acc) and b > a["beneish"] and acc > a["beneish_accruals"]:
            check.append(f"Possibili bilanci gonfiati: Beneish M {_num(b, 2)} sopra {_num(a['beneish'], 2)} e utili poco "
                       f"coperti da cassa (accantonamenti {_pct(acc)} dell'attivo)")
    if profile in ("bank", "insurer"):
        r = g("rotce")
        if _ok(r) and r < 0:
            out.append(f"In perdita: ROTCE {_pct(r)}")
    return out, check


def _data_errors(inp: VerdictInput, cfg: dict) -> list[str]:
    out = [f"{c}: {_it_text(d)}" for c, s, d in (inp.quality_issues or []) if s == "errore"]
    if not _ok(inp.metrics.get("price")):
        out.append("prezzo non disponibile o non aggiornato")
    elif inp.price_date is not None and inp.as_of is not None and \
            (pd.Timestamp(inp.as_of) - pd.Timestamp(inp.price_date)).days > int(cfg["max_price_age_days"]):
        out.append(f"ultimo prezzo del {_date(inp.price_date)}")
    return out


# ------------------------------------------------------------------------------------------------ regole
def _decide(p: dict[str, Pillar], coverage: float, alarms: list[str], errors: list[str],
            cfg: dict, checks: list[str] | None = None) -> tuple[str, str]:
    """Le regole del giudizio complessivo, in ordine: la prima che vale decide."""
    q, s, pr, fl = (p[k].score for k in (QUALITY, SOLIDITY, PRICE, FLAGS))
    if errors:
        return NO_DATA, "Problemi nei dati (" + "; ".join(errors[:2]) + "): non si giudica su numeri sbagliati."
    short = [p[k].label for k in (QUALITY, SOLIDITY, PRICE) if p[k].score is None]
    if short:
        return NO_DATA, (f"Prove insufficienti per: {', '.join(short)} (serve almeno il "
                         f"{_pct(cfg['min_pillar_coverage'], 0)} delle prove di ogni pilastro).")
    if coverage < cfg["min_coverage"]:
        return NO_DATA, (f"Disponibile solo il {_pct(coverage, 0)} delle prove (minimo {_pct(cfg['min_coverage'], 0)}): un dato "
                         "mancante non conta mai come positivo.")
    qg, qb, sg, sb, pg = (cfg[k] for k in ("quality_good", "quality_bad", "solidity_good", "solidity_bad",
                                           "price_good"))
    if alarms:
        return AVOID, f"Allarme grave: {alarms[0]}."
    weak = q < qb or s < sb
    if weak and pr >= pg:
        return VALUE_TRAP, (f"Prezzo {_sc(pr)} ≥ {_sc(pg)} ma Qualità {_sc(q)} (minimo {_sc(qb)}) o Solidità {_sc(s)} "
                            f"(minimo {_sc(sb)}) troppo basse: il prezzo basso potrebbe avere un motivo.")
    if weak:
        return AVOID, (f"Qualità {_sc(q)} sotto {_sc(qb)} o Solidità {_sc(s)} sotto {_sc(sb)}, e il prezzo ({_sc(pr)}) non "
                       "compensa.")
    if q >= qg and s >= sg:
        brake = cfg.get("price_mode", "brake") == "brake"
        need = float(cfg.get("price_brake", 0.40)) if brake else pg
        price_ok = f"Prezzo {_sc(pr)} ≥ {_sc(need)}" + (" (non alto)" if brake else "")
        if pr >= need and checks:
            return NEUTRAL, (f"Qualità e solidità superano le soglie e il prezzo non le blocca, ma c'è un allarme da "
                             f"verificare: {checks[0]}.")
        if pr >= need and fl is not None and fl >= cfg["flags_ok"]:
            return CANDIDATE, (f"Qualità {_sc(q)} ≥ {_sc(qg)}, Solidità {_sc(s)} ≥ {_sc(sg)}, {price_ok} e segnali "
                               f"d'allarme sotto controllo ({_sc(fl)} ≥ {_sc(cfg['flags_ok'])}).")
        if pr >= need:
            fl_s = "non verificabili" if fl is None else f"{_sc(fl)}, sotto {_sc(cfg['flags_ok'])}"
            return NEUTRAL, (f"Qualità e solidità superano le soglie e il prezzo non le blocca, ma i segnali "
                             f"d'allarme ({fl_s}) vanno verificati prima.")
        return WAIT, (f"Qualità {_sc(q)} ≥ {_sc(qg)} e Solidità {_sc(s)} ≥ {_sc(sg)}, ma Prezzo {_sc(pr)} sotto {_sc(need)}"
                      + (": il prezzo è alto." if brake else ": il prezzo non lascia margine."))
    if pr >= pg:
        return VALUE_TRAP, (f"Prezzo {_sc(pr)} ≥ {_sc(pg)} ma Qualità {_sc(q)} o Solidità {_sc(s)} solo nella media "
                            f"(servono {_sc(qg)} e {_sc(sg)}).")
    return NEUTRAL, (f"Qualità {_sc(q)}, Solidità {_sc(s)}, Prezzo {_sc(pr)}: nessuna soglia decisiva superata né "
                     "mancata.")


def _worst(pillar: Pillar, n: int = 3) -> str:
    bad = sorted([e for e in pillar.evidence if e.score is not None and e.outcome != "favorevole"],
                 key=lambda e: (-(1 - e.score) * e.weight, e.key))[:n]
    return "; ".join(f"{e.label} {e.shown}" for e in bad)


def _changes(inp: VerdictInput, cfg: dict, pillars: dict, coverage: float, alarms: list[str], errors: list[str],
             category: str, checks: list[str]) -> list[str]:
    """Cosa farebbe cambiare il giudizio, a parità del resto."""
    out = []
    price = _f(inp.metrics.get("price"))
    if category == NO_DATA:
        if errors:
            out.append("Correggere i dati: " + "; ".join(errors[:3]) + ".")
        for k in (QUALITY, SOLIDITY, PRICE):
            miss = [e.label for e in pillars[k].missing]
            if miss and (pillars[k].score is None or coverage < cfg["min_coverage"]):
                out.append(f"{pillars[k].label}: mancano {', '.join(miss[:5])}.")
        if inp.user_fair_value is None and pillars[PRICE].score is None:
            out.append("Una tua valutazione (fair value) renderebbe giudicabile il prezzo.")
        return out

    def category_at(k: float) -> str:
        p = dict(pillars)
        p[PRICE] = _price_pillar(inp, cfg, k)
        return _decide(p, coverage, alarms, errors, cfg, checks)[0]

    if category in (WAIT, NEUTRAL, VALUE_TRAP) and _ok(price):
        target = next((k for k in np.round(np.arange(0.99, 0.295, -0.01), 2) if category_at(k) == CANDIDATE), None)
        if target is not None:
            out.append(f"Sotto circa {_num(price * target, 2)} {inp.currency} ({_pct(target - 1, 0, signed=True)} da oggi) il giudizio "
                       f"diventerebbe \"{LABELS[CANDIDATE]}\", a parità del resto.")
        elif category == WAIT:
            out.append("Nemmeno un prezzo più basso del 70% basterebbe da solo: pesano anche altre prove.")
    if category == CANDIDATE and _ok(price):
        stop = next((k for k in np.round(np.arange(1.01, 2.005, 0.01), 2) if category_at(k) != CANDIDATE), None)
        if stop is not None:
            out.append(f"Sopra circa {_num(price * stop, 2)} {inp.currency} ({_pct(stop - 1, 0, signed=True)} da oggi) il prezzo non "
                       "lascerebbe più margine sufficiente.")
    if alarms:
        out.append("Gli allarmi gravi vanno chiariti prima di tutto: " + "; ".join(alarms) + ".")
    if checks:
        out.append("Da verificare nei bilanci (se si chiarisce, il giudizio può migliorare): " + "; ".join(checks) + ".")
    for k, need in ((QUALITY, cfg["quality_good"]), (SOLIDITY, cfg["solidity_good"])):
        sc = pillars[k].score
        if sc is not None and sc < need and category in (VALUE_TRAP, AVOID, NEUTRAL):
            worst = _worst(pillars[k])
            out.append(f"{pillars[k].label} {_sc(sc)}: servirebbe almeno {_sc(need)}" +
                       (f" (pesano contro: {worst})." if worst else "."))
    fl = pillars[FLAGS].score
    if category in (NEUTRAL, CANDIDATE) and fl is not None and fl < cfg["flags_ok"]:
        worst = _worst(pillars[FLAGS])
        out.append(f"Segnali d'allarme {_sc(fl)}: da verificare" + (f" ({worst})." if worst else "."))
    return out


# ------------------------------------------------------------------------------------------------ funzione pura
def judge(inp: VerdictInput, cfg: dict | None = None) -> Verdict:
    """Il giudizio su un'azienda dai suoi numeri. Deterministico, senza accesso a dati esterni.
    `cfg`: la configurazione completa (si usa la sezione `verdict`) oppure già passata da `settings`."""
    c = cfg if cfg is not None and "thresholds" in cfg and "profiles" in cfg else settings(cfg)
    profile = _profile(inp)
    spec = c["profiles"][profile]
    pillars = {
        QUALITY: _pillar(QUALITY, [_evidence(inp, k, QUALITY, c) for k in spec[QUALITY]], c),
        SOLIDITY: _pillar(SOLIDITY, [_evidence(inp, k, SOLIDITY, c) for k in spec[SOLIDITY]], c),
        PRICE: _price_pillar(inp, c),
        FLAGS: _pillar(FLAGS, [_evidence(inp, k, FLAGS, c) for k in spec[FLAGS]], c),
        INSIDERS: _pillar(INSIDERS, [_evidence(inp, k, INSIDERS, c) for k in c["insider_evidence"]], c),
    }
    judged = [pillars[k] for k in (QUALITY, SOLIDITY, PRICE, FLAGS)]
    total = sum(e.weight for p in judged for e in p.evidence)
    coverage = sum(e.weight for p in judged for e in p.evidence if e.score is not None) / total if total else 0.0
    alarms, checks = _alarms(inp, profile, c)
    errors = _data_errors(inp, c)
    category, rule = _decide(pillars, coverage, alarms, errors, c, checks)
    ow = c["overall_weights"]
    parts = [(pillars[k].score, w) for k, w in ow.items() if pillars[k].score is not None]
    overall = sum(s * w for s, w in parts) / sum(w for _, w in parts) \
        if parts and category != NO_DATA and sum(w for _, w in parts) > 0 else None
    return Verdict(
        ticker=inp.ticker, category=category, label=LABELS[category], rule=rule, profile=profile, pillars=pillars,
        coverage=float(coverage), overall=overall, alarms=alarms, checks=checks,
        changes=_changes(inp, c, pillars, coverage, alarms, errors, category, checks), data_issues=errors,
        price=_f(inp.metrics.get("price")) if _ok(inp.metrics.get("price")) else None, as_of=inp.as_of,
        data_notes=list(inp.data_notes or []))


# ------------------------------------------------------------------------------------------------ dati dell'app
FALLBACK_PREFIX = "yahoo_fill"  # modules/yahoo_fundamentals.py: perde sempre contro le fonti primarie
SOURCE_NOTE_METRICS = {"revenue": "ricavi", "operating_income": "utile operativo", "net_income": "utile netto",
                       "free_cash_flow": "free cash flow", "total_assets": "attivo", "equity": "patrimonio netto",
                       "total_debt": "debito", "cash": "cassa", "shares": "azioni"}


class _Context:
    """Ciò che serve a tutti i titoli, calcolato una volta: metriche, settori, gruppi di confronto, note, avvisi."""

    def __init__(self, d, tickers: list[str]):
        from modules.metrics import compute_metrics

        self.d = d
        m = d.metrics()
        extra = [t for t in tickers if t not in m.index]
        if extra:
            m = pd.concat([m, compute_metrics(d.data, extra)])
        self.m = m
        self.sectors = d.sectors()
        comp = d.companies
        self.sub = comp["sub_industry"].to_dict() if "sub_industry" in comp else {}
        self.names = comp["name"].to_dict() if "name" in comp else {}
        self.index = set(d.index_tickers)
        self.foreign = set(d.foreign["ticker"]) if not d.foreign.empty else set()
        notes = d.db.load_notes()
        self.fair = pd.to_numeric(notes.set_index("ticker")["fair_value"], errors="coerce").dropna().to_dict() \
            if not notes.empty else {}
        q = d.quality_issues()
        self.quality_available = not q.empty
        self.quality = {t: list(zip(g["check"], g["severity"], g["detail"].astype(str)))
                        for t, g in q.groupby("ticker")} if not q.empty else {}
        v = d.cfg["valuation"]
        self.terminal_growth = float(v["terminal_growth"])
        self.skip_dcf = set(v.get("skip_sectors", []))
        self.max_fcf_lag = int(v.get("max_fcf_lag_days", 400))
        self.cfg = settings(d.cfg)
        lo, hi, n = self.cfg["implied_growth_grid"]
        self.grid = np.linspace(float(lo), float(hi), int(n))
        # gruppi di confronto: S&P 500 con S&P 500, europee con europee, le altre con tutte
        self._groups: dict[tuple, pd.DataFrame] = {}
        fin = d.data.financials
        self.fallback = set(fin.loc[fin["source"].astype(str).str.startswith(FALLBACK_PREFIX), "ticker"]) \
            if not fin.empty and "source" in fin.columns else set()

    def group_of(self, t: str) -> tuple[str, str]:
        if t in self.index:
            return "S&P 500", "dell'S&P 500"
        if t in self.foreign:
            return "Europa", "europee"
        return "Tutte", ""

    def peers(self, t: str) -> tuple[dict, str]:
        sector = self.sectors.get(t)
        if not sector:
            return {}, ""
        group, words = self.group_of(t)
        key = (group, sector)
        if key not in self._groups:
            members = self.index if group == "S&P 500" else self.foreign if group == "Europa" else set(self.m.index)
            rows = [x for x in self.m.index if self.sectors.get(x) == sector and x in members]
            self._groups[key] = self.m.loc[rows, [c for c in SECTOR_PEER_METRICS if c in self.m.columns]]
        table = self._groups[key]
        table = table[table.index != t]
        peers = {c: table[c].to_numpy(dtype=float) for c in table.columns}
        return peers, f"del settore {sector}" + (f" {words}" if words else "")

    def dcf(self, t: str, profile: str) -> dict:
        """Costo del capitale e curva valore-crescita del DCF a leve (scenario base del sistema, senza le tue
        ipotesi salvate: è il prezzo di mercato che si interroga)."""
        out = {"wacc": None, "cost_of_equity": None, "dcf_growths": None, "dcf_values": None}
        try:
            if profile in ("general",) and self.sectors.get(t) not in self.skip_dcf:
                from modules.valuation_model import Levers, value

                setup = self.d.valuation_setup(t, use_saved=False)
                if setup is not None:
                    coc = setup["cost_of_capital"]
                    out.update(wacc=setup["system_wacc"], cost_of_equity=coc.get("cost_of_equity"))
                    base, lv = setup["base"], setup["scenarios"]["Base"]
                    if base.margin > 0:
                        res = value(base, Levers(**{**lv.to_dict(), "growth": self.grid}))
                        out.update(dcf_growths=self.grid, dcf_values=np.asarray(res.per_share, float))
                    return out
            coc = self.d.cost_of_capital(t)
            out.update(wacc=coc.get("wacc"), cost_of_equity=coc.get("cost_of_equity"))
        except Exception as exc:  # noqa: BLE001 - senza DCF le prove relative restano mancanti, non si blocca
            logger.warning("Giudizio %s: costo del capitale / DCF non calcolati (%s)", t, exc)
        return out

    def fcf_lag(self, t: str) -> float | None:
        st = self.d.data.fundamentals(t)
        if st.empty or "free_cash_flow" not in st:
            return None
        fcf = st["free_cash_flow"].dropna()
        if fcf.empty:
            return None
        return float((st.index[-1] - fcf.index[-1]).days)

    def source_notes(self, t: str) -> list[str]:
        """Cautela se l'ultimo bilancio usato dalle metriche ha voci prese dalla fonte di riserva (Yahoo)."""
        if t not in self.fallback:
            return []
        data = self.d.data
        fund = data.fundamentals(t)
        if fund.empty:
            return []
        day = fund.index[-1]
        src = data.statement_sources(t, ttm=day not in data.statements(t).index)
        if day not in src.index:
            return []
        row = src.loc[day]
        fb = [k for k in SOURCE_NOTE_METRICS if str(row.get(k, "")).startswith(FALLBACK_PREFIX)]
        if not fb:
            return []
        whole = len(fb) == len([k for k in SOURCE_NOTE_METRICS if pd.notna(row.get(k))])
        what = "preso interamente da Yahoo" if whole else \
            "completato da Yahoo per: " + ", ".join(SOURCE_NOTE_METRICS[k] for k in fb)
        return [f"Ultimo bilancio ({_date(day)}) {what} (fonte di riserva, non il deposito ufficiale): verificare i "
                "numeri sul documento dell'azienda prima di decidere."]

    def build(self, t: str) -> VerdictInput:
        row = self.m.loc[t] if t in self.m.index else pd.Series(dtype=float)
        metrics = {k: _f(row.get(k)) for k in INPUT_METRICS}
        sector, sub = self.sectors.get(t), self.sub.get(t)
        profile = profile_of(sector, sub, metrics)
        peers, words = self.peers(t)
        px = self.d.data.prices(t)
        sd = row.get("statement_date")
        return VerdictInput(
            ticker=t, metrics=metrics, sector=sector, sub_industry=sub, name=self.names.get(t),
            currency=self.d.currency(t), sec_filer=t not in self.foreign, as_of=self.d.data.last_date,
            price_date=px.index[-1] if not px.empty else None,
            statement_date=pd.Timestamp(sd) if sd is not None and not pd.isna(sd) else None,
            user_fair_value=self.fair.get(t), terminal_growth=self.terminal_growth,
            sector_peers=peers, sector_group=words, fcf_lag_days=self.fcf_lag(t),
            quality_issues=self.quality.get(t, []) if self.quality_available else None,
            profile=profile, data_notes=self.source_notes(t), **self.dcf(t, profile))


def assess_ticker(d, ticker: str) -> Verdict:
    """Giudizio su un titolo con i dati dell'app (DashboardData)."""
    ctx = _Context(d, [ticker])
    return judge(ctx.build(ticker), ctx.cfg)


def assess_many(d, tickers: list[str] | None = None, return_verdicts: bool = False):
    """Tabella dei giudizi (una riga per titolo: giudizio, punteggi dei pilastri, copertura...), per Esplora e
    "Le mie aziende". Riusa la tabella delle metriche già calcolata. Con `return_verdicts` anche i Verdict."""
    tickers = list(d.index_tickers if tickers is None else tickers)
    ctx = _Context(d, tickers)
    verdicts = {}
    for t in tickers:
        try:
            verdicts[t] = judge(ctx.build(t), ctx.cfg)
        except Exception as exc:  # noqa: BLE001 - un titolo con dati strani non deve fermare la tabella
            logger.warning("Giudizio %s non calcolato: %s", t, exc)
    rows = [v.to_row() for v in verdicts.values()]
    table = pd.DataFrame(rows, columns=["ticker", "verdict", "category", "profile", QUALITY, SOLIDITY, PRICE, FLAGS,
                                        INSIDERS, "coverage", "overall", "alarms", "checks", "rule"])
    table.insert(1, "name", table["ticker"].map(ctx.names))
    table.insert(2, "sector", table["ticker"].map(ctx.sectors))
    table["_order"] = table["category"].map({c: i for i, c in enumerate(ORDER)})
    table = table.sort_values(["_order", "overall", "ticker"], ascending=[True, False, True]) \
        .drop(columns="_order").set_index("ticker")
    return (table, verdicts) if return_verdicts else table
