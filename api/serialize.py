"""Conversione in JSON dei risultati del motore: DataFrame, Series, numpy, date, dataclass.

Il JSON non ammette NaN né infinito (Starlette serializza con allow_nan=False): diventano null. I numeri restano
grezzi (la formattazione italiana la fa il client, con le regole di ui/components.fmt).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd


def jsonable(obj: Any) -> Any:
    """Converte ricorsivamente in tipi JSON (dict, list, str, int, float, bool, None)."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, dt.datetime, dt.date)):
        ts = pd.Timestamp(obj)
        if pd.isna(ts):
            return None
        return ts.date().isoformat() if ts == ts.normalize() else ts.isoformat()
    if isinstance(obj, pd.DataFrame):
        df = obj.reset_index() if not isinstance(obj.index, pd.RangeIndex) else obj
        return [jsonable(r) for r in df.to_dict("records")]
    if isinstance(obj, pd.Series):
        return {str(jsonable(k)): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, dict):
        return {str(jsonable(k)) if not isinstance(k, str) else k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    try:  # valori scalari di pandas (NA) e simili
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)
