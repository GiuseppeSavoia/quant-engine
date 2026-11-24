import { describe, expect, it } from "vitest";
import { EN } from "./i18n";

// Ogni testo passato a t()/tn() deve avere la traduzione inglese; nessun testo visibile fuori da t() (testo nei tag
// JSX, attributi label/title/placeholder/aria-label/alt, campi label:/title:/hint: degli oggetti). Sorgenti letti con
// import.meta.glob (niente tipi di Node).
const RAW = import.meta.glob<string>(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx"],
  { query: "?raw", import: "default", eager: true });
const DICTS = import.meta.glob<Record<string, string>>("../i18n/en/*.json", { import: "default", eager: true });
const SOURCES = Object.entries(RAW).map(([p, text]) => ({ path: p.replace(/^\.\.\//, ""),
  text: text.split("\n").map((l) => (/^\s*(\/\/|\*|\/\*)/.test(l) ? "" : l)).join("\n") }));
const LETTERS = /[A-Za-zÀ-ÿ]{2,}/;

/** Argomenti stringa di t("...") e tn(n, "...", "...") (letterali semplici, anche su più righe). */
export function usedKeys(text: string): string[] {
  const keys: string[] = [];
  const lit = String.raw`"((?:[^"\\]|\\.)*)"`;
  for (const m of text.matchAll(new RegExp(String.raw`\bt\(\s*` + lit, "g"))) keys.push(m[1]);
  for (const m of text.matchAll(new RegExp(String.raw`\btn\([^,]+,\s*` + lit + String.raw`\s*,\s*` + lit, "g"))) keys.push(m[1], m[2]);
  return keys.map((k) => JSON.parse(`"${k}"`) as string);
}

// parole che restano uguali in inglese (marchio, sigle) o righe di codice che il controllo scambierebbe per testo
const ALLOWED = /^(QuantEngine|DEMO|ETF|EPS|ROIC|ROE|FCF|EBITDA|EV|P\/E|P\/B|DCF|LBO|SEC|IRR|WACC|CAGR|Sharpe|TTM|YoY|ID|[A-Z0-9 .,:/%+\-−–·×()&↵↑↓⌘▲▼■●]+)$/;

export function leaks(path: string, text: string): string[] {
  const out: string[] = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    const code = line.replace(/\{\/\*.*?\*\/\}/g, "").replace(/\/\/.*$/, "");
    if (/^\s*(\/\/|\*|\/\*|import )/.test(line)) return;
    const found: string[] = [];
    for (const m of code.matchAll(/(?<![=-])>([^<>{}]*)</g))                                          // testo tra i tag
      if (!/[()=;&|?:]|^\s*[,.\]]/.test(m[1])) found.push(m[1]);
    for (const m of code.matchAll(/^\s*([^<>{}=()]*[A-Za-zÀ-ÿ][^<>{}=()]*)\s*$/g))           // riga di solo testo JSX
      if (/^\s*[A-ZÀ-Ýa-zà-ÿ]/.test(m[1]) && /\s/.test(m[1].trim()) && !/[;=]|=>|^\s*(return|const|let|if|else|export|function|case|type|interface)\b/.test(m[1])
        && lines.slice(Math.max(0, i - 3), i).some((l) => />\s*$|\)\s*$|>\{|\}\s*$/.test(l)) && /<|\/>|>/.test(lines.slice(Math.max(0, i - 3), i + 2).join(""))) found.push(m[1]);
    for (const m of code.matchAll(/\b(?:label|title|placeholder|aria-label|alt|hint|empty|why|caption)=["']([^"']+)["']/g)) found.push(m[1]);
    for (const m of code.matchAll(/\b(?:label|title|hint|why|what|caption|empty|help|desc|placeholder|text|note):\s*"([^"]+)"/g)) found.push(m[1]);
    for (const s of found) {
      const v = s.trim();
      if (!v || !LETTERS.test(v) || ALLOWED.test(v) || EN[v] !== undefined) continue;
      out.push(`${path}:${i + 1}: ${v}`);
    }
  });
  return out;
}

describe("lingua dell'interfaccia", () => {
  it("ogni testo passato a t() ha la traduzione inglese", () => {
    const missing = SOURCES.flatMap(({ path, text }) => usedKeys(text)
      .filter((k) => LETTERS.test(k) && EN[k] === undefined).map((k) => `${path}: ${k}`));
    expect(missing).toEqual([]);
  });
  it("nessun testo visibile fuori da t()", () => {
    const out = SOURCES.filter((s) => s.path.endsWith(".tsx")).flatMap(({ path, text }) => leaks(path, text));
    expect(out).toEqual([]);
  });
  it("i file del dizionario non si contraddicono", () => {
    const seen: Record<string, [string, string]> = {};
    const clash: string[] = [];
    for (const [f, dict] of Object.entries(DICTS)) {
      for (const [k, v] of Object.entries(dict)) {
        if (seen[k] && seen[k][1] !== v) clash.push(`${k}: ${seen[k][0]} / ${f}`);
        seen[k] = [f, v];
      }
    }
    expect(clash).toEqual([]);
  });
  it("le traduzioni hanno le stesse parti variabili", () => {
    const vars = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
    const wrong = Object.entries(EN).filter(([it, en]) => vars(it) !== vars(en)).map(([it]) => it);
    expect(wrong).toEqual([]);
  });
});
