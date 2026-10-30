import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  cardBody, category, citation, classify, customNc, detectLicenceText, entryLicence, normalise, sourceIds, validId,
} from "../src/check";

// Written by `python -m tests.parity` from the Python generator, which is the reference.
const P = JSON.parse(readFileSync(fileURLToPath(new URL("../../tests/fixtures/parity.json", import.meta.url)), "utf8"));

describe("live check matches the Python generator", () => {
  it("normalise", () => { for (const [v, want] of P.normalise) expect(normalise(v), JSON.stringify(v)).toEqual(want); });
  it("category", () => { for (const [v, want] of P.category) expect(category(v), String(v)).toEqual(want); });
  it("classify", () => { for (const [args, want] of P.classify) expect(classify(args[0], args[1], args[2]), JSON.stringify(args)).toEqual(want); });
  it("detect licence text", () => {
    for (const [t, want] of P.detect_licence_text) expect(detectLicenceText(t), String(t).slice(0, 60)).toEqual(want);
  });
  it("custom non-commercial wording", () => {
    for (const [args, want] of P.custom_nc) expect(customNc(args[0], args[1], args[2]), JSON.stringify(args)).toEqual(want);
  });
  it("source ids", () => { for (const [args, want] of P.source_ids) expect(sourceIds(args[0], args[1])).toEqual(want); });
  it("entry licence", () => { for (const [args, want] of P.entry_licence) expect(entryLicence(args[0], args[1])).toEqual(want); });
  it("valid ids", () => { for (const [v, want] of P.valid_id) expect(validId(v), v).toEqual(want); });
  it("card body and citation", () => {
    for (const c of P.cards) {
      expect(cardBody(c.text), c.name).toEqual(c.body);
      expect(citation(c.body), c.name).toEqual(c.citation);
    }
  });
});
