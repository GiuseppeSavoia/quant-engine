import { describe, expect, it } from "vitest";
import commands from "./commands.json";
import { companyRoute, FUNCTION_PARAMS, PAGE_ROUTES } from "./routes";

describe("destinazioni dei comandi", () => {
  it("ogni funzione della scheda del motore ha un parametro nel web", () => {
    const missing = commands.functions.map((f) => f.code).filter((c) => !(c in FUNCTION_PARAMS));
    expect(missing).toEqual([]);
  });
  it("ogni pagina del motore ha un indirizzo nel web", () => {
    const missing = commands.pages.map((p) => p.page).filter((p) => !(p in PAGE_ROUTES));
    expect(missing).toEqual([]);
  });
  it("scheda con funzione", () => {
    expect(companyRoute("MSFT", "FA")).toBe("/azienda/MSFT?fn=bilanci");
    expect(companyRoute("BRK-B", "PRV")).toBe("/azienda/BRK-B");
  });
});
