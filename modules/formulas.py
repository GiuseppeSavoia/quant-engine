"""
Formule delle metriche personali, es. "fcf_yield - 0.02 * net_debt_ebitda" oppure "roic - 0.08".

Si leggono con l'albero sintattico di Python ma si esegue SOLO un sottoinsieme sicuro: numeri, nomi delle
metriche del catalogo (più `price`), + - * / **, parentesi e le funzioni min, max, abs, log, sqrt. Niente
attributi, indici, chiamate ad altro o confronti: una formula non può eseguire codice.
Il calcolo è vettoriale (una colonna per metrica) e un risultato infinito diventa vuoto.
"""
from __future__ import annotations

import ast

import numpy as np
import pandas as pd

FUNCTIONS = {
    "min": lambda a, b: np.minimum(a, b),
    "max": lambda a, b: np.maximum(a, b),
    "abs": np.abs,
    "log": lambda a: np.log(a.where(a > 0) if isinstance(a, pd.Series) else (a if a > 0 else np.nan)),
    "sqrt": lambda a: np.sqrt(a.where(a >= 0) if isinstance(a, pd.Series) else (a if a >= 0 else np.nan)),
}
ARITY = {"min": 2, "max": 2, "abs": 1, "log": 1, "sqrt": 1}
BINARY = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}


class FormulaError(ValueError):
    pass


def names_in(formula: str) -> set[str]:
    """Le metriche usate dalla formula (dopo averla validata)."""
    return {n.id for n in ast.walk(parse(formula)) if isinstance(n, ast.Name)}


def parse(formula: str, allowed: set[str] | None = None) -> ast.Expression:
    """Albero della formula; errore in italiano se contiene qualcosa di non ammesso."""
    if not formula or not formula.strip():
        raise FormulaError("La formula è vuota.")
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Formula non valida: {exc.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)) or type(node) in BINARY:
            continue
        if isinstance(node, ast.BinOp):
            if type(node.op) not in BINARY:
                raise FormulaError("Operazioni ammesse: + - * / **")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                raise FormulaError("Operatore non ammesso.")
        elif isinstance(node, (ast.USub, ast.UAdd)):
            continue
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise FormulaError("Sono ammessi solo numeri.")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS:
                continue
            if allowed is not None and node.id not in allowed:
                raise FormulaError(f"Metrica sconosciuta: {node.id}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise FormulaError("Funzioni ammesse: " + ", ".join(FUNCTIONS))
            if node.keywords or len(node.args) != ARITY[node.func.id]:
                raise FormulaError(f"{node.func.id} vuole {ARITY[node.func.id]} argomenti.")
        else:
            raise FormulaError(f"Elemento non ammesso nella formula: {type(node).__name__}")
    return tree


def evaluate(formula: str, frame: pd.DataFrame, allowed: set[str] | None = None) -> pd.Series:
    """Valore della formula per ogni riga di `frame` (colonne = metriche)."""
    tree = parse(formula, allowed)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in frame:
                return pd.Series(np.nan, index=frame.index)
            return frame[node.id].astype(float)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            with np.errstate(all="ignore"):
                return BINARY[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Call):
            return FUNCTIONS[node.func.id](*[ev(a) for a in node.args])
        raise FormulaError(f"Elemento non ammesso: {type(node).__name__}")  # già escluso da parse

    out = ev(tree)
    if not isinstance(out, pd.Series):  # formula senza metriche: un numero solo
        out = pd.Series(float(out), index=frame.index)
    return out.astype(float).replace([np.inf, -np.inf], np.nan)
