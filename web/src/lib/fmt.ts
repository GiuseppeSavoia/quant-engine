// Formati dei numeri in italiano o inglese (lingua dell'interfaccia, lib/i18n.ts): porta di modules/formats.fmt (UNICA definizione, in Python).
// I test (fmt.test.ts) confrontano questa versione con i casi generati da Python (fmt_cases.json,
// tools/export_web_shared.py): se le regole cambiano in Python, si rigenerano i casi e questi test lo dicono.

export const NA = "–";
const MINUS = "−";
export const CURRENCY_SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", CHF: "CHF", JPY: "¥" };
const DIGITS: Record<string, number> = { num: 2, int: 0, price: 2, pct: 1, pp: 1, mult: 1, score: 2, money: 1, shares: 1 };

export type Kind = "num" | "int" | "price" | "pct" | "pp" | "mult" | "score" | "money" | "shares" | "text" | "date" | "date_short";
export interface FmtOptions {
  digits?: number;
  currency?: string | boolean | null;
  signed?: boolean;
  arrow?: boolean;
  na?: string;
  lang?: Lang;
}
export type Lang = "it" | "en";
let currentLang: Lang = "it";
/** Lingua predefinita di fmt (la imposta lib/i18n.ts all'avvio). */
export function setFmtLang(lang: Lang) { currentLang = lang; }
const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function toNumber(v: unknown): number | null {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    if (v.trim() === "" || /^\d{4}-\d{2}-\d{2}/.test(v)) return null;
    const x = Number(v);
    return Number.isFinite(x) ? x : null;
  }
  return null;
}

// Arrotondamento come round() di Python: sul valore binario esatto, a metà esatta verso il pari.
function roundedDigits(absx: number, d: number): string {
  const exact = absx.toFixed(100); // espansione decimale esatta del double (|x| < 1e21)
  const [intPart, frac] = exact.split(".");
  const keep = frac.slice(0, d);
  const rest = frac.slice(d);
  const restTrim = rest.replace(/0+$/, "");
  let up: boolean;
  if (restTrim === "" || rest[0] < "5") up = false;
  else if (rest[0] > "5" || restTrim.length > 1) up = true;
  else {
    const last = d > 0 ? keep[d - 1] : intPart[intPart.length - 1];
    up = Number(last) % 2 === 1; // metà esatta: verso il pari
  }
  let digits = intPart + keep;
  if (up) {
    const arr = digits.split("").map(Number);
    let i = arr.length - 1;
    while (i >= 0) {
      if (arr[i] === 9) { arr[i] = 0; i -= 1; } else { arr[i] += 1; break; }
    }
    if (i < 0) arr.unshift(1);
    digits = arr.join("");
  }
  const ip = digits.slice(0, digits.length - d) || "0";
  const fp = d > 0 ? digits.slice(digits.length - d) : "";
  return d > 0 ? `${ip}.${fp}` : ip;
}

function italian(plain: string): string {
  const [ip, fp] = plain.split(".");
  const grouped = ip.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return fp !== undefined ? `${grouped},${fp}` : grouped;
}

function symbol(currency: FmtOptions["currency"]): string {
  if (currency === true) return "$";
  if (!currency) return "";
  return CURRENCY_SYMBOL[String(currency)] ?? String(currency);
}

function english(plain: string): string {
  const [ip, fp] = plain.split(".");
  const grouped = ip.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fp !== undefined ? `${grouped}.${fp}` : grouped;
}

function fmtDate(v: unknown, short: boolean, na: string, lang: Lang): string {
  if (typeof v !== "string") return na;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return na;
  if (lang === "en") return `${Number(m[3])} ${MONTHS_EN[Number(m[2]) - 1]} ${short ? m[1].slice(2) : m[1]}`;
  return short ? `${m[3]}/${m[2]}/${m[1].slice(2)}` : `${m[3]}/${m[2]}/${m[1]}`;
}

export function fmt(v: unknown, kind: Kind = "num", opt: FmtOptions = {}): string {
  const na = opt.na ?? NA;
  const lang = opt.lang ?? currentLang;
  const en = lang === "en";
  if (kind === "text") return v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)) ? na : String(v);
  if (kind === "date" || kind === "date_short") return fmtDate(v, kind === "date_short", na, lang);
  let x = toNumber(v);
  if (x === null) return na;
  let d = opt.digits ?? DIGITS[kind] ?? 2;
  let signed = !!opt.signed;
  const arrow = !!opt.arrow;
  let unit = "";
  let suffix = "";
  if (kind === "pct") { x *= 100; suffix = "%"; }
  else if (kind === "pp") { x *= 100; suffix = en ? " pp" : " punti"; signed = true; }
  else if (kind === "mult") suffix = "x";
  else if (kind === "money" || kind === "shares") {
    const scales: [number, string][] = en ? [[1e12, "T"], [1e9, "B"], [1e6, "M"]] : [[1e12, " T"], [1e9, " mld"], [1e6, " mln"]];
    const hit = scales.find(([div]) => Math.abs(x as number) >= div);
    if (hit) { x /= hit[0]; unit = hit[1]; }
    else d = opt.digits ?? (Math.abs(x) >= 1000 || x === 0 ? 0 : 2);
  }
  const plain = roundedDigits(Math.abs(x), d);
  const isZero = !/[1-9]/.test(plain);
  const neg = x < 0 && !isZero;
  const pos = x > 0 && !isZero;
  const sign = neg ? MINUS : (signed || arrow) && pos ? "+" : "";
  const sym = ["money", "price", "num", "int"].includes(kind) ? symbol(opt.currency) : "";
  let text = en
    ? `${sign}${sym ? (sym.length > 1 ? `${sym} ` : sym) : ""}${english(plain)}${unit}${suffix}`
    : `${sign}${italian(plain)}${unit}${suffix}${sym ? ` ${sym}` : ""}`;
  if (arrow) text = (pos ? "▲ " : neg ? "▼ " : "■ ") + text;
  return text;
}

export function toneOf(v: unknown): "pos" | "neg" | "flat" {
  const x = toNumber(v);
  return x === null || x === 0 ? "flat" : x > 0 ? "pos" : "neg";
}

// tipo di metrica del catalogo (modules/metrics.Metric.fmt) -> tipo di fmt
export function kindOfMetric(metricFmt: string | undefined): Kind {
  return ({ pct: "pct", x: "mult", num: "num", money: "money", int: "int", date: "date" } as Record<string, Kind>)[metricFmt ?? ""] ?? "num";
}
