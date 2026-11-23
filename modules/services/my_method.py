"""Il mio metodo e i dati a mano: metriche personali (formule), checklist qualitativa, profilo dell'utente, bilanci
inseriti a mano."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import method, strategies
from modules import metrics as metrics_mod
from modules.services.base import Service


class MethodService(Service):
    # ------------------------------------------------------------------ metriche personali
    def save_custom_metric(self, d: dict) -> None:
        metrics_mod.validate_custom(d)
        self.db.save_custom_metric(d)
        self.ctx._load_custom_metrics()
        self.ctx._metrics = None  # si ricalcola con la nuova formula

    def delete_custom_metric(self, key: str) -> list[str]:
        """Elimina la metrica; restituisce le strategie dell'utente che la usavano (da sistemare)."""
        used = [n for n, d in self.ctx.user_strategies().items() if key in strategies.needed_metrics(d)]
        self.db.delete_custom_metric(key)
        self.ctx._load_custom_metrics()
        self.ctx._metrics = None
        return used

    def preview_formula(self, formula: str) -> pd.Series:
        """La formula calcolata oggi su tutte le aziende (per vederla prima di salvarla)."""
        from modules.formulas import evaluate

        m = self.ctx.metrics()
        return evaluate(formula, m[[c for c in m.columns if c in metrics_mod.BASE_KEYS]], metrics_mod.BASE_KEYS)

    # ------------------------------------------------------------------ checklist e profilo
    def checklist_items(self) -> pd.DataFrame:
        return self.db.load_checklist_items(method.DEFAULT_CHECKLIST)

    def checklist(self, ticker: str) -> dict[int, float]:
        sc = self.db.load_checklist_scores(ticker)
        return {int(r.item_id): float(r.score) for r in sc.itertuples()}

    def save_checklist(self, ticker: str, scores: dict[int, float | None]) -> None:
        self.db.save_checklist_scores(ticker, scores)

    def checklist_scores(self) -> pd.Series:
        """Voto (1-5) di ogni azienda valutata, con le domande e i pesi attuali."""
        items = self.ctx.checklist_items()
        sc = self.db.load_checklist_scores()
        out = {}
        for t, g in sc.groupby("ticker"):
            v = method.checklist_score(dict(zip(g["item_id"].astype(int), g["score"])), items)
            if v is not None:
                out[t] = v
        return pd.Series(out, dtype=float)

    def profile(self) -> dict:
        return {**method.PROFILE_DEFAULTS, **self.db.load_profile()}

    def save_profile(self, profile: dict) -> None:
        self.db.save_profile({k: v for k, v in profile.items() if k in method.PROFILE_DEFAULTS})

    # ------------------------------------------------------------------ pagina "Il mio metodo" (interfaccia web)
    def method_page(self) -> dict:
        """Profilo con le scelte possibili, metriche personali, nomi delle metriche da usare nelle formule, formati,
        domande della checklist."""
        items = self.ctx.checklist_items()
        return {
            "profile": self.ctx.profile(), "styles": method.STYLES, "horizons": method.HORIZONS,
            "custom_metrics": self.db.load_custom_metrics(),
            "formats": [{"value": k, "label": v} for k, v in metrics_mod.CUSTOM_FORMATS.items()],
            "formula_names": [{"key": m.key, "label": m.label, "group": m.group} for m in metrics_mod.BASE_METRICS]
            + [{"key": "price", "label": "Prezzo", "group": "Prezzo"}],
            "functions": ["min(a, b)", "max(a, b)", "abs(a)", "log(a)", "sqrt(a)"],
            "checklist": [{"id": int(r["id"]), "question": r["question"], "help": r["help"] or "",
                           "weight": float(r["weight"])} for _, r in items.iterrows()] if not items.empty else [],
        }

    def formula_preview(self, formula: str, better: str | None = None, top: int = 10) -> dict:
        """Una formula calcolata oggi: per quante aziende si può, mediana, 10° e 90° percentile e (se ha un verso)
        le migliori. ValueError (messaggio in italiano) se la formula non va."""
        values = self.ctx.preview_formula(formula)
        ok = values.dropna()
        ok = ok[np.isfinite(ok)]
        out = {"computable": int(len(ok)), "total": int(len(values)), "median": None, "p10": None, "p90": None, "best": []}
        if not ok.empty:
            out.update(median=float(ok.median()), p10=float(ok.quantile(0.1)), p90=float(ok.quantile(0.9)))
            if better in ("high", "low"):
                best = ok.sort_values(ascending=better == "low").head(top)
                out["best"] = [{"ticker": t, "name": self.ctx.info(t).get("name") or "", "value": float(v)}
                               for t, v in best.items()]
        return out

    def create_custom_metric(self, label: str, formula: str, fmt: str = "num", better: str | None = None,
                             description: str = "") -> dict:
        """Nuova metrica personale dal nome (la chiave si ricava dal nome). ValueError se nome, formula o formato
        non vanno."""
        label, formula = (label or "").strip(), (formula or "").strip()
        if not label or not formula:
            raise ValueError("Servono il nome e la formula.")
        if fmt not in metrics_mod.CUSTOM_FORMATS:
            raise ValueError(f"Formato non previsto: {fmt}")
        if better not in ("high", "low", None):
            raise ValueError("Il verso è «più alta», «più bassa» o nessuno.")
        definition = {"key": metrics_mod.custom_key(label), "label": label, "formula": formula, "fmt": fmt,
                      "better": better, "description": (description or "").strip()}
        self.ctx.save_custom_metric(definition)
        return definition

    def save_checklist_question(self, question: str, help_text: str = "", weight: float = 1.0,
                                item_id: int | None = None) -> None:
        question = (question or "").strip()
        if not question:
            raise ValueError("Scrivi la domanda.")
        if not 0 <= float(weight) <= 3:
            raise ValueError("Il peso va da 0 a 3.")
        self.db.save_checklist_item(question, (help_text or "").strip(), float(weight), item_id=item_id)

    def remove_checklist_question(self, item_id: int) -> None:
        """Toglie la domanda (i voti già dati restano nel database, ma non contano più)."""
        self.db.deactivate_checklist_item(int(item_id))

    # ------------------------------------------------------------------ dati inseriti a mano
    def manual_table(self, ticker: str, kind: str = "annual", amount_unit: float = 1e6, share_unit: float = 1e6,
                     periods: int = 8) -> pd.DataFrame:
        """Tabella da modificare: righe = voci (manual_data.ITEMS), colonne = date di chiusura (AAAA-MM-GG) degli
        ultimi `periods` periodi, valori attuali (SEC o già inseriti) nelle unità scelte."""
        from modules.manual_data import ITEMS

        st = self.data.statements(ticker) if kind == "annual" else self.data.ttm_statements(ticker)
        st = st.tail(periods)
        keys = [k for k, _, _ in ITEMS]
        out = pd.DataFrame(index=keys, columns=[d.strftime("%Y-%m-%d") for d in st.index], dtype=float)
        for d in st.index:
            for k in keys:
                if k in st:
                    v = st.at[d, k]
                    out.at[k, d.strftime("%Y-%m-%d")] = v / (share_unit if k == "shares" else amount_unit) \
                        if pd.notna(v) else np.nan
        return out

    def save_manual(self, ticker: str, changes: dict, kind: str, amount_unit: float, share_unit: float,
                    days_after_close: int | None = None) -> int:
        """Salva le celle modificate ({periodo: {voce: valore}}). `days_after_close` = None: valgono da oggi;
        altrimenti dalla chiusura + quei giorni (per correggere la storia). Restituisce quante righe."""
        from modules.manual_data import build_rows

        frames = []
        for period, values in changes.items():
            filed = None if days_after_close is None else \
                min(pd.Timestamp(period) + pd.Timedelta(days=int(days_after_close)), pd.Timestamp.now().normalize())
            frames.append(build_rows(ticker, period, values, kind, filed, amount_unit, share_unit))
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not rows.empty:
            self.db.upsert_financials(rows)
        return len(rows)
