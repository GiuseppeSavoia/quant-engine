"""Notizie: 8-K recenti (tutte le aziende dell'app o alcune), 10-Q/10-K appena visti nell'indice SEC, flusso dei feed
RSS dalla copia salvata (il thread che li scarica è in modules/news.py e gira nella webapp Streamlit: qui niente rete)
e account X scelti dall'utente (solo l'elenco e il riquadro ufficiale di X)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.services.base import Service


class NewsService(Service):
    def news(self, days: int = 3, tickers=None) -> pd.DataFrame:
        """8-K degli ultimi `days` giorni (tutte le aziende o `tickers`): quando, chi, cosa, se da guardare."""
        from modules.sec_filings import describe_items, filing_url, important

        since = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        ev = self.db.load_events(tickers if tickers is not None else self.ctx.tickers, since=since)  # niente ex membri
        if ev.empty:
            return ev
        ev["when"] = ev["accepted"].fillna(ev["filed"])
        ev["name"] = [self.ctx.info(t).get("name") or "" for t in ev["ticker"]]
        ev["what"] = [describe_items(i) if isinstance(i, str) else "(voci in arrivo)" for i in ev["items"]]
        ev["important"] = [important(i) for i in ev["items"]]
        ev["url"] = [filing_url(c, a) for c, a in zip(ev["cik"], ev["accession"])]
        return ev.sort_values("when", ascending=False).reset_index(drop=True)

    def recent_filings(self, days: int = 14) -> pd.DataFrame:
        """10-Q/10-K delle aziende seguite visti nell'indice SEC negli ultimi `days` giorni, con lo stato."""
        since = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        return self.db.load_filings(since=since)

    # ------------------------------------------------------------------ pagina Notizie
    def _followed(self) -> list[str]:
        from modules.alerts import followed_tickers

        return followed_tickers(self.db)

    def news_stream(self, view: str = "all", ticker: str | None = None, groups=None, lang: str | None = None,
                    query: str | None = None, limit: int = 250) -> dict:
        """Flusso dei feed RSS dalla copia salvata: tutte, le mie aziende (seguite, in portafoglio, con soglia) o un
        ticker; filtri per gruppo di fonti, lingua e testo nel titolo. Le più recenti per prime, al massimo `limit`."""
        from modules import news as news_mod
        from modules.config import PROJECT_ROOT

        n = self.cfg.get("news") or {}
        followed = self._followed()
        table, saved = pd.DataFrame(columns=news_mod.COLUMNS), None
        if n.get("enabled", True):
            try:
                table, saved = news_mod.saved_table(n, PROJECT_ROOT)
            except Exception:  # noqa: BLE001 - copia illeggibile: pagina vuota, non un errore
                pass
        t = (ticker or "").strip().upper()
        tickers = followed if view == "mine" else [t] if view == "ticker" and t else None
        rows = news_mod.filter_table(table, tickers, groups, lang or None, query)
        return {
            "enabled": bool(n.get("enabled", True)), "saved_at": saved, "total": int(len(table)),
            "matched": int(len(rows)), "limit": int(limit), "groups": news_mod.GROUPS, "followed": followed,
            "keep_days": float(n.get("keep_days", 3)),
            "items": [{"published": r["published"], "first_seen": r["first_seen"], "source": r["source"],
                       "feed": r["feed"], "group": r["group"], "title": r["title"], "link": r["link"],
                       "tickers": str(r["tickers"] or "").split(), "lang": r["lang"]}
                      for _, r in rows.head(limit).iterrows()],
        }

    def filings_page(self, days: int = 7, mine: bool = False, important_only: bool = False, kinds=None) -> dict:
        """8-K degli ultimi `days` giorni (tutte le aziende o le mie), solo quelli da guardare subito o di alcune voci."""
        from modules.sec_filings import ITEM_LABELS

        followed = self._followed()
        ev = self.ctx.news(days, followed if mine else None)
        if not ev.empty and important_only:
            ev = ev[ev["important"]]
        kinds = set(kinds or [])
        if not ev.empty and kinds:
            ev = ev[[bool(set(str(i or "").split(",")) & kinds) for i in ev["items"]]]
        return {
            "days": int(days), "mine": bool(mine), "followed": followed,
            "item_labels": [{"code": k, "label": v} for k, v in ITEM_LABELS.items()],
            "companies": int(ev["ticker"].nunique()) if not ev.empty else 0,
            "items": [{"when": r["when"], "ticker": r["ticker"], "name": r["name"], "form": r["form"],
                       "what": r["what"], "important": bool(r["important"]), "url": r["url"],
                       "codes": [c for c in str(r["items"] or "").split(",") if c] if isinstance(r["items"], str) else []}
                      for _, r in ev.iterrows()] if not ev.empty else [],
        }

    def _x_path(self) -> Path:
        from modules.config import PROJECT_ROOT

        db = Path(self.db.db_path)
        return (db if db.is_absolute() else PROJECT_ROOT / db).parent / "preferenze_x.json"

    def x_accounts(self) -> dict:
        """Account X scelti dall'utente (preferenze accanto al database) e altezza del riquadro."""
        from modules import news as news_mod

        n = self.cfg.get("news") or {}
        return {"accounts": news_mod.load_x_accounts(self._x_path(), n.get("x_accounts") or []),
                "height": int(n.get("x_height", 600))}

    def add_x_account(self, text: str) -> list[str]:
        """Aggiunge un account (da "@nome", "nome" o link al profilo); ValueError in italiano se non va."""
        from modules import news as news_mod

        user = news_mod.x_username(text)
        if user is None:
            raise ValueError("Nome utente non valido: solo lettere, cifre e underscore, al massimo 15 caratteri.")
        accounts = self.ctx.x_accounts()["accounts"]
        if user.lower() in {a.lower() for a in accounts}:
            raise ValueError(f"@{user} è già nell'elenco.")
        return news_mod.save_x_accounts(self._x_path(), accounts + [user])

    def remove_x_account(self, user: str) -> list[str]:
        from modules import news as news_mod

        accounts = self.ctx.x_accounts()["accounts"]
        return news_mod.save_x_accounts(self._x_path(), [a for a in accounts if a.lower() != str(user).lower()])

    def x_embed_html(self, user: str, dark: bool = False) -> str:
        """Pagina con il riquadro ufficiale della timeline (ValueError se il nome non è valido)."""
        from modules import news as news_mod

        return news_mod.x_timeline_html(user, dark=dark, height=self.ctx.x_accounts()["height"])
