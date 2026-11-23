"""API HTTP di QuantEngine (fase 1 di docs/piano_frontend.md): la scheda azienda per l'interfaccia nuova.

Regole:
- qui niente calcoli: solo instradamento, validazione dell'input e conversione in JSON; i numeri vengono dai servizi
  (`DashboardData` -> modules/services), gli stessi che usa la webapp Streamlit;
- niente Streamlit né `ui/` (test in tests/test_api.py): il tema e le preferenze sono per utente, non del processo;
- valori grezzi (JSON), la formattazione italiana la fa il client;
- finché non c'è il login (fase 4) l'API ascolta solo su 127.0.0.1, senza CORS, e non ha funzioni che vanno in rete o
  lanciano l'aggiornamento dei dati. `current_user` è il punto in cui arriverà l'utente vero.
- endpoint sincroni (`def`): girano in un gruppo di thread, il motore è tutto bloccante.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator

from api.serialize import jsonable
from modules.app_context import ContextCache
from modules.jobs import JobRunner
from modules.dashboard_data import DashboardData

API_PREFIX = "/api/v1"
LOCAL_USER = "locale"
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"  # interfaccia compilata (cd web && npm run build)


# ---------------------------------------------------------------- dipendenze
def current_user() -> str:
    """Utente della richiesta. Oggi uno solo (app locale); con il login (fase 4) arriverà dal token."""
    return LOCAL_USER


def context(request: Request) -> DashboardData:
    try:
        return request.app.state.contexts.get()
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


Ctx = Annotated[DashboardData, Depends(context)]
User = Annotated[str, Depends(current_user)]


def resolve_ticker(d: DashboardData, ticker: str) -> str:
    """Ticker come lo conosce il motore, senza badare a maiuscole e minuscole; 404 se non c'è."""
    known = {t.upper(): t for t in d.tickers}
    t = known.get(str(ticker).upper())
    if t is None:
        raise HTTPException(404, f"Azienda sconosciuta: {ticker}")
    return t


# ---------------------------------------------------------------- modelli dell'input
class ManualIn(BaseModel):
    changes: dict[str, dict[str, float | None]]
    kind: Literal["annual", "ttm"] = "annual"
    unit: str = "milioni"
    share_unit: str = "milioni"
    days_after_close: int | None = Field(None, ge=0, le=365)


class ManualDeleteIn(BaseModel):
    rows: list[dict[str, str]] = Field(..., min_length=1, max_length=500)


class CustomCompanyIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    name: str = Field("", max_length=200)
    sector: str | None = Field(None, max_length=100)
    currency: str = "USD"


class LabIn(BaseModel):
    values: dict[str, object] = Field(default_factory=dict)
    period: Literal["all", "first", "second"] = "all"
    name: str = Field("", max_length=200)
    with_research: bool = False


class ResearchIn(BaseModel):
    fields: dict[str, str | None] = Field(default_factory=dict)
    scores: dict[int, float | None] | None = None
    thesis: str | None = Field(None, max_length=20000)


class EstimatesCsvIn(BaseModel):
    csv: str = Field(..., min_length=1, max_length=2_000_000)


class PipelineIn(BaseModel):
    stage: Literal["idea", "studio", "pronta", "portafoglio", "scartata"]
    note: str | None = Field(None, max_length=1000)


class PriceTargetIn(BaseModel):
    buy_below: float | None = Field(None, gt=0, description="Avviso quando il prezzo scende sotto")
    sell_above: float | None = Field(None, gt=0, description="Avviso quando il prezzo sale sopra")
    note: str | None = Field(None, max_length=500)


class LeversIn(BaseModel):  # noqa: D101
    """Leve per scenario (anche parziali) e probabilità; i nomi degli scenari sono quelli della partenza."""
    levers: dict[str, dict[str, float]] = Field(default_factory=dict)
    probabilities: dict[str, float] | None = None

    @model_validator(mode="after")
    def _finite(self):
        import math

        values = [v for lv in self.levers.values() for v in lv.values()] + list((self.probabilities or {}).values())
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Leve e probabilità devono essere numeri finiti")
        return self


class RuleIn(BaseModel):
    metric: str
    op: str
    value: float


class ExploreIn(BaseModel):
    universe: str = "S&P 500"
    rules: list[RuleIn] = Field(default_factory=list, max_length=30)
    sectors: list[str] = Field(default_factory=list)
    verdicts: list[str] = Field(default_factory=list)
    min_scores: dict[str, float] = Field(default_factory=dict)
    text: str = Field("", max_length=60)
    sort: str = "auto"
    columns: list[str] | None = Field(None, max_length=60)


class ScreenIn(BaseModel):
    rules: list[RuleIn] = Field(default_factory=list, max_length=30)
    sectors: list[str] = Field(default_factory=list)
    verdicts: list[str] = Field(default_factory=list)
    min_scores: dict[str, float] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    ticker: str
    action: str
    reason: str = Field(..., max_length=3000)
    change_mind: str | None = Field(None, max_length=2000)
    price: float | None = None
    review_date: str | None = Field(None, description="AAAA-MM-GG; vuota = fra 6 mesi")


class ReviewIn(BaseModel):
    verdict: str
    note: str = Field("", max_length=3000)


class ProfileIn(BaseModel):
    styles: list[str] = Field(default_factory=list)
    horizon: str = "3-5 anni"
    positions: int = Field(20, ge=1, le=200)
    principles: str = Field("", max_length=5000)
    red_flags: str = Field("", max_length=5000)
    sell_rules: str = Field("", max_length=5000)


class FormulaIn(BaseModel):
    formula: str = Field(..., max_length=500)
    better: Literal["high", "low"] | None = None


class CustomMetricIn(BaseModel):
    label: str = Field(..., max_length=80)
    formula: str = Field(..., max_length=500)
    fmt: str = "num"
    better: Literal["high", "low"] | None = None
    description: str = Field("", max_length=500)


class QuestionIn(BaseModel):
    question: str = Field(..., max_length=300)
    help: str = Field("", max_length=1000)
    weight: float = 1.0


class TransactionIn(BaseModel):
    ticker: str = Field(..., max_length=20, description="Simbolo Yahoo (es. AAPL, ENI.MI, VWCE.DE)")
    kind: Literal["BUY", "SELL", "DIV"]
    date: str = Field(..., description="AAAA-MM-GG")
    quantity: float
    price: float = Field(..., description="Prezzo (o dividendo per azione) nella valuta del titolo")
    currency: str | None = None
    fx: float | None = Field(None, gt=0, description="Valuta per 1 euro; vuoto = chiusura del giorno")
    fees: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=500)
    reason: str | None = Field(None, max_length=3000, description="Il perché: va nel diario delle decisioni")


class StrategyRunIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class StrategyIn(BaseModel):
    definition: dict
    editing: str | None = Field(None, max_length=200)


class XAccountIn(BaseModel):
    account: str = Field(..., min_length=1, max_length=200)


class SeenIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=5000)


class SaveValuationIn(LeversIn):
    story: str = Field("", max_length=5000, description="La storia dietro i numeri")
    note: str = Field("", max_length=2000)


# ---------------------------------------------------------------- applicazione
def create_app(config_path: str | None = None, demo: bool = False, preload: bool = True,
               web_dist: Path | None = WEB_DIST, jobs: JobRunner | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if preload:  # i dati si caricano all'avvio, non alla prima richiesta
            try:
                app.state.contexts.get()
            except FileNotFoundError:
                pass  # /health lo dirà; le altre richieste rispondono 503
        yield
        app.state.jobs.shutdown()

    app = FastAPI(title="QuantEngine API", version="1", lifespan=lifespan,
                  description="Dati della scheda azienda per l'interfaccia di QuantEngine. Valori grezzi, testi in "
                              "italiano. Il giudizio è una sintesi di criteri fissi, non una raccomandazione.")
    app.state.contexts = ContextCache(config_path, demo)
    app.state.demo = demo
    app.state.jobs = jobs or JobRunner()  # backtest in sottofondo, uno alla volta

    @app.get(f"{API_PREFIX}/health")
    def health(request: Request) -> dict:
        cache = request.app.state.contexts
        return {"ok": cache.db_path.exists(), "demo": demo, "database": cache.db_path.name}

    @app.get(f"{API_PREFIX}/companies")
    def companies(d: Ctx, q: Annotated[str, Query(max_length=60)] = "", limit: Annotated[int, Query(ge=1, le=200)] = 20):
        """Ricerca per ticker o nome (prima i ticker che iniziano con il testo)."""
        text = q.strip().upper()
        rows = []
        for t in d.tickers:
            name = d.info(t).get("name") or ""
            if not text or t.upper().startswith(text) or text in name.upper():
                rows.append({"ticker": t, "name": name, "sector": d.info(t).get("sector"),
                             "universe": d.universe_of(t), "rank": 0 if t.upper().startswith(text) else 1})
        rows.sort(key=lambda r: (r["rank"], r["ticker"]))
        return {"results": [{k: v for k, v in r.items() if k != "rank"} for r in rows[:limit]], "total": len(rows)}

    @app.get(f"{API_PREFIX}/companies/{{ticker}}")
    def company(ticker: str, d: Ctx, user: User):  # noqa: ARG001 - user: dati per utente dalla fase 4
        """Testata, giudizio con le prove, cosa è cambiato, avvisi attivi."""
        t = resolve_ticker(d, ticker)
        started = time.perf_counter()
        try:
            v = d.company_verdict(t)
        except Exception as exc:  # noqa: BLE001 - la scheda resta usabile senza giudizio
            v, verdict_error = None, str(exc)
        else:
            verdict_error = None
        header = d.company_header(t, v.profile if v is not None else "general")
        note, valuation = d.thesis_inputs(t)
        try:
            changes = d.what_changed(t, header["sector"], header["currency"], note=note, valuation=valuation)
        except Exception as exc:  # noqa: BLE001
            changes = {"error": str(exc)}
        verdict = None
        if v is not None:
            verdict = {**jsonable(v), "profile_label": v.profile_label}
        return jsonable({"header": header, "verdict": verdict, "verdict_error": verdict_error,
                         "what_changed": changes, "alerts": d.active_alerts(t),
                         "elapsed_ms": round((time.perf_counter() - started) * 1000)})

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/financials")
    def financials(ticker: str, d: Ctx, view: Literal["annual", "quarterly_ttm", "quarterly_single"] = "annual"):
        """Bilanci pronti da mostrare: periodi con link al deposito, serie dei grafici, tabella con segni sulle fonti
        di riserva. `view`: esercizi, ultimi 12 mesi a ogni trimestre, trimestri singoli."""
        t = resolve_ticker(d, ticker)
        return jsonable(d.financial_statements(t, view))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/valuation")
    def valuation(ticker: str, d: Ctx):
        """Valutazione a leve con le ipotesi di partenza (o quelle dell'ultima valutazione salvata)."""
        t = resolve_ticker(d, ticker)
        out = d.lever_valuation(t)
        if out is None:
            raise HTTPException(422, "Valutazione non impostabile: mancano ricavi, azioni o prezzi.")
        return jsonable(out)

    @app.post(f"{API_PREFIX}/companies/{{ticker}}/valuation")
    def valuation_with_levers(ticker: str, body: LeversIn, d: Ctx):
        """Stessa valutazione con leve e probabilità scelte dal client (non salva nulla)."""
        t = resolve_ticker(d, ticker)
        try:
            out = d.lever_valuation(t, body.levers, body.probabilities)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if out is None:
            raise HTTPException(422, "Valutazione non impostabile: mancano ricavi, azioni o prezzi.")
        return jsonable(out)

    @app.post(f"{API_PREFIX}/companies/{{ticker}}/valuation/save")
    def save_valuation(ticker: str, body: SaveValuationIn, d: Ctx, user: User):  # noqa: ARG001
        """Salva la valutazione nel diario e aggiorna il tuo fair value (Le mie aziende, avvisi, testata)."""
        t = resolve_ticker(d, ticker)
        try:
            entry = d.save_lever_valuation(t, body.levers, body.probabilities, body.story, body.note)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return jsonable({"ticker": t, "saved": entry, "fair_value": d.note(t).get("fair_value")})

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/price")
    def price(ticker: str, d: Ctx, period: str = "1A", style: str = "Auto", bench: bool = False):
        """Grafico del prezzo: barre del periodo, medie, volumi, riferimenti dell'utente, 10-K, operazioni del
        backtest, sintesi; con `bench` anche il confronto con il benchmark in base 100."""
        try:
            return jsonable(d.price_chart(resolve_ticker(d, ticker), period, style, bench))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/peers")
    def peers(ticker: str, d: Ctx):
        """Concorrenti: le più grandi del gruppo e l'azienda contro le mediane del gruppo."""
        return jsonable(d.company_peers(resolve_ticker(d, ticker)))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/documents")
    def documents(ticker: str, d: Ctx):
        """Notizie che citano l'azienda (copia salvata, niente rete) e documenti depositati alla SEC."""
        return jsonable(d.company_documents(resolve_ticker(d, ticker)))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/estimates")
    def estimates(ticker: str, d: Ctx):
        """Stime degli analisti già salvate (niente scarico da internet da qui). Solo informazione."""
        t = resolve_ticker(d, ticker)
        return jsonable({"ticker": t, **d.estimates_overview(t, online=False)})

    # ------------------------------------------------------------ scritture dell'utente
    @app.get(f"{API_PREFIX}/watchlist")
    def watchlist(d: Ctx, user: User):  # noqa: ARG001
        return {"tickers": d.watchlist()}

    @app.get(f"{API_PREFIX}/method")
    def method_page(d: Ctx, user: User):  # noqa: ARG001
        """Il mio metodo: profilo, metriche personali (formule), domande della checklist."""
        return jsonable(d.method_page())

    @app.put(f"{API_PREFIX}/method/profile")
    def save_profile(body: ProfileIn, d: Ctx, user: User):  # noqa: ARG001
        from modules import method as method_mod

        bad = [x for x in body.styles if x not in method_mod.STYLES]
        if bad or body.horizon not in method_mod.HORIZONS:
            raise HTTPException(422, "Stile o orizzonte non previsti.")
        d.save_profile(body.model_dump())
        return jsonable(d.profile())

    @app.post(f"{API_PREFIX}/method/metrics/preview")
    def formula_preview(body: FormulaIn, d: Ctx):
        try:
            return jsonable(d.formula_preview(body.formula, body.better))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post(f"{API_PREFIX}/method/metrics")
    def create_metric(body: CustomMetricIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            return jsonable(d.create_custom_metric(body.label, body.formula, body.fmt, body.better, body.description))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete(f"{API_PREFIX}/method/metrics/{{key}}")
    def delete_metric(key: str, d: Ctx, user: User):  # noqa: ARG001
        """Elimina una metrica personale; `used_by` = le tue strategie che la usavano (da sistemare)."""
        return {"deleted": key, "used_by": d.delete_custom_metric(key)}

    @app.post(f"{API_PREFIX}/method/checklist")
    def add_question(body: QuestionIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            d.save_checklist_question(body.question, body.help, body.weight)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.put(f"{API_PREFIX}/method/checklist/{{item_id}}")
    def edit_question(item_id: int, body: QuestionIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            d.save_checklist_question(body.question, body.help, body.weight, item_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True}

    @app.delete(f"{API_PREFIX}/method/checklist/{{item_id}}")
    def remove_question(item_id: int, d: Ctx, user: User):  # noqa: ARG001
        d.remove_checklist_question(item_id)
        return {"removed": item_id}

    @app.get(f"{API_PREFIX}/portfolio")
    def portfolio(d: Ctx, user: User):  # noqa: ARG001
        """Portafoglio con i soli dati salvati: riepilogo in euro, posizioni, composizione, andamento contro il
        benchmark, operazioni, dividendi e tasse stimate (stima, non consulenza fiscale)."""
        return jsonable(d.portfolio_page())

    @app.post(f"{API_PREFIX}/portfolio/transactions")
    def add_transaction(body: TransactionIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            tx_id = d.add_transaction(body.ticker, body.kind, body.date, body.quantity, body.price, body.currency,
                                      body.fx, body.fees, body.note, body.reason)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": tx_id}

    @app.delete(f"{API_PREFIX}/portfolio/transactions/{{tx_id}}")
    def delete_transaction(tx_id: int, d: Ctx, user: User):  # noqa: ARG001
        d.delete_transaction(tx_id)
        return {"deleted": tx_id}

    @app.get(f"{API_PREFIX}/journal")
    def journal(d: Ctx, user: User):  # noqa: ARG001
        """Diario delle decisioni: riepilogo, da rivedere, tutte con l'esito contro l'indice (dopo 3 mesi)."""
        return jsonable(d.journal_page())

    @app.post(f"{API_PREFIX}/journal")
    def add_decision(body: DecisionIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            new_id = d.add_journal_decision(resolve_ticker(d, body.ticker), body.action, body.reason, body.change_mind,
                                            body.price, body.review_date)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": new_id}

    @app.post(f"{API_PREFIX}/journal/{{dec_id}}/review")
    def review_decision(dec_id: int, body: ReviewIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            d.review_journal_decision(dec_id, body.verdict, body.note)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": dec_id, "verdict": body.verdict}

    @app.delete(f"{API_PREFIX}/journal/{{dec_id}}")
    def delete_decision(dec_id: int, d: Ctx, user: User):  # noqa: ARG001
        d.delete_journal_decision(dec_id)
        return {"deleted": dec_id}

    @app.get(f"{API_PREFIX}/today")
    def today(d: Ctx, user: User, only_mine: bool = False):  # noqa: ARG001
        """La pagina Oggi: le mie aziende, avvisi, notizie, documenti, movimenti, insider, borse. Solo dati salvati:
        prezzi = ultime chiusure del database (nessuna quotazione dal vivo dall'API)."""
        return jsonable(d.today(only_mine))

    # ------------------------------------------------------------ strategie (nome come parametro: spazi, accenti, "/")
    def strategy_name(d: DashboardData, name: str) -> str:
        try:
            d.strategy_label(name)
        except KeyError as exc:
            raise HTTPException(404, f"Strategia sconosciuta: {name}") from exc
        return name

    def run_job(request: Request, d: DashboardData, name: str) -> dict:
        jobs: JobRunner = request.app.state.jobs
        return jobs.submit(d.strategy_run_key(name), name, lambda: d.run_strategy_backtest(name))

    @app.get(f"{API_PREFIX}/strategies")
    def strategies_list(request: Request, d: Ctx, user: User):  # noqa: ARG001
        """Elenco delle strategie (catalogo, grandi investitori, mie) e backtest in coda o in corso."""
        return {"items": d.strategies_list(), "jobs": request.app.state.jobs.active()}

    @app.get(f"{API_PREFIX}/strategies/detail")
    def strategy_detail(request: Request, d: Ctx, user: User,  # noqa: ARG001
                        name: Annotated[str, Query(min_length=1, max_length=200)]):
        """Una strategia con il risultato del backtest (null se non c'è ancora) e lo stato del suo lavoro."""
        name = strategy_name(d, name)
        return jsonable({**d.strategy_page(name), "job": request.app.state.jobs.status(d.strategy_run_key(name))})

    @app.get(f"{API_PREFIX}/strategies/holdings")
    def strategy_holdings(d: Ctx, user: User, name: Annotated[str, Query(min_length=1, max_length=200)]):  # noqa: ARG001
        """Cosa terrebbe oggi la strategia con i dati di oggi (non è un consiglio)."""
        return jsonable(d.strategy_holdings_view(strategy_name(d, name)))

    @app.get(f"{API_PREFIX}/strategies/overview")
    def strategies_overview(request: Request, d: Ctx, user: User):  # noqa: ARG001
        return jsonable({**d.strategies_overview(), "jobs": request.app.state.jobs.active()})

    @app.post(f"{API_PREFIX}/strategies/run")
    def strategy_run(body: StrategyRunIn, request: Request, d: Ctx, user: User):  # noqa: ARG001
        """Mette in coda il backtest (e quello dell'indice a pesi uguali, se manca); risponde subito."""
        return run_job(request, d, strategy_name(d, body.name))

    @app.post(f"{API_PREFIX}/strategies/run-missing")
    def strategy_run_missing(request: Request, d: Ctx, user: User):  # noqa: ARG001
        return {"jobs": [run_job(request, d, n) for n in d.strategies_overview()["missing"]]}

    @app.get(f"{API_PREFIX}/strategies/builder")
    def strategy_builder(d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.strategy_builder_options())

    @app.post(f"{API_PREFIX}/strategies/preview")
    def strategy_preview(body: StrategyIn, d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.strategy_preview(body.definition))

    @app.post(f"{API_PREFIX}/strategies/mine")
    def strategy_save(body: StrategyIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            return {"name": d.save_user_strategy(body.definition, body.editing)}
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc).strip("'\"")) from exc

    @app.delete(f"{API_PREFIX}/strategies/mine")
    def strategy_delete(d: Ctx, user: User, name: Annotated[str, Query(min_length=1, max_length=200)]):  # noqa: ARG001
        try:
            d.delete_user_strategy(name)
        except KeyError as exc:
            raise HTTPException(404, f"Non è una tua strategia: {name}") from exc
        return {"deleted": name}

    # ------------------------------------------------------------ esportazioni della scheda (file da scaricare)
    def download(content: bytes, name: str, media: str) -> Response:
        return Response(content, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/export.xlsx")
    def export_excel(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        """Modello di valutazione in Excel con le formule (DCF, scenari, sensibilità, costo del capitale, LBO)."""
        t = resolve_ticker(d, ticker)
        data = d.export_excel(t)
        if not data:
            raise HTTPException(422, "Valutazione non impostabile: mancano ricavi, azioni o prezzi.")
        day = time.strftime("%Y%m%d")
        return download(data, f"{t}_modello_{day}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/export.pdf")
    def export_pdf(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        return download(d.export_pdf(t), f"{t}_sintesi_{time.strftime('%Y%m%d')}.pdf", "application/pdf")

    # ------------------------------------------------------------ fondi, qualità dei dati, dati a mano
    @app.get(f"{API_PREFIX}/funds")
    def funds(d: Ctx, user: User, fund: Annotated[str | None, Query(max_length=20)] = None):  # noqa: ARG001
        """Grandi investitori: le aziende più presenti nei 13F dei fondi seguiti e il portafoglio di un fondo."""
        return jsonable(d.funds_page(fund))

    @app.get(f"{API_PREFIX}/quality")
    def quality(d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.quality_page())

    @app.get(f"{API_PREFIX}/manual/{{ticker}}")
    def manual(ticker: str, d: Ctx, user: User, kind: Literal["annual", "ttm"] = "annual",  # noqa: ARG001
               unit: str = "milioni", share_unit: str = "milioni"):
        try:
            return jsonable(d.manual_page(resolve_ticker(d, ticker), kind, unit, share_unit))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post(f"{API_PREFIX}/manual/{{ticker}}")
    def save_manual(ticker: str, body: ManualIn, d: Ctx, user: User):  # noqa: ARG001
        """Voci di bilancio inserite a mano (prevalgono sulla SEC per stesso periodo e voce); i derivati li calcola
        il motore. Da quando valgono: oggi (default) o `days_after_close` giorni dopo la chiusura."""
        t = resolve_ticker(d, ticker)
        try:
            n = d.save_manual_values(t, body.changes, body.kind, body.unit, body.share_unit, body.days_after_close)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"saved": n}

    @app.post(f"{API_PREFIX}/manual/{{ticker}}/delete")
    def delete_manual(ticker: str, body: ManualDeleteIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            return {"deleted": d.delete_manual_values(resolve_ticker(d, ticker), body.rows)}
        except KeyError as exc:
            raise HTTPException(422, f"Riga incompleta: manca {exc}") from exc

    @app.get(f"{API_PREFIX}/custom-companies")
    def custom_companies(d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.custom_companies_view())

    @app.post(f"{API_PREFIX}/custom-companies")
    def add_custom_company(body: CustomCompanyIn, d: Ctx, user: User):  # noqa: ARG001
        """Aggiunge un'azienda fuori dall'indice (simbolo Yahoo): i prezzi arrivano con l'aggiornamento dei dati."""
        try:
            return {"ticker": d.add_custom_company(body.symbol, body.name, body.sector, body.currency)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete(f"{API_PREFIX}/custom-companies/{{ticker}}")
    def remove_custom_company(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        d.remove_custom_company(ticker.upper())
        return {"removed": ticker.upper()}

    # ------------------------------------------------------------ backtest e laboratorio
    @app.get(f"{API_PREFIX}/backtest")
    def backtest(d: Ctx, user: User):  # noqa: ARG001
        """Ultimo backtest della strategia automatica e ricerca di segnali e fattori (già calcolati da main.py)."""
        return jsonable(d.backtest_page())

    @app.get(f"{API_PREFIX}/auto-strategy")
    def auto_strategy(d: Ctx, user: User):  # noqa: ARG001
        """Strategia automatica originale (sperimentale, ordini solo simulati): decisioni, portafoglio, rischio."""
        return jsonable(d.auto_strategy_page())

    @app.get(f"{API_PREFIX}/lab/options")
    def lab_options(d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.lab_options())

    @app.post(f"{API_PREFIX}/lab/preview")
    def lab_preview(body: LabIn, d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.lab_preview(body.values, body.period))

    @app.post(f"{API_PREFIX}/lab/run")
    def lab_run(body: LabIn, request: Request, d: Ctx, user: User):  # noqa: ARG001
        """Mette in coda l'esperimento (stessa coda dei backtest delle strategie); risponde subito."""
        prev = d.lab_preview(body.values, body.period)
        if not prev["ok"]:
            raise HTTPException(422, prev["error"])
        jobs: JobRunner = request.app.state.jobs
        label = body.name.strip() or prev["description"]
        return jobs.submit(d.lab_run_key(body.values, body.period, body.with_research), f"Laboratorio: {label}",
                           lambda: d.run_lab_experiment(body.values, body.name, body.period, body.with_research))

    @app.get(f"{API_PREFIX}/lab/experiments")
    def lab_experiments(request: Request, d: Ctx, user: User):  # noqa: ARG001
        return jsonable({**d.lab_experiments(), "jobs": request.app.state.jobs.active()})

    @app.get(f"{API_PREFIX}/lab/compare")
    def lab_compare(d: Ctx, user: User, id: Annotated[list[int], Query(max_length=4)]):  # noqa: A002, ARG001
        return jsonable(d.lab_compare(id))

    @app.delete(f"{API_PREFIX}/lab/experiments/{{exp_id}}")
    def lab_delete(exp_id: int, d: Ctx, user: User):  # noqa: ARG001
        d.delete_experiment(exp_id)
        return {"deleted": exp_id}

    # ------------------------------------------------------------ funzioni "Altro" della scheda
    @app.get(f"{API_PREFIX}/companies/{{ticker}}/metrics")
    def company_metrics(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        """Tutte le metriche del catalogo con le mediane del settore e dell'S&P 500 (funzione DES)."""
        return jsonable(d.company_metrics_view(resolve_ticker(d, ticker)))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/dividends")
    def company_dividends(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.company_dividends(resolve_ticker(d, ticker)))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/ownership")
    def company_ownership(ticker: str, d: Ctx, user: User, everything: bool = False):  # noqa: ARG001
        """Insider (Form 4) e grandi investitori seguiti (ultimo 13F)."""
        return jsonable(d.company_ownership(resolve_ticker(d, ticker), everything))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/research")
    def company_research(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.company_research(resolve_ticker(d, ticker)))

    @app.put(f"{API_PREFIX}/companies/{{ticker}}/research")
    def save_company_research(ticker: str, body: ResearchIn, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        try:
            d.save_company_research(t, body.fields, body.scores, body.thesis)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return jsonable(d.company_research(t))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/investors")
    def company_investors(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        """Tavolo dei grandi investitori: come le regole tradotte dal metodo di ciascuno leggono i numeri di oggi, e
        la sintesi. Solo informazione: non è il loro giudizio e non entra nel giudizio della scheda."""
        return jsonable(d.investor_panel(resolve_ticker(d, ticker)))

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/auto")
    def company_auto(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        """Strategia automatica originale su un'azienda (sperimentale: non è il giudizio)."""
        return jsonable(d.company_auto(resolve_ticker(d, ticker)))

    @app.post(f"{API_PREFIX}/estimates/import")
    def import_estimates(body: EstimatesCsvIn, d: Ctx, user: User):  # noqa: ARG001
        """Stime degli analisti da un CSV (la strada pulita per il lavoro: niente scarico da internet dall'API)."""
        try:
            n, tickers = d.import_estimates_csv(body.csv)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"rows": n, "tickers": sorted(tickers)}

    # ------------------------------------------------------------ pipeline
    @app.get(f"{API_PREFIX}/pipeline")
    def pipeline(d: Ctx, user: User):  # noqa: ARG001
        """Le aziende in studio per fase, con cosa manca per ogni fase, e lo storico degli spostamenti."""
        return jsonable(d.pipeline_board())

    @app.get(f"{API_PREFIX}/companies/{{ticker}}/pipeline")
    def company_pipeline(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        return jsonable(d.pipeline_stage(resolve_ticker(d, ticker)))

    @app.put(f"{API_PREFIX}/companies/{{ticker}}/pipeline")
    def set_company_pipeline(ticker: str, body: PipelineIn, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        d.set_pipeline_stage(t, body.stage, body.note)
        return jsonable(d.pipeline_stage(t))

    @app.delete(f"{API_PREFIX}/companies/{{ticker}}/pipeline")
    def remove_company_pipeline(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        d.remove_from_pipeline(t)
        return jsonable(d.pipeline_stage(t))

    @app.get(f"{API_PREFIX}/news")
    def news(d: Ctx, user: User, view: Literal["all", "mine", "ticker"] = "all",  # noqa: ARG001
             ticker: Annotated[str | None, Query(max_length=20)] = None,
             group: Annotated[list[str] | None, Query()] = None,
             lang: Literal["", "en", "it"] = "", q: Annotated[str | None, Query(max_length=200)] = None,
             limit: Annotated[int, Query(ge=1, le=1000)] = 250):
        """Flusso dei feed RSS dalla copia salvata (l'API non scarica nulla): tutte, le mie aziende o un ticker."""
        return jsonable(d.news_stream(view, ticker, group, lang or None, q, limit))

    @app.get(f"{API_PREFIX}/news/filings")
    def news_filings(d: Ctx, user: User, days: Annotated[int, Query(ge=1, le=90)] = 7, mine: bool = False,  # noqa: ARG001
                     important: bool = False, item: Annotated[list[str] | None, Query()] = None):
        """8-K depositati alla SEC negli ultimi giorni (dal database), con le voci e il segno per quelle da guardare."""
        return jsonable(d.filings_page(days, mine, important, item))

    @app.get(f"{API_PREFIX}/news/x")
    def x_accounts(d: Ctx, user: User):  # noqa: ARG001
        return d.x_accounts()

    @app.post(f"{API_PREFIX}/news/x")
    def add_x_account(body: XAccountIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            return {"accounts": d.add_x_account(body.account)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete(f"{API_PREFIX}/news/x/{{account}}")
    def remove_x_account(account: str, d: Ctx, user: User):  # noqa: ARG001
        return {"accounts": d.remove_x_account(account)}

    @app.get(f"{API_PREFIX}/news/x/{{account}}/embed", include_in_schema=False)
    def x_embed(account: str, d: Ctx, user: User, dark: bool = False):  # noqa: ARG001
        """Pagina con il riquadro ufficiale di X (lo script lo carica il browser da platform.twitter.com, non l'API);
        l'interfaccia la mostra in un iframe isolato (sandbox senza la stessa origine)."""
        try:
            page = d.x_embed_html(account, dark)
        except ValueError as exc:
            raise HTTPException(422, "Nome utente X non valido.") from exc
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get(f"{API_PREFIX}/compare")
    def compare(d: Ctx, t: Annotated[list[str], Query(max_length=5)], years: int = 5):
        """Da 2 a 5 aziende affiancate: giudizio e pilastri, metriche con la migliore di ogni riga, grafici nel
        tempo. `t` ripetuto (?t=AAPL&t=MSFT) o separato da virgole (?t=AAPL,MSFT)."""
        tickers = [x.strip() for part in t for x in part.split(",") if x.strip()]
        try:
            return jsonable(d.compare(tickers, years))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(f"{API_PREFIX}/explore/catalog")
    def explore_catalog(d: Ctx):
        """Metriche, operatori, ricerche pronte e salvate, universi, settori, pilastri e giudizi per i filtri."""
        return jsonable(d.explore_catalog())

    @app.post(f"{API_PREFIX}/explore")
    def explore(body: ExploreIn, d: Ctx):
        """Aziende che passano tutti i filtri (un dato mancante non passa mai), già ordinate."""
        try:
            return jsonable(d.explore(body.universe, [r.model_dump() for r in body.rules], body.sectors, body.verdicts,
                                      body.min_scores, body.text, body.sort, body.columns))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put(f"{API_PREFIX}/screens/{{name}}")
    def save_screen(name: str, body: ScreenIn, d: Ctx, user: User):  # noqa: ARG001
        try:
            d.save_screen(name, [r.model_dump() for r in body.rules], body.sectors,
                          {"verdicts": body.verdicts, "min_scores": body.min_scores})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"saved": name.strip()}

    @app.delete(f"{API_PREFIX}/screens/{{name}}")
    def delete_screen(name: str, d: Ctx, user: User):  # noqa: ARG001
        d.delete_screen(name)
        return {"deleted": name}

    @app.get(f"{API_PREFIX}/my-companies")
    def my_companies(d: Ctx, user: User):  # noqa: ARG001
        """Le mie aziende: una riga per azienda seguita (giudizio, pilastri, fair value, soglie, avvisi), gli avvisi
        da guardare e, se non segui nulla, le prime «Candidate» da cui cominciare."""
        return jsonable(d.my_companies())

    @app.get(f"{API_PREFIX}/alerts")
    def alerts(d: Ctx, user: User, state: Literal["attivi", "non_visti", "chiusi", "tutti"] = "attivi"):  # noqa: ARG001
        return jsonable({"state": state, "alerts": d.alert_history(state)})

    @app.post(f"{API_PREFIX}/alerts/seen")
    def alerts_seen(body: SeenIn, d: Ctx, user: User):  # noqa: ARG001
        d.mark_alerts_seen(body.ids)
        return {"seen": len(body.ids)}

    @app.put(f"{API_PREFIX}/watchlist/{{ticker}}")
    def follow(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        d.follow(t, on=True)
        return {"tickers": d.watchlist()}

    @app.delete(f"{API_PREFIX}/watchlist/{{ticker}}")
    def unfollow(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        d.follow(t, on=False)
        return {"tickers": d.watchlist()}

    @app.put(f"{API_PREFIX}/companies/{{ticker}}/price-target")
    def set_price_target(ticker: str, body: PriceTargetIn, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        try:
            d.set_price_target(t, body.buy_below, body.sell_above, body.note)
        except ValueError as exc:  # soglie mancanti o invertite: messaggio già in italiano
            raise HTTPException(422, str(exc)) from exc
        return {"ticker": t, "price_target": d.price_target(t)}

    @app.delete(f"{API_PREFIX}/companies/{{ticker}}/price-target")
    def delete_price_target(ticker: str, d: Ctx, user: User):  # noqa: ARG001
        t = resolve_ticker(d, ticker)
        d.delete_price_target(t)
        return {"ticker": t, "price_target": {}}

    # ------------------------------------------------------------ interfaccia web compilata (stessa porta)
    @app.api_route(f"{API_PREFIX}/{{rest:path}}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        raise HTTPException(404, f"Indirizzo dell'API sconosciuto: {API_PREFIX}/{rest}")

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        """File di web/dist (asset, loghi) o index.html per gli indirizzi dell'interfaccia (/azienda/AAPL...)."""
        if web_dist is None or not (web_dist / "index.html").exists():
            raise HTTPException(404, "Interfaccia non compilata: cd web && npm install && npm run build")
        root = web_dist.resolve()
        target = (root / path).resolve()
        if path and target.is_file() and root in target.parents:  # niente uscite dalla cartella (../)
            # file con l'impronta nel nome (assets/index-AbC123.js): non cambiano mai, cache lunga; gli altri (loghi)
            # si ricontrollano a ogni apertura
            immutable = target.parent.name == "assets"
            return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable
                                                 else "no-cache"})
        if "." in Path(path).name:  # un file che non c'è (es. /favicon.ico): 404, non la pagina
            raise HTTPException(404, f"File non trovato: /{path}")
        # la pagina mai in cache: dopo un aggiornamento dell'interfaccia il browser deve prendere i file nuovi
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
