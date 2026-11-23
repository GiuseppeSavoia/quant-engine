"""
Tavolo dei grandi investitori: come le regole tradotte dal metodo pubblicato di ciascun investitore del catalogo
(modules/investors.py, Magic Formula e Piotroski di strategies.py) leggono i numeri di un'azienda, e una sintesi.

SOLO INFORMAZIONE: non entra nel giudizio (verdict.py), negli avvisi, nel sito pubblico, nelle strategie. Niente AI:
ogni esito e ogni frase vengono da regole e testi fissi qui sotto, riempiti con i numeri del catalogo delle metriche.

Per ogni investitore, con la STESSA logica di `Strategy._run_rules` (universo = membri dell'indice nei suoi settori,
`screener.apply_rules`, classifica `strategies.rank_score` sui soli titoli che passano i filtri, primi N):
  universo     il settore dell'azienda è tra i suoi? (se no: "Fuori dal suo campo")
  regole       passata / mancata (di quanto) / dato mancante, regola per regola
  classifica   posizione tra chi passa i filtri: rientra nei suoi N titoli?
  esito        regole in ordine, scritte in chiaro in `_decide`
  da cambiare  per le regole di prezzo il prezzo a cui passerebbe (calcolato ricalcolando la metrica a quel prezzo
               e rifacendo la regola, non stimato); per le altre il valore che servirebbe

Regola ferma (come nel giudizio): un dato mancante non conta mai come regola passata.
Soglie del "quasi" in config.yaml, sezione `investor_panel` (default in modules/config.py). Le regole degli
investitori NON si toccano qui: sono quelle fissate a priori in investors.py.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from modules.formats import fmt, it_text
from modules.metrics import METRIC_BY_KEY, format_value, percentile_ranks
from modules.screener import RANK_OPS, apply_rules, describe_rule
from modules.strategies import rank_score

NOTE = ("Come le regole tradotte dal metodo pubblicato di ogni investitore leggono i numeri di oggi. Non è il loro "
        "giudizio e non è una raccomandazione.")

DEFAULTS = {"near_miss": 0.10, "near_miss_rank": 0.10, "near_miss_abs": 0.01, "price_range": [0.05, 20.0]}

# esiti, nell'ordine in cui si mostrano
OUTCOMES: dict[str, tuple[str, str]] = {
    "compra": ("La comprerebbe", "pos"),
    "piace": ("Le piace, ma ne ha di migliori", "info"),
    "quasi": ("Quasi", "warn"),
    "no": ("Non fa per lui", "neg"),
    "fuori": ("Fuori dal suo campo", "off"),
    "dati": ("Dati insufficienti", "off"),
}
POSITIVE = {"compra", "piace"}

# famiglie di stile, fissate a priori (per i disaccordi della sintesi); gli altri non hanno famiglia
FAMILIES: dict[str, list[str]] = {
    "qualità": ["Munger", "Terry Smith", "Buffett"],
    "value profondo": ["Graham", "Schloss", "Dreman", "Templeton", "Klarman"],
    "crescita e momentum": ["O'Neil", "Driehaus", "Lynch", "Zweig"],
}

# metriche che dipendono dal prezzo di oggi e come cambiano se il prezzo è f volte quello di oggi
# (mul: v * f**k; div: v / f; ev_*: tramite l'enterprise value = capitalizzazione * f + debito netto;
#  low/high: distanza dal minimo/massimo di 52 settimane, che si sposta se il prezzo li supera)
PRICE_METRICS: dict[str, tuple[str, int]] = {
    "pe": ("mul", 1), "pb": ("mul", 1), "ps": ("mul", 1), "peg": ("mul", 1), "pe_vs_history": ("mul", 1),
    "p_fcf": ("mul", 1), "pe_x_pb": ("mul", 2),
    "dividend_yield": ("div", 1), "earnings_yield": ("div", 1), "fcf_yield": ("div", 1),
    "shareholder_yield": ("div", 1),
    "ev_ebit": ("ev_mul", 1), "ev_ebitda": ("ev_mul", 1), "ev_sales": ("ev_mul", 1), "fcf_ev_yield": ("ev_div", 1),
    "from_52w_low": ("low", 1), "drawdown_52w": ("high", 1),
}


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def gics_sectors(sectors: dict[str, str]) -> dict[str, str]:
    """Settori nella classificazione delle strategie (GICS): le europee hanno in anagrafica i settori ICB ("Banks",
    "Automobiles and Parts"...), tradotti come per il costo del capitale (`cost_of_capital.ICB_TO_GICS`). Senza, ogni
    europea sarebbe "fuori campo" e da sola nel suo gruppo nelle regole "tra i migliori del settore"."""
    from modules.cost_of_capital import ICB_TO_GICS

    return {t: ICB_TO_GICS.get(x, x) if isinstance(x, str) else x for t, x in sectors.items()}


def short_name(name: str) -> str:
    """"Graham – investitore difensivo" -> "Graham"."""
    return name.split(" – ")[0].strip()


def investor_catalog(catalog: dict) -> dict[str, dict]:
    """Le strategie dei grandi investitori del catalogo (gruppo "Grandi investitori"), nell'ordine del catalogo."""
    return {n: e for n, e in catalog.items() if e.get("group") == "Grandi investitori" and e.get("kind") == "rules"}


def price_value(key: str, value: float, f: float, row: pd.Series) -> float:
    """Valore della metrica `key` se il prezzo fosse `f` volte quello di oggi (gli altri dati fermi)."""
    kind, k = PRICE_METRICS[key]
    if kind == "mul":
        return value * f ** k
    if kind == "div":
        return value / f
    if kind in ("ev_mul", "ev_div"):
        mcap, ev = _num(row.get("market_cap")), _num(row.get("enterprise_value"))
        if mcap is None or ev is None or ev == 0:
            return float("nan")
        ev_new = mcap * f + (ev - mcap)
        if ev_new <= 0:
            return float("nan")
        return value * ev_new / ev if kind == "ev_mul" else value * ev / ev_new
    if kind == "low":
        return max((1 + value) * f - 1, 0.0)
    return min((1 + value) * f - 1, 0.0)  # high


def _rule_passes(universe: pd.DataFrame, ticker: str, rule: dict, sectors: dict, value: float | None = None) -> bool:
    """La regola, con la stessa funzione del motore, sull'azienda (con `value` al posto del suo valore, se dato)."""
    key = rule["metric"]
    col = universe[[key]].copy() if key in universe else pd.DataFrame({key: np.nan}, index=universe.index)
    if value is not None:
        col.loc[ticker, key] = value
    return bool(apply_rules(col, [rule], sectors).get(ticker, False))


def _price_boundary(universe: pd.DataFrame, ticker: str, rule: dict, sectors: dict, row: pd.Series,
                    lo: float, hi: float) -> tuple[float, str] | None:
    """Per una regola di prezzo: il fattore di prezzo f (prezzo nuovo / prezzo di oggi) oltre il quale cambia esito,
    e da che parte passa ("below": passa per f <= confine, "above": per f >= confine). None se la regola non cambia
    tra `lo` e `hi` volte il prezzo di oggi."""
    key = rule["metric"]
    v = _num(row.get(key))
    if v is None:
        return None
    ok = lambda f: _rule_passes(universe, ticker, rule, sectors, price_value(key, v, f, row))  # noqa: E731
    at_lo, at_hi = ok(lo), ok(hi)
    if at_lo == at_hi:
        return None
    side = "below" if at_lo else "above"
    a, b = math.log(lo), math.log(hi)  # ok(exp(a)) != ok(exp(b)): bisezione in scala logaritmica
    for _ in range(60):
        mid = (a + b) / 2
        if ok(math.exp(mid)) == at_lo:
            a = mid
        else:
            b = mid
    return (math.exp(a) if side == "below" else math.exp(b)), side


def _gap(rule: dict, value: float, rank: float | None, cfg: dict) -> tuple[float | None, bool]:
    """Scarto di una regola mancata (relativo alla soglia, o in punti percentili) e se è entro il "quasi"."""
    thr = float(rule["value"])
    if rule["op"] in RANK_OPS:
        if rank is None:
            return None, False
        g = (1 - thr) - rank  # punti percentili che mancano
        return g, g <= float(cfg["near_miss_rank"])
    if thr == 0:
        g = abs(value - thr)
        return g, g <= float(cfg["near_miss_abs"])
    g = abs(value - thr) / abs(thr)
    return g, g <= float(cfg["near_miss"])


def _value_text(key: str, v) -> str:
    return it_text(format_value(key, v)) if key in METRIC_BY_KEY else fmt(v, "num")


def _rule_text(rule: dict) -> str:
    return it_text(describe_rule(rule))


def _excluded_reason(sheet: dict, sectors: list[str]) -> str:
    """Il motivo dell'esclusione del settore, preso dalla scheda (il pezzo di frase con "esclus..."), più l'elenco
    dei settori in cui cerca."""
    import re

    where = "Cerca solo in: " + ", ".join(sectors) + "."
    m = re.search(r"[^.;()]*[Ee]sclus[^.;()]*", str(sheet.get("translation") or ""))
    if not m:
        return where
    clause = m.group(0).strip()
    return f"{clause[0].upper()}{clause[1:]}. {where}"


def _decide(rules: list[dict], ranking: dict) -> tuple[str, str]:
    """Esito di un investitore, in ordine:
    1. nessuna regola mancata e nessun dato mancante: "La comprerebbe" se rientra nei suoi N titoli, "Le piace, ma ne
       ha di migliori" se no (senza punteggio di classifica: "Dati insufficienti");
    2. una sola regola mancata entro la soglia del "quasi" e nessun dato mancante: "Quasi";
    3. almeno una regola mancata: "Non fa per lui" (i dati mancanti restano segnati);
    4. nessuna regola mancata ma almeno un dato mancante: "Dati insufficienti" (un dato che manca non passa mai)."""
    failed = [r for r in rules if r["status"] == "failed"]
    missing = [r for r in rules if r["status"] == "missing"]
    if not failed and not missing:
        if ranking.get("score") is None and ranking.get("metrics"):
            return "dati", "passa tutte le regole, ma mancano i dati per la sua classifica"
        if ranking.get("selected"):
            return "compra", f"passa tutte le regole ed è {ranking['position']}ª su {ranking['passing']} nella sua " \
                             f"classifica: ne tiene {ranking['positions']}"
        return "piace", f"passa tutte le regole ma è {ranking['position']}ª su {ranking['passing']} nella sua " \
                        f"classifica: ne tiene solo {ranking['positions']}"
    if len(failed) == 1 and not missing and failed[0]["near"]:
        return "quasi", f"manca di poco una sola regola: {failed[0]['text']}"
    if failed:
        n = len(failed)
        tail = f"; e {len(missing)} {'dato mancante' if len(missing) == 1 else 'dati mancanti'}" if missing else ""
        return "no", (f"non rispetta {failed[0]['text']}" if n == 1 else f"non rispetta {n} regole su {len(rules)}") + tail
    return "dati", f"{len(missing)} {'regola' if len(missing) == 1 else 'regole'} senza il dato: un dato mancante non " \
                   "conta mai come regola passata"


def assess_investor(name: str, entry: dict, ticker: str, metrics: pd.DataFrame, sectors: dict[str, str],
                    index_tickers: list[str], cfg: dict | None = None) -> dict:
    """Come le regole dell'investitore `name` leggono l'azienda `ticker` (vedi il docstring del modulo)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    sectors = gics_sectors(sectors)
    d, sheet = entry["definition"], entry.get("investor") or {}
    allowed = list(d.get("universe", {}).get("sectors") or [])
    sector = sectors.get(ticker)
    base = {"name": name, "short": short_name(name),
            "family": next((f for f, ms in FAMILIES.items() if short_name(name) in ms), None),
            "who": sheet.get("who", ""), "original": sheet.get("original", ""), "missing_in_rules": sheet.get("translation", ""),
            "rules": [], "ranking": None}
    if allowed and sector not in allowed:
        return {**base, "outcome": "fuori", "reason": _excluded_reason(sheet, allowed)}

    # universo come nel motore: membri dell'indice nei suoi settori, più l'azienda (se fuori indice: ipotetica)
    members = [t for t in index_tickers if t in metrics.index and (not allowed or sectors.get(t) in allowed)]
    if ticker not in members and ticker in metrics.index:
        members.append(ticker)
    uni = metrics.loc[members]
    row = metrics.loc[ticker] if ticker in metrics.index else pd.Series(dtype=float)
    lo, hi = (float(x) for x in cfg["price_range"])
    price = _num(row.get("price"))

    rules = []
    for rule in d.get("filters", []):
        key, op = rule["metric"], rule["op"]
        v = _num(row.get(key))
        r = {"metric": key, "label": METRIC_BY_KEY[key].label if key in METRIC_BY_KEY else key, "op": op,
             "threshold": float(rule["value"]), "value": v, "text": _rule_text(rule), "value_text": _value_text(key, v),
             "rank": None, "gap": None, "near": False, "need": None, "price_rule": key in PRICE_METRICS}
        if op in RANK_OPS and v is not None and key in uni:
            # posizione 0-1 (1 = migliore) nello stesso universo e con la stessa funzione della regola
            pr = percentile_ranks(uni[[key]], sectors if op == "top_sector" else None)
            r["rank"] = _num(pr[key].get(ticker)) if key in pr else None
        if v is None:
            r["status"] = "missing"
        elif _rule_passes(uni, ticker, rule, sectors):
            r["status"] = "passed"
        else:
            r["status"] = "failed"
            r["gap"], r["near"] = _gap(rule, v, r["rank"], cfg)
        if r["price_rule"] and v is not None and price:
            b = _price_boundary(uni, ticker, rule, sectors, row, lo, hi)
            if b is not None:
                r["bound"] = {"factor": b[0], "side": b[1], "price": price * b[0]}
        if r["status"] == "failed":
            if r.get("bound"):
                r["need"] = {"kind": "price", "price": r["bound"]["price"], "change": r["bound"]["factor"] - 1}
            elif op in RANK_OPS:
                r["need"] = {"kind": "rank", "rank": 1 - r["threshold"]}
            else:
                word = "almeno" if op == ">=" else "al massimo"
                r["need"] = {"kind": "value", "value": r["threshold"],
                             "text": f"servirebbe {word} {_value_text(key, r['threshold'])} (oggi {r['value_text']})"}
        rules.append(r)

    # classifica come nel motore: punteggio sui soli titoli che passano i filtri, primi N
    filters = d.get("filters", [])
    passing = uni.index[apply_rules(uni, filters, sectors)] if filters else uni.index
    positions = int(d["portfolio"]["positions"])
    ranking = {"metrics": [METRIC_BY_KEY[x["metric"]].label for x in d.get("ranking", []) if x["metric"] in METRIC_BY_KEY],
               "positions": positions, "passing": int(len(passing)), "score": None, "position": None, "selected": False}
    if ticker in passing:
        score = rank_score(uni.loc[passing], d.get("ranking", []))
        if score.notna().any():
            ranked = score.dropna().sort_values(ascending=False, kind="stable")
            ranking["score"] = _num(score.get(ticker))
        else:  # nessuna classifica: le più grandi, come il motore
            size = uni["market_cap"] if "market_cap" in uni else pd.Series(np.nan, index=uni.index)
            ranked = size.reindex(passing).fillna(0).sort_values(ascending=False, kind="stable")
            ranking["score"] = None
            ranking["metrics"] = []
        order = list(ranked.index)
        if ticker in order:
            ranking["position"] = order.index(ticker) + 1
            ranking["selected"] = ranking["position"] <= positions
        ranking["passing"] = int(len(order))
    outcome, reason = _decide(rules, ranking)
    return {**base, "outcome": outcome, "reason": reason, "rules": rules, "ranking": ranking,
            "outside_index": ticker not in index_tickers}


def _price_to_pass_all(m: dict, price: float | None) -> dict | None:
    """Prezzo più vicino a oggi a cui TUTTE le regole di un investitore passerebbero, se le uniche mancate sono di
    prezzo (e nessun dato manca): intersezione dei confini di tutte le sue regole di prezzo."""
    if not price or m["outcome"] in ("fuori", "compra", "piace") or not m["rules"]:
        return None
    failed = [r for r in m["rules"] if r["status"] == "failed"]
    if not failed or any(r["status"] == "missing" for r in m["rules"]) or any(not r.get("bound") for r in failed):
        return None
    lo, hi = 0.0, math.inf
    for r in m["rules"]:
        b = r.get("bound")
        if b is None:
            if r["price_rule"] and r["status"] == "failed":
                return None
            continue
        if b["side"] == "below":
            hi = min(hi, b["factor"])
        else:
            lo = max(lo, b["factor"])
    if lo > hi:
        return None
    f = hi if hi < 1 else lo  # il più vicino a oggi
    return {"price": price * f, "change": f - 1}


def summarize(members: list[dict], price: float | None, currency: str = "") -> dict:
    """Sintesi a regole: conteggi, disaccordi tra famiglie di stile, ostacolo più comune, prezzo più alto a cui almeno
    un investitore in più passerebbe tutte le regole."""
    counts = {k: sum(m["outcome"] == k for m in members) for k in OUTCOMES}
    names = {k: [m["short"] for m in members if m["outcome"] == k] for k in OUTCOMES}

    families = []
    for fam, shorts in FAMILIES.items():
        ms = [m for m in members if m["short"] in shorts and m["outcome"] in ("compra", "piace", "quasi", "no")]
        pos = [m["short"] for m in ms if m["outcome"] in POSITIVE]
        neg = [m["short"] for m in ms if m["outcome"] == "no"]
        stance = None if not ms else "favorevole" if len(pos) * 2 > len(ms) else \
            "contraria" if not pos and neg else "divisa"
        families.append({"family": fam, "stance": stance, "positive": pos, "negative": neg,
                         "members": [m["short"] for m in members if m["short"] in shorts]})
    fav = [f for f in families if f["stance"] == "favorevole"]
    con = [f for f in families if f["stance"] == "contraria"]
    disagreement = None
    if fav and con:
        disagreement = "; ".join(
            [f"piace alla {f['family']} ({', '.join(f['positive'])})" for f in fav]
            + [f"non piace al {f['family']} ({', '.join(f['negative'])})" if f["family"].startswith("value")
               else f"non piace alla {f['family']} ({', '.join(f['negative'])})" for f in con]) + "."

    obstacles: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for m in members:
        for r in m["rules"]:
            if r["status"] == "failed":
                obstacles.setdefault(r["metric"], []).append(m["short"])
                labels[r["metric"]] = r["label"]
    obstacle = None
    if obstacles:
        key = sorted(obstacles, key=lambda k: (-len(obstacles[k]), k))[0]
        obstacle = {"metric": key, "label": labels[key], "count": len(obstacles[key]), "investors": obstacles[key]}

    candidates = [(p, m["short"]) for m in members if (p := _price_to_pass_all(m, price))]
    next_price = None
    if candidates:
        best = max(c[0]["price"] for c in candidates)
        who = [s for p, s in candidates if abs(p["price"] - best) <= 1e-9 * max(1.0, best)]
        next_price = {"price": best, "change": best / price - 1 if price else None, "investors": who}

    lines = []
    total = sum(counts[k] for k in ("compra", "piace", "quasi", "no"))
    def n(k: str, one: str, many: str) -> str:
        return f"{counts[k]} {one if counts[k] == 1 else many}"

    lines.append(f"Su {len(members)} investitori: {n('compra', 'la comprerebbe', 'la comprerebbero')}, "
                 f"{n('piace', 'la apprezza ma ne ha di migliori', 'la apprezzano ma ne hanno di migliori')}, "
                 f"{counts['quasi']} quasi, {counts['no']} no"
                 + (f"; {counts['fuori']} fuori dal loro campo" if counts["fuori"] else "")
                 + (f"; {counts['dati']} senza dati sufficienti" if counts["dati"] else "") + ".")
    if disagreement:
        lines.append(f"Disaccordo tra stili: {disagreement}")
    if obstacle and obstacle["count"] > 1:
        lines.append(f"L'ostacolo più comune è {obstacle['label']}: non passa la regola di {obstacle['count']} "
                     f"investitori ({', '.join(obstacle['investors'])}).")
    if next_price:
        lines.append(f"A {fmt(next_price['price'], 'price', currency=currency or None)} "
                     f"({fmt(next_price['change'], 'pct', signed=True)}) passerebbe tutte le regole di "
                     f"{', '.join(next_price['investors'])}.")
    return {"counts": counts, "names": names, "families": families, "disagreement": disagreement,
            "obstacle": obstacle, "next_price": next_price, "lines": lines, "evaluated": total}


def assess_panel(ticker: str, metrics: pd.DataFrame, sectors: dict[str, str], index_tickers: list[str],
                 catalog: dict, cfg: dict | None = None, currency: str = "") -> dict:
    """Il tavolo completo: un esito per investitore (ordine del catalogo) e la sintesi."""
    raw_sector = sectors.get(ticker)
    sectors = gics_sectors(sectors)
    members = [assess_investor(n, e, ticker, metrics, sectors, index_tickers, cfg)
               for n, e in investor_catalog(catalog).items()]
    price = _num(metrics.at[ticker, "price"]) if ticker in metrics.index and "price" in metrics else None
    return {"ticker": ticker, "sector": raw_sector, "price": price, "outside_index": ticker not in index_tickers,
            "members": members, "summary": summarize(members, price, currency), "note": NOTE,
            "outcomes": {k: {"label": v[0], "tone": v[1]} for k, v in OUTCOMES.items()}}


def _pct_if_inserted(member_values: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Posizione 0-1 che avrebbe ciascun valore `x` se fosse aggiunto, da solo, ai valori dei membri (già orientati:
    più alto = migliore): la stessa di `rank(pct=True)` di pandas (media dei pari merito, NaN esclusi)."""
    v = np.sort(member_values[np.isfinite(member_values)])
    less = np.searchsorted(v, x, side="left")
    eq = np.searchsorted(v, x, side="right") - less
    out = (less + (eq + 2) / 2) / (len(v) + 1)
    return np.where(np.isfinite(x), out, np.nan)


def liked_by(metrics: pd.DataFrame, sectors: dict[str, str], index_tickers: list[str], catalog: dict,
             tickers: list[str] | None = None) -> pd.Series:
    """A quanti investitori del tavolo "piace": l'azienda passa TUTTE le sue regole (esiti "La comprerebbe" e "Le
    piace, ma ne ha di migliori"; la classifica non conta). Metrica derivata di Esplora; un dato mancante non passa
    mai. Non usabile nelle strategie (JUDGMENT_KEYS). Stesso risultato del tavolo della scheda: i membri dell'indice
    contro l'indice, ogni azienda fuori indice aggiunta da sola all'indice (vettoriale, senza un ciclo per azienda)."""
    sectors = gics_sectors(sectors)
    tickers = list(metrics.index) if tickers is None else [t for t in tickers if t in metrics.index]
    out = pd.Series(0, index=tickers, dtype=int)
    for _, e in investor_catalog(catalog).items():
        d = e["definition"]
        allowed = set(d.get("universe", {}).get("sectors") or [])
        members = [t for t in index_tickers if t in metrics.index and (not allowed or sectors.get(t) in allowed)]
        member_set = set(members)
        extras = [t for t in tickers if t not in member_set and (not allowed or sectors.get(t) in allowed)]
        uni = metrics.loc[members]
        filters = d.get("filters", [])
        ok_members = apply_rules(uni, filters, sectors) if filters else pd.Series(True, index=uni.index)
        passed = [t for t in ok_members.index[ok_members] if t in out.index]
        if extras:
            ex = metrics.loc[extras]
            ok = pd.Series(True, index=extras)
            plain = [r for r in filters if r["op"] not in RANK_OPS]
            if plain:
                ok &= apply_rules(ex, plain, sectors)
            for r in (r for r in filters if r["op"] in RANK_OPS):
                key = r["metric"]
                sign = -1.0 if METRIC_BY_KEY[key].better == "low" else 1.0
                x = (ex[key].astype(float) * sign).to_numpy() if key in ex else np.full(len(ex), np.nan)
                col = uni[key].astype(float) * sign if key in uni else pd.Series(np.nan, index=uni.index)
                if r["op"] == "top_index":
                    pct = _pct_if_inserted(col.to_numpy(), x)
                else:  # top_sector: contro i membri dello stesso settore
                    pct = np.array([_pct_if_inserted(col[[sectors.get(m) == sectors.get(t) for m in col.index]]
                                                     .to_numpy(), np.array([xi]))[0] for t, xi in zip(extras, x)])
                ok &= pd.Series(np.nan_to_num(pct, nan=-1.0) >= 1 - float(r["value"]), index=extras)
            passed += [t for t in ok.index[ok] if t in out.index]
        out = out.add(pd.Series(1, index=passed, dtype=int), fill_value=0).astype(int)
    return out
