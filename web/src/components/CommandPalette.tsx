// Barra di ricerca a comandi (⌘K, Ctrl+K o "/"): aziende per ticker o nome, comandi del motore ("MSFT FA", "AAPL MSFT",
// "SEGUI KO", "ESPLORA"; interpretati da modules/commands.py tramite /command, niente regole copiate qui), pagine e
// aziende recenti. Tutto da tastiera: frecce, Invio, Esc.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CompanyRow } from "../lib/api";
import commands from "../lib/commands.json";
import { companyRoute, PAGE_ROUTES } from "../lib/routes";
import { LANG, t } from "../lib/i18n";

interface Parsed { page: string | null; tickers: string[]; follow: boolean | null; section: string | null; help: boolean; error: string | null; names: Record<string, string> }
interface Item { key: string; group: string; title: string; hint?: string; code?: string; run: () => void }

const RECENT_KEY = "qe_recenti";
export const PAGES: { to: string; label: string; words: string }[] = [
  { to: "/", label: "Oggi", words: "oggi home monitor" }, { to: "/mie-aziende", label: "Le mie aziende", words: "seguite watchlist" },
  { to: "/esplora", label: "Esplora", words: "filtri screener ricerca" }, { to: "/confronta", label: "Confronta", words: "confronto" },
  { to: "/portafoglio", label: "Portafoglio", words: "posizioni operazioni" }, { to: "/notizie", label: "Notizie", words: "news 8-k" },
  { to: "/pipeline", label: "Pipeline", words: "idee studio" }, { to: "/diario", label: "Diario delle decisioni", words: "decisioni journal" },
  { to: "/metodo", label: "Il mio metodo", words: "checklist formule" }, { to: "/strategie", label: "Strategie", words: "backtest regole" },
  { to: "/backtest", label: "Backtest e ricerca", words: "segnali fattori" }, { to: "/laboratorio", label: "Laboratorio", words: "esperimenti" },
  { to: "/strategia-automatica", label: "Strategia automatica", words: "ordini buy skip" },
  { to: "/fondi", label: "Grandi investitori (13F)", words: "fondi 13f guru" }, { to: "/inserisci-dati", label: "Inserisci dati", words: "bilanci manuale" },
  { to: "/qualita", label: "Qualità dei dati", words: "controlli sec depositi" },
  { to: "/limiti", label: "Metodo e limiti", words: "cautele avvertenze come funziona" },
];
const FN_LABEL: Record<string, string> = Object.fromEntries(commands.functions.map((f) => [f.code, f.label]));

export function rememberCompany(ticker: string) {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[];
    localStorage.setItem(RECENT_KEY, JSON.stringify([ticker, ...list.filter((t) => t !== ticker)].slice(0, 6)));
  } catch { /* niente archiviazione */ }
}
function recentCompanies(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[]; } catch { return []; }
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName ?? "");
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      else if (e.key === "/" && !typing) { e.preventDefault(); setOpen(true); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return [open, setOpen] as const;
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [help, setHelp] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const dq = useDebounced(q.trim(), 120);
  const companies = useQuery({ queryKey: ["search", dq], enabled: dq.length > 0, staleTime: 60_000,
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(dq)}&limit=6`) });
  const parsed = useQuery({ queryKey: ["command", dq], enabled: dq.length > 0, staleTime: 60_000,
    queryFn: () => api<Parsed>(`/command?q=${encodeURIComponent(dq)}`) });
  useEffect(() => { input.current?.focus(); }, []);

  const go = (to: string, ticker?: string) => { if (ticker) rememberCompany(ticker); onClose(); navigate(to); };
  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    const p = dq ? parsed.data : undefined;
    if (p && !p.error) {
      const tks = p.tickers;
      if (p.help) out.push({ key: "help", group: t("Comando"), title: t("Elenco dei comandi"), code: LANG === "en" ? "HELP" : "AIUTO", run: () => setHelp(true) });
      else if (p.follow !== null && tks.length) out.push({
        key: "follow", group: t("Comando"), title: t(p.follow ? "Segui {tickers}" : "Smetti di seguire {tickers}", { tickers: tks.join(", ") }), code: q.toUpperCase(),
        run: () => { void Promise.all(tks.map((x) => api(`/watchlist/${encodeURIComponent(x)}`, { method: p.follow ? "PUT" : "DELETE" })))
          .then(() => qc.invalidateQueries()); go(companyRoute(tks[0]), tks[0]); } });
      else if (p.page === "confronta" && tks.length > 1) out.push({ key: "cmp", group: t("Comando"), title: t("Confronta {tickers}", { tickers: tks.join(", ") }),
        code: q.toUpperCase(), run: () => go(`/confronta?t=${tks.map(encodeURIComponent).join(",")}`) });
      else if (tks.length || p.section) {
        const tk = tks[0] ?? (localStorage.getItem("qe_ultima_azienda") ?? "");
        if (tk) out.push({ key: "co", group: t("Comando"), title: `${tk}${p.names[tk] ? ` · ${p.names[tk]}` : ""}`,
          hint: p.section ? t(FN_LABEL[p.section]) : t("Scheda"), code: q.toUpperCase(), run: () => go(companyRoute(tk, p.section), tk) });
      } else if (p.page !== null && PAGE_ROUTES[p.page] !== undefined) {
        const label = PAGES.find((x) => x.to === PAGE_ROUTES[p.page!])?.label ?? p.page;
        out.push({ key: "page", group: t("Comando"), title: t("Vai a {page}", { page: t(label) }), code: q.toUpperCase(), run: () => go(PAGE_ROUTES[p.page!]) });
      }
    }
    if (dq) {
      for (const r of companies.data?.results ?? []) {
        if (out.some((i) => i.key === "co" && i.title.startsWith(r.ticker))) continue;
        out.push({ key: `c-${r.ticker}`, group: t("Aziende"), title: r.ticker, hint: r.name ?? "", run: () => go(companyRoute(r.ticker), r.ticker) });
      }
      const low = dq.toLowerCase();
      for (const pg of PAGES.filter((x) => x.label.toLowerCase().includes(low) || t(x.label).toLowerCase().includes(low) || x.words.includes(low)).slice(0, 4))
        out.push({ key: `p-${pg.to}`, group: t("Pagine"), title: t(pg.label), run: () => go(pg.to) });
    } else {
      for (const r of recentCompanies()) out.push({ key: `r-${r}`, group: t("Recenti"), title: r, run: () => go(companyRoute(r), r) });
      for (const pg of PAGES.slice(0, 6)) out.push({ key: `p-${pg.to}`, group: t("Pagine"), title: t(pg.label), run: () => go(pg.to) });
    }
    return out;
    // go/qc/navigate sono stabili per l'uso che se ne fa qui
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq, parsed.data, companies.data, q]);
  useEffect(() => { setActive(0); }, [dq]);
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [active]);

  const error = dq && parsed.data?.error && !(companies.data?.results.length) ? parsed.data.error : null;
  let lastGroup = "";
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={t("Cerca e comandi")} onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input ref={input} value={q} placeholder={t("Azienda, ticker o comando (es. MSFT FA, AAPL MSFT, SEGUI KO)")} aria-label={t("Cerca azienda, ticker o comando")}
            role="combobox" aria-expanded={items.length > 0} aria-controls="palette-list" aria-activedescendant={items[active] ? `pi-${items[active].key}` : undefined}
            onChange={(e) => { setQ(e.target.value); setHelp(false); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); items[active]?.run(); }
              else if (e.key === "Escape") { e.preventDefault(); onClose(); }
            }} />
          <kbd>{t("esc")}</kbd>
        </div>
        {help ? (
          <div className="palette-help">
            <p>{t("Scrivi un ticker (o un nome) seguito dalla funzione; più ticker per confrontarli; SEGUI o TOGLI per la tua lista; il nome di una pagina per aprirla.")}</p>
            <div className="palette-codes">{commands.functions.map((f) => <span key={f.code}><code>{f.code}</code>{t(f.label)}</span>)}</div>
          </div>
        ) : (
          <div className="palette-list" id="palette-list" role="listbox" ref={list}>
            {items.map((it, i) => {
              const head = it.group !== lastGroup ? it.group : null;
              lastGroup = it.group;
              return (
                <div key={it.key}>
                  {head && <div className="palette-group">{head}</div>}
                  <button type="button" id={`pi-${it.key}`} role="option" aria-selected={i === active} className="palette-item"
                    onMouseEnter={() => setActive(i)} onClick={() => it.run()}>
                    <span className="t">{it.title}</span>{it.hint && <span className="h">{it.hint}</span>}
                    {it.code && <code>{it.code}</code>}
                    <span className="enter" aria-hidden="true">↵</span>
                  </button>
                </div>
              );
            })}
            {error && <div className="palette-empty">{error}</div>}
            {dq && !error && items.length === 0 && <div className="palette-empty">{t("Cerco…")}</div>}
          </div>
        )}
        <div className="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> {t("per scegliere")}</span><span><kbd>↵</kbd> {t("per aprire")}</span>
          <button type="button" className="linklike" onClick={() => setHelp(!help)}>{help ? t("Torna ai risultati") : t("Elenco dei comandi")}</button></div>
      </div>
    </div>
  );
}
