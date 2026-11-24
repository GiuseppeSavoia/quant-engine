// Avvio guidato al posto delle pagine vuote (primo accesso, cliente in prova): seguire le prime aziende con un clic
// (le prime «Candidate» dell'S&P 500 per punteggio, dal servizio di Le mie aziende), provare la ricerca a comandi,
// preparare portafoglio e filtri. Nessun dato inventato: solo scorciatoie verso ciò che già esiste.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type MyCompanies } from "../lib/api";
import { companyRoute } from "../lib/routes";
import { t } from "../lib/i18n";

export function GetStarted() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["my-companies"], queryFn: () => api<MyCompanies>("/my-companies") });
  const follow = useMutation({
    mutationFn: (tickers: string[]) => Promise.all(tickers.map((t) => api(`/watchlist/${encodeURIComponent(t)}`, { method: "PUT" }))),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["my-companies"] }); qc.invalidateQueries({ queryKey: ["today"] }); },
  });
  const sugg = q.data?.suggestions ?? [];
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  return (
    <div className="onboard">
      <div className="step">
        <span className="n">1</span>
        <div>
          <b>{t("Segui le prime aziende")}</b>
          <p>{t("Prezzi, giudizio e avvisi (bilanci nuovi, prezzo sotto la tua soglia) arrivano qui. Per cominciare, le prime «Candidate» dell'S&P 500 per punteggio:")}</p>
          {sugg.length > 0 && (
            <div className="onboard-picks">
              {sugg.map((s) => (
                <span key={s.ticker} className="pick-chip">
                  <Link to={companyRoute(s.ticker)}><b>{s.ticker}</b></Link><span className="nm">{s.name}</span>
                  <button type="button" aria-label={t("Segui {tickers}", { tickers: s.ticker })} disabled={follow.isPending} onClick={() => follow.mutate([s.ticker])}>+</button>
                </span>
              ))}
              <button type="button" className="btn primary" disabled={follow.isPending} onClick={() => follow.mutate(sugg.map((s) => s.ticker))}>{t("Segui tutte")}</button>
            </div>
          )}
        </div>
      </div>
      <div className="step">
        <span className="n">2</span>
        <div>
          <b>{t("Cerca e comanda dalla tastiera")}</b>
          <p>{t("Premi")} <kbd>{mac ? "⌘K" : "Ctrl K"}</kbd> {t("o")} <kbd>/</kbd> {t("e scrivi un ticker, un nome o un comando:")}</p>
          <div className="onboard-cmds">
            <Link to={companyRoute(sugg[0]?.ticker ?? "MSFT", "FA")}><code>{sugg[0]?.ticker ?? "MSFT"} FA</code> {t("bilanci")}</Link>
            <Link to={companyRoute(sugg[0]?.ticker ?? "MSFT", "DCF")}><code>{sugg[0]?.ticker ?? "MSFT"} DCF</code> {t("valutazione")}</Link>
            {sugg.length > 1 && <Link to={`/confronta?t=${sugg.slice(0, 2).map((s) => s.ticker).join(",")}`}><code>{sugg[0].ticker} {sugg[1].ticker}</code> {t("confronto")}</Link>}
          </div>
        </div>
      </div>
      <div className="step">
        <span className="n">3</span>
        <div>
          <b>{t("Trova idee e registra le tue posizioni")}</b>
          <p><Link to="/esplora">{t("Esplora")}</Link> {t("con i filtri o le ricerche pronte, guarda chi le comprerebbe nel tavolo dei grandi investitori, poi aggiungi le tue operazioni nel")} <Link to="/portafoglio">{t("Portafoglio")}</Link>.</p>
        </div>
      </div>
    </div>
  );
}
