// Dove porta ogni comando del motore (modules/commands.py, elenco generato in commands.json): pagine della webapp
// classica -> indirizzi del web, funzioni della scheda (codici) -> parametro fn. Il test in routes.test.ts fallisce se
// il motore ha una pagina o una funzione senza destinazione qui.
export const PAGE_ROUTES: Record<string, string> = {
  "": "/", "mie-aziende": "/mie-aziende", pipeline: "/pipeline", notizie: "/notizie", azienda: "/azienda",
  portafoglio: "/portafoglio", diario: "/diario", esplora: "/esplora", confronta: "/confronta", fondi: "/fondi",
  metodo: "/metodo", dati: "/inserisci-dati", strategie: "/strategie", "strategia-automatica": "/strategia-automatica",
  backtest: "/backtest", laboratorio: "/laboratorio", qualita: "/qualita", limiti: "/limiti",
};

export const FUNCTION_PARAMS: Record<string, string> = {
  PRV: "prove", FA: "bilanci", GP: "grafico", DCF: "valutazione", RV: "concorrenti", CF: "documenti", ERN: "trimestri",
  DVD: "dividendi", HDS: "insider", NOTE: "ricerca", DES: "metriche", EE: "stime", AUTO: "automatica", TAV: "investitori",
};

export function companyRoute(ticker: string, section?: string | null): string {
  const fn = section ? FUNCTION_PARAMS[section] : undefined;
  return `/azienda/${encodeURIComponent(ticker)}${fn && fn !== "prove" ? `?fn=${fn}` : ""}`;
}
