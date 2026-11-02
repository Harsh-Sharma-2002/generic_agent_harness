import { test } from "node:test";
import assert from "node:assert/strict";
import { extractBody } from "../../scripts/send-email.mjs";

test("extractBody returns text after the ## Body heading, unchanged", () => {
  const draft = "# Draft x\n- subject: s\n\n## Body\nHi Neel,\n\nLine one.  \nBest,\nHarsh\n";
  assert.equal(extractBody(draft), "Hi Neel,\n\nLine one.  \nBest,\nHarsh");
});

test("extractBody throws when there is no Body section", () => {
  assert.throws(() => extractBody("no body here"), /Body/);
});
