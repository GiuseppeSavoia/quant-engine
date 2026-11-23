import { describe, expect, it } from "vitest";
import cases from "./fmt_cases.json";
import { fmt, type FmtOptions, type Kind } from "./fmt";

// Casi generati da Python (modules/formats.fmt) con tools/export_web_shared.py: la porta deve dare lo stesso testo.
const value = (v: unknown) => (v === "nan" ? Number.NaN : v === "inf" ? Number.POSITIVE_INFINITY : v);

describe("fmt come in Python", () => {
  it(`riproduce tutti i ${cases.length} casi`, () => {
    const wrong = (cases as { v: unknown; kind: Kind; opt: FmtOptions; out: string }[])
      .map((c) => ({ ...c, got: fmt(value(c.v), c.kind, c.opt) }))
      .filter((c) => c.got !== c.out);
    expect(wrong.slice(0, 10)).toEqual([]);
  });
});
