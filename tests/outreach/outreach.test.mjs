import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  parseCsv, toCsv, writeApproval, readApproval, approvalPayload, verifyApproved,
  pickQueued, sentRecipients, addDays, sha256, OUTREACH_COLS,
} from "../../lib/outreach.mjs";

const tmp = () => mkdtempSync(join(tmpdir(), "outreach-"));

test("CSV round-trips commas, quotes and newlines", () => {
  const cols = ["a", "b"];
  const objs = [{ a: 'x, "y"', b: "line1\nline2" }];
  const parsed = parseCsv(toCsv(cols, objs));
  assert.deepEqual(parsed, [["a", "b"], ['x, "y"', "line1\nline2"]]);
});

test("addDays handles month boundaries", () => {
  assert.equal(addDays("2026-10-25", 7), "2026-11-01");
});

test("approval is immutable: second write fails", () => {
  const dir = tmp();
  const att = join(dir, "att.pdf");
  writeFileSync(att, "pdf-bytes");
  const payload = approvalPayload({
    draft_id: "d1", recipient: "A@X.org", subject: "Hi", body: "Body", attachment: att, approved_at: "2026-10-25",
  });
  assert.equal(payload.recipient, "a@x.org");
  writeApproval(payload, dir);
  assert.throws(() => writeApproval(payload, dir), /EEXIST/);
  assert.ok(existsSync(join(dir, "d1.json")));
});

test("verifyApproved passes for unchanged data and flags each change", () => {
  const dir = tmp();
  const att = join(dir, "att.pdf");
  writeFileSync(att, "pdf-bytes");
  const payload = approvalPayload({
    draft_id: "d2", recipient: "a@x.org", subject: "Hi", body: "Body", attachment: att, approved_at: "2026-10-25",
  });
  const { ref } = writeApproval(payload, dir);
  const approval = readApproval("d2", dir);
  assert.equal(approval.ref, ref);
  const row = { recipient: "a@x.org", subject: "Hi", approval_ref: ref };
  const bytes = readFileSync(att);

  assert.deepEqual(verifyApproved(row, approval, bytes), []);
  assert.match(verifyApproved({ ...row, recipient: "b@x.org" }, approval, bytes).join(), /recipient/);
  assert.match(verifyApproved({ ...row, subject: "Other" }, approval, bytes).join(), /subject/);
  assert.match(verifyApproved({ ...row, approval_ref: "bad" }, approval, bytes).join(), /approval record changed/);
  assert.match(verifyApproved(row, approval, Buffer.from("changed")).join(), /attachment hash/);
  assert.match(verifyApproved(row, approval, null).join(), /attachment missing/);
  assert.match(verifyApproved(row, null, bytes).join(), /no approval record/);
});

test("body tampering is detected", () => {
  const dir = tmp();
  const att = join(dir, "att.pdf");
  writeFileSync(att, "x");
  const payload = approvalPayload({
    draft_id: "d3", recipient: "a@x.org", subject: "S", body: "Original", attachment: att, approved_at: "2026-10-25",
  });
  writeApproval(payload, dir);
  const approval = readApproval("d3", dir);
  approval.payload.body = "Edited";
  const row = { recipient: "a@x.org", subject: "S", approval_ref: approval.ref };
  assert.match(verifyApproved(row, approval, readFileSync(att)).join(), /body hash/);
});

test("pickQueued skips queued initial emails to already-sent recipients", () => {
  const rows = [
    { draft_id: "old", recipient: "a@x.org", email_type: "initial", state: "sent", sent_at: "2026-10-20" },
    { draft_id: "dup", recipient: "A@x.org", email_type: "initial", state: "queued" },
    { draft_id: "new", recipient: "b@x.org", email_type: "initial", state: "queued" },
    { draft_id: "draft", recipient: "c@x.org", email_type: "initial", state: "draft_ready" },
  ];
  assert.deepEqual(pickQueued(rows).map((r) => r.draft_id), ["new"]);
  assert.ok(sentRecipients(rows).has("a@x.org"));
});

test("unused sha256 import sanity", () => {
  assert.equal(sha256(Buffer.from("")).length, 64);
  assert.ok(OUTREACH_COLS.includes("approval_ref"));
});
