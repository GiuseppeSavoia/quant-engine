"""Base comune dei servizi: nessuno stato proprio, tutto passa dal contesto (`ctx`)."""
from __future__ import annotations


class Service:
    """Un'area dei dati dell'app (scheda azienda, portafoglio, strategie...).

    Regole, perché `DashboardData` resti identica a prima:
    - lo stato (dati, anagrafica, cache `_metrics`, `_industry`, `_erp`) sta sul contesto, non qui;
    - ogni metodo pubblico si chiama attraverso il contesto (`self.ctx.metrics()`, `self.ctx.note(t)`, anche quelli
      dello stesso servizio): se il contesto è `DashboardData` o una sua sottoclasse (PointInTimeView ridefinisce
      `metrics`, `sectors`, `implied_erp`, `quality_issues`; i test sostituiscono `fund_positions`) vale la versione
      ridefinita, come quando tutto era un'unica classe. Con un `AppData` semplice i nomi arrivano al servizio giusto
      (`AppData.__getattr__`);
    - gli aiuti privati (`_facts_store`, `_fx_close`...) restano nel servizio.
    """

    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def cfg(self) -> dict:
        return self.ctx.cfg

    @property
    def db(self):
        return self.ctx.db

    @property
    def data(self):
        return self.ctx.data
