// Lingua dell'interfaccia: inglese (predefinito, prodotto) o italiano. Il testo italiano scritto nel codice È la chiave:
// t("Le mie aziende") -> "My companies" dal dizionario src/i18n/en/*.json (un file per area). Parti variabili con
// {nome}: t("{n} aziende", { n: 3 }). I testi che arrivano dall'API li traduce il server (intestazione X-Lang,
// modules/i18n). Cambiare lingua ricarica la pagina (niente testi vecchi nella cache delle richieste).
// i18n.test.ts fallisce se una chiave usata con t() manca nel dizionario inglese.
import { setFmtLang, type Lang } from "./fmt";

export type { Lang };
const LANG_KEY = "qe_lingua";

const files = import.meta.glob<Record<string, string>>("../i18n/en/*.json", { eager: true, import: "default" });
export const EN: Record<string, string> = Object.assign({}, ...Object.values(files));

function readLang(): Lang {
  // ?lang=en|it nell'indirizzo (demo, materiale di vendita) prevale ed è ricordato
  const fromUrl = typeof location !== "undefined" ? new URLSearchParams(location.search).get("lang") : null;
  try {
    if (fromUrl === "en" || fromUrl === "it") localStorage.setItem(LANG_KEY, fromUrl);
    const v = fromUrl ?? localStorage.getItem(LANG_KEY);
    return v === "it" ? "it" : "en";
  } catch {
    return fromUrl === "it" ? "it" : "en";
  }
}

export const LANG: Lang = readLang();
setFmtLang(LANG);
if (typeof document !== "undefined") document.documentElement.lang = LANG;

export function setLang(lang: Lang) {
  try { localStorage.setItem(LANG_KEY, lang); } catch { /* niente archiviazione */ }
  const url = new URL(location.href);
  url.searchParams.delete("lang");
  location.replace(url.toString());
}

export function t(text: string, vars?: Record<string, string | number | null | undefined>): string {
  let out = LANG === "en" ? (EN[text] ?? text) : text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k: string) => (vars[k] === undefined || vars[k] === null ? m : String(vars[k])));
  return out;
}

/** Singolare/plurale: tn(n, "{n} azienda", "{n} aziende"). */
export function tn(n: number, one: string, many: string, vars?: Record<string, string | number | null | undefined>): string {
  return t(n === 1 ? one : many, { n, ...vars });
}
