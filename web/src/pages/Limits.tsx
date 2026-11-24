// Metodo e limiti: tutte le cautele in un posto solo (le pagine ne mostrano una sola riga, in fondo). Testo fisso: i
// numeri della prova sul passato sono quelli documentati in CLAUDE.md e nei rapporti in reports/.
import { Link } from "react-router-dom";
import { Card } from "../components/ui";
import { t } from "../lib/i18n";

export function LimitsPage() {
  return (
    <div className="page limits">
      <div>
        <h1>{t("Metodo e limiti")}</h1>
        <p className="lead">{t("Come lavora QuantEngine, cosa sa e cosa non sa. Vale per ogni pagina: è un'analisi a criteri fissi, non consulenza finanziaria né una raccomandazione.")}</p>
      </div>
      <div className="grid-halves">
        <Card title={t("Il giudizio della scheda")}>
          <p>{t("Cinque pilastri (qualità, solidità, prezzo, segnali d'allarme e, solo come contesto, chi è dentro) fatti di prove con soglie fissate prima di guardare i risultati. Un dato mancante non conta mai come positivo. Le regole che decidono l'esito sono scritte in chiaro nella scheda.")}</p>
          <p><b>{t("Prova sul passato.")}</b> {t("Le stesse regole applicate a ogni fine trimestre dal 2013 al 2026, con i soli dati noti allora e i membri dell'indice di allora: le aziende «Candidata all'acquisto» non hanno battuto l'indice a pesi uguali (circa −0,8% a 12 mesi, non significativo). Il giudizio è quindi una checklist ragionata per decidere cosa studiare, non un segnale provato.")}</p>
        </Card>
        <Card title={t("Strategie e backtest")}>
          <p>{t("Ogni backtest usa a ogni data solo i bilanci già depositati e la composizione dell'indice di allora; gli ordini si eseguono il giorno dopo, con costi. Restano tre cautele:")}</p>
          <ul className="plain">
            <li>{t("più configurazioni si provano sugli stessi dati, più la migliore è anche la più fortunata (per questo lo Sharpe è corretto per i tentativi);")}</li>
            <li>{t("le aziende uscite dall'indice e poi sparite mancano in parte (survivorship bias);")}</li>
            <li>{t("il 2012-2026 è stato favorevole alle grandi aziende di qualità.")}</li>
          </ul>
        </Card>
        <Card title={t("Il tavolo dei grandi investitori")}>
          <p>{t("Mostra come le regole tradotte dal metodo pubblicato di ogni investitore leggono i numeri di oggi. Non è il loro giudizio: mancano tutto ciò che non si misura (management, vantaggio competitivo, tempismo) e i mercati diversi dall'S&P 500. Non entra nel giudizio né nelle strategie.")}</p>
        </Card>
        <Card title={t("I dati")}>
          <ul className="plain">
            <li>{t("Prezzi: chiusure giornaliere (nessuna quotazione in tempo reale nella pagina Oggi).")}</li>
            <li>{t("Bilanci: depositi XBRL alla SEC (10-K, 10-Q) e ESEF per le europee; dove mancano, valori di riserva segnati nella scheda.")}</li>
            <li>{t("Stime degli analisti: solo informazione, mai nel giudizio; storicamente ottimiste.")}</li>
            <li>{t("Notizie: titoli da feed pubblici, con un ritardo tipico da 1 a 15 minuti.")}</li>
          </ul>
          <p>{t("La qualità dei dati di ogni aggiornamento è nella pagina")} <Link to="/qualita">{t("Qualità dei dati")}</Link>.</p>
        </Card>
      </div>
      <Card title={t("Cosa non fa")}>
        <p>{t("Non è collegato a nessun broker e non invia ordini: quelli della strategia automatica sono simulati. Non conosce la tua situazione, i tuoi obiettivi né il tuo profilo di rischio. Le decisioni d'investimento restano tue.")}</p>
      </Card>
    </div>
  );
}
