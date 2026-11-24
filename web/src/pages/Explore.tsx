// Esplora: filtri sulle metriche del catalogo, sul giudizio e sui punteggi dei pilastri; tabella dei risultati con le
// colonne scelte; ricerche pronte e salvate. Il filtro lo fa il motore (POST /explore, servizio explore: stesse regole
// della webapp Streamlit, un dato mancante non passa mai): qui solo i controlli e la tabella.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Spark } from "../components/Spark";
import { draftsFrom, groupMetrics, parseDrafts, RuleRows, type RuleDraft } from "../components/rules";
import { VerdictBadge, VERDICT_STYLE } from "../components/verdict";
import { Card, Empty, Notice, Num, Segmented, Skeleton } from "../components/ui";
import { api, ApiError, type ExploreCatalog, type ExploreResult } from "../lib/api";
import { fmt, kindOfMetric } from "../lib/fmt";
import { t, tn } from "../lib/i18n";

const SCORE_STEPS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

export function ExplorePage() {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["explore-catalog"], queryFn: () => api<ExploreCatalog>("/explore/catalog") });
  const metrics = useMemo(() => new Map((catalog.data?.metrics ?? []).map((m) => [m.key, m])), [catalog.data]);
  const [universe, setUniverse] = useState("S&P 500");
  const [rules, setRules] = useState<RuleDraft[]>([]);
  const [nextId, setNextId] = useState(1);
  const [sectors, setSectors] = useState<string[]>([]);
  const [verdicts, setVerdicts] = useState<string[]>([]);
  const [minScores, setMinScores] = useState<Record<string, number>>({});
  const [text, setText] = useState("");
  const [sort, setSort] = useState("auto");
  const [columns, setColumns] = useState<string[] | null>(null);
  const [choice, setChoice] = useState("");
  const [saveName, setSaveName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const navigate = useNavigate();

  const groups = useMemo(() => groupMetrics(catalog.data?.metrics ?? []), [catalog.data]);

  // regole valide nelle unità della metrica; le altre si segnalano accanto alla riga
  const parsed = parseDrafts(rules, metrics);
  const validRules = parsed.flatMap((p) => (p.rule ? [p.rule] : []));
  const body = JSON.stringify({ universe, rules: validRules, sectors, verdicts, min_scores: minScores, text, sort,
    columns: columns ?? catalog.data?.default_columns ?? null });
  const debounced = useDebounced(body, 300);
  const result = useQuery({
    queryKey: ["explore", debounced], enabled: !!catalog.data,
    queryFn: () => api<ExploreResult>("/explore", { method: "POST", body: debounced }),
    placeholderData: keepPreviousData, retry: false,
  });

  const follow = useMutation({
    mutationFn: ({ t, on }: { t: string; on: boolean }) => api(`/watchlist/${encodeURIComponent(t)}`, { method: on ? "PUT" : "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["explore"] }); qc.invalidateQueries({ queryKey: ["my-companies"] }); },
  });
  const save = useMutation({
    mutationFn: () => api(`/screens/${encodeURIComponent(saveName.trim())}`, { method: "PUT",
      body: JSON.stringify({ rules: validRules, sectors, verdicts, min_scores: minScores }) }),
    onSuccess: () => { setSaveName(""); qc.invalidateQueries({ queryKey: ["explore-catalog"] }); },
  });
  const remove = useMutation({
    mutationFn: (name: string) => api(`/screens/${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => { setChoice(""); qc.invalidateQueries({ queryKey: ["explore-catalog"] }); },
  });

  if (catalog.isPending) return <div className="page"><h1>{t("Esplora")}</h1><Skeleton height={240} /></div>;
  if (catalog.isError) return <div className="page"><h1>{t("Esplora")}</h1><Empty what={t("Esplora non disponibile")} why={catalog.error.message} /></div>;
  const cat = catalog.data;
  const preset = cat.presets.find((p) => p.name === choice);
  const saved = cat.saved.find((s) => `salvata:${s.name}` === choice);

  const load = () => {
    if (preset) { setRules(draftsFrom(preset.rules, metrics, nextId)); setNextId(nextId + preset.rules.length); setSectors([]); setVerdicts([]); setMinScores({}); }
    else if (saved) {
      setRules(draftsFrom(saved.rules, metrics, nextId)); setNextId(nextId + saved.rules.length);
      setSectors(saved.sectors.filter((s) => cat.sectors.includes(s)));
      setVerdicts(saved.filters.verdicts ?? []); setMinScores(saved.filters.min_scores ?? {});
    }
  };
  const addRule = () => { setRules([...rules, { id: nextId, metric: "roic", op: ">=", text: "" }]); setNextId(nextId + 1); };
  const shownCols = columns ?? cat.default_columns;
  const r = result.data;
  const singleCurrency = r && r.currencies.length <= 1 ? r.currencies[0] : undefined;
  const hasFilters = validRules.length > 0 || verdicts.length > 0 || Object.values(minScores).some((v) => v > 0);

  return (
    <div className="page">
      <h1>{t("Esplora")}</h1>
      <p className="lead">{t("Cerca tra le aziende con le metriche del catalogo (ultimi 12 mesi, prezzi dell'ultima chiusura). Ogni condizione va rispettata; un'azienda senza il dato non passa.")}</p>

      <Card title={t("Filtri")}>
        <div className="filters">
          <div className="row">
            <Segmented label={t("Universo")} value={universe} onChange={setUniverse} options={cat.universes.map((u) => ({ value: u, label: t(u) }))} />
            <select className="select" value={choice} onChange={(e) => setChoice(e.target.value)} aria-label={t("Ricerca pronta o salvata")}>
              <option value="">{t("Ricerca pronta o salvata…")}</option>
              <optgroup label={t("Ricerche pronte")}>{cat.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}</optgroup>
              {cat.saved.length > 0 && <optgroup label={t("Le tue ricerche")}>{cat.saved.map((s) => <option key={s.name} value={`salvata:${s.name}`}>{s.name}</option>)}</optgroup>}
            </select>
            <button type="button" className="btn" disabled={!preset && !saved} onClick={load}>{t("Carica")}</button>
            {saved && <button type="button" className="btn ghost" onClick={() => remove.mutate(saved.name)}>{t("Elimina")}</button>}
          </div>
          {preset && <p className="honest" style={{ margin: 0 }}>{preset.description}</p>}
          {universe !== "S&P 500" && <Notice tone="info">{t("Aziende europee: bilanci annuali ESEF dal 2021 (niente trimestri), valori nella valuta di quotazione.")}</Notice>}

          <div className="row">
            <details className="dropdown">
              <summary className="btn">{sectors.length ? t("Settori: {n}", { n: sectors.length }) : t("Tutti i settori")}</summary>
              <div className="menu">
                {cat.sectors.map((s) => (
                  <label key={s} className="check"><input type="checkbox" checked={sectors.includes(s)}
                    onChange={(e) => setSectors(e.target.checked ? [...sectors, s] : sectors.filter((x) => x !== s))} />{s}</label>
                ))}
                {sectors.length > 0 && <button type="button" className="btn ghost" onClick={() => setSectors([])}>{t("Tutti i settori")}</button>}
              </div>
            </details>
            <div className="pills" role="group" aria-label={t("Giudizio")}>
              {cat.verdicts.map((v) => {
                const on = verdicts.includes(v.value);
                const [sym, , short] = VERDICT_STYLE[v.value] ?? ["●", "neutral", v.label];
                return (
                  <button key={v.value} type="button" className={`pill${on ? " on" : ""}`} aria-pressed={on} title={v.label}
                    onClick={() => setVerdicts(on ? verdicts.filter((x) => x !== v.value) : [...verdicts, v.value])}>
                    {sym} {t(short)} · {r?.verdict_counts[v.value] ?? 0}
                  </button>
                );
              })}
            </div>
          </div>
          <p className="honest" style={{ margin: 0 }}>{t("Il giudizio applica i criteri fissi della scheda:")} <Link to="/limiti">{t("come funziona e limiti")} ›</Link></p>

          <div className="sub">{t("Condizioni")}</div>
          <RuleRows drafts={rules} setDrafts={setRules} metrics={metrics} groups={groups} ops={cat.ops} />
          <div className="row">
            <button type="button" className="btn" onClick={addRule}>＋ {t("Aggiungi una condizione")}</button>
            {rules.length > 0 && <button type="button" className="btn ghost" onClick={() => setRules([])}>{t("Togli tutte")}</button>}
          </div>

          <div className="sub">{t("Punteggi minimi del giudizio (0 = nessun filtro)")}</div>
          <div className="row">
            {cat.pillars.map((p) => (
              <label key={p.key} className="score" title={p.help}>
                <span>{p.label}</span>
                <select className="select" value={minScores[p.key] ?? 0} onChange={(e) => setMinScores({ ...minScores, [p.key]: Number(e.target.value) })}>
                  {SCORE_STEPS.map((s) => <option key={s} value={s}>{s === 0 ? t("qualsiasi") : `≥ ${fmt(s, "score", { digits: 1 })}`}</option>)}
                </select>
              </label>
            ))}
          </div>
        </div>
      </Card>

      <Card title={r ? tn(r.count, "{n} azienda su {total} · ordinate per {by}", "{n} aziende su {total} · ordinate per {by}", { total: r.total, by: r.sorted_by }) : t("Risultati")}
        right={result.isFetching ? t("aggiorno…") : r && r.shown < r.count ? t("prime {n}", { n: r.shown }) : undefined}>
        <div className="row results-tools">
          <div className="search"><input value={text} onChange={(e) => setText(e.target.value)} placeholder={t("Cerca nei risultati: ticker o nome")} aria-label={t("Cerca nei risultati")} style={{ paddingLeft: 12 }} /></div>
          <select className="select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label={t("Ordina per")}>
            {cat.sorts.map((s) => <option key={s.value} value={s.value}>{t("Ordina: {label}", { label: s.label })}</option>)}
          </select>
          <details className="dropdown">
            <summary className="btn">{t("Colonne ({n})", { n: shownCols.length })}</summary>
            <div className="menu wide">
              {groups.map(([g, ms]) => (
                <fieldset key={g}><legend>{g}</legend>
                  {ms.map((m) => (
                    <label key={m.key} className="check"><input type="checkbox" checked={shownCols.includes(m.key)}
                      onChange={(e) => setColumns(e.target.checked ? [...shownCols, m.key] : shownCols.filter((c) => c !== m.key))} />{m.label}</label>
                  ))}
                </fieldset>
              ))}
              <button type="button" className="btn ghost" onClick={() => setColumns(null)}>{t("Colonne predefinite")}</button>
            </div>
          </details>
        </div>
        {r && r.rules.length > 0 && <p className="honest" style={{ margin: "0 0 8px" }}>{r.rules.join(" · ")}</p>}
        {picked.length > 0 && (
          <div className="row selection-bar">
            <span>{tn(picked.length, "{n} selezionata", "{n} selezionate")}: {picked.join(", ")}</span>
            <button type="button" className="btn primary" disabled={picked.length < 2} onClick={() => navigate(`/confronta?t=${picked.join(",")}`)}>
              {picked.length < 2 ? t("Confronta (scegline almeno 2)") : t("Confronta le {n}", { n: picked.length })}
            </button>
            <button type="button" className="btn ghost" onClick={() => setPicked([])}>{t("Annulla")}</button>
          </div>
        )}
        {result.isError ? (
          <Notice tone="neg" title={t("Filtri non validi:")}>{result.error instanceof ApiError ? result.error.message : String(result.error)}</Notice>
        ) : !r ? <Skeleton height={240} /> : r.rows.length === 0 ? (
          <Empty what={t("Nessuna azienda passa i filtri")} why={t("Allenta una condizione, abbassa i punteggi minimi o scegli un altro giudizio.")} />
        ) : (
          <div className="table-wrap tall">
            <table className="dense explore">
              <thead>
                <tr>
                  <th aria-label={t("Confronta")} /><th aria-label={t("Seguita")} /><th>{t("Ticker")}</th><th>{t("Azienda")}</th><th>{t("Giudizio")}</th><th>{t("Settore")}</th>
                  {!singleCurrency && <th>{t("Valuta")}</th>}
                  <th className="n">{t("Prezzo")}</th><th>{t("1 anno")}</th>
                  {r.columns.map((c) => <th key={c.key} className="n" title={c.help || c.label}>{c.label}</th>)}
                  <th className="n" title={t("Tuo fair value / prezzo − 1 (solo se l'hai salvato)")}>{t("Margine sul tuo FV")}</th>
                </tr>
              </thead>
              <tbody>
                {r.rows.map((row) => (
                  <tr key={row.ticker} className={picked.includes(row.ticker) ? "picked" : undefined}>
                    <td>
                      <input type="checkbox" aria-label={t("Scegli {ticker} per il confronto", { ticker: row.ticker })} checked={picked.includes(row.ticker)}
                        disabled={!picked.includes(row.ticker) && picked.length >= 5}
                        onChange={(e) => setPicked(e.target.checked ? [...picked, row.ticker] : picked.filter((x) => x !== row.ticker))} />
                    </td>
                    <td>
                      <button type="button" className={`star${row.followed ? " on" : ""}`} aria-pressed={row.followed}
                        title={row.followed ? t("Smetti di seguire {ticker}", { ticker: row.ticker }) : t("Segui {ticker}", { ticker: row.ticker })}
                        onClick={() => follow.mutate({ t: row.ticker, on: !row.followed })}>{row.followed ? "★" : "☆"}</button>
                    </td>
                    <td><Link to={`/azienda/${encodeURIComponent(row.ticker)}`}><b>{row.ticker}</b></Link></td>
                    <td className="nm">{row.name}</td>
                    <td>{row.category ? <VerdictBadge category={row.category} label={row.verdict ?? undefined} /> : <span className="na">–</span>}</td>
                    <td className="nm">{row.sector}</td>
                    {!singleCurrency && <td>{row.currency}</td>}
                    <td className="n">{fmt(row.price, "price")}</td>
                    <td className="spark-cell"><Spark values={row.spark} /></td>
                    {r.columns.map((c) => (
                      <td key={c.key} className="n">
                        <Num v={row.values[c.key]} kind={kindOfMetric(c.fmt)} currency={c.fmt === "money" ? singleCurrency ?? row.currency : undefined} />
                      </td>
                    ))}
                    <td className="n"><Num v={row.my_margin} kind="pct" signed /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={t("Salva questa ricerca")} right={t("condizioni, settori, giudizi e punteggi minimi")}>
        <div className="row">
          <div className="search" style={{ maxWidth: 360 }}>
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder={t("es. Le mie qualità a sconto")} aria-label={t("Nome della ricerca")} style={{ paddingLeft: 12 }} />
          </div>
          <button type="button" className="btn primary" disabled={!saveName.trim() || !hasFilters || save.isPending} onClick={() => save.mutate()}>{t("Salva")}</button>
          {!hasFilters && <span className="honest">{t("Serve almeno una condizione, un giudizio o un punteggio minimo.")}</span>}
          {save.isSuccess && <span className="pos">{t("Salvata: la ritrovi in «Ricerca pronta o salvata».")}</span>}
          {save.isError && <span className="neg">{save.error.message}</span>}
        </div>
      </Card>
    </div>
  );
}
