// Turns an Intercom conversation (display_as=plaintext) into the FailedTransaction
// body that POST /triage expects, plus the list of required fields it could not find.
// This exact function body is pasted into the "Extract triage inputs" Set node as an
// IIFE expression. Keep it dependency-free and ES2019-safe (n8n expression sandbox).
// Self-check: `pytest tests/test_n8n_js.py` (generates the seeded tickets, then runs this file).

function extractTriageInputs(conv) {
  const parts = ((conv.conversation_parts || {}).conversation_parts || [])
    .filter((p) => p.author && p.author.type === "user" && p.body)
    .map((p) => p.body);
  const text = [(conv.source || {}).body || ""].concat(parts).join("\n\n").trim();

  const pick = (re) => { const m = text.match(re); return m ? m[1] : undefined; };
  const upper = (v) => (v ? v.toUpperCase() : undefined);

  const id = pick(/\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/i);
  const docType = pick(/\b(\d{3}_[A-Z_]+)\b/) || pick(/\bdocument\s+(\S+?)[,.\s]/i);
  const senderId = pick(/\bsender\s+([A-Z0-9_-]+)/i);
  const receiverId = pick(/\breceiver\s+([A-Z0-9_-]+)/i);
  const direction = (pick(/\bdirection\s+(in|out)\b/i) || "in").toLowerCase();
  const stream = (pick(/\bstream\s+(test|live)\b/i) || "live").toLowerCase();

  const VALID = ["PROCESSING", "VALID", "INVALID"];
  const DELIV = ["PENDING", "SENT", "DELIVERED", "FAILED"];
  const ACK = ["NOT_ACKNOWLEDGED", "ACCEPTED_WITH_ERRORS", "ACCEPTED", "REJECTED", "OVERDUE"];
  const status = (label, allowed) => {
    const v = upper(pick(new RegExp("\\b" + label + "\\s+([A-Z_]+|nothing)", "i")));
    return allowed.indexOf(v) >= 0 ? v : undefined;
  };
  let validationStatus = status("validation", VALID);
  const neverCreated = /validation\s+nothing|never created/i.test(text);
  const deliveryStatus = status("delivery", DELIV);
  const acknowledgmentStatus = status("acknowledgment", ACK);

  const errors = [];
  const errRe = /^\s*Error:\s*(.+)$/gim;
  let m;
  while ((m = errRe.exec(text)) !== null) errors.push({ message: m[1].trim(), source: "guideline" });

  const missing = [];
  if (!id) missing.push("transaction id");
  if (!docType) missing.push("document type (e.g. 850_PURCHASE_ORDER)");
  if (!senderId) missing.push("sender id");
  if (!receiverId) missing.push("receiver id");

  const tx = { id, ediTransactionType: docType, senderId, receiverId, direction, stream, errors, customer_note: text };
  if (validationStatus) tx.validationStatus = validationStatus;
  else if (neverCreated) tx.validationStatus = null;
  if (deliveryStatus) tx.deliveryStatus = deliveryStatus;
  if (acknowledgmentStatus) tx.acknowledgmentStatus = acknowledgmentStatus;
  return { tx, missing, conversation_id: String(conv.id || "") };
}

if (typeof require !== "undefined" && require.main === module) {
  const assert = require("assert");
  const seeds = require(process.argv[2] || "/tmp/seeds.json");
  for (const [name, s] of Object.entries(seeds)) {
    const out = extractTriageInputs({ id: "1", source: { body: s.text } });
    assert.deepStrictEqual(out.missing, [], name + " missing " + out.missing);
    for (const k of ["id", "ediTransactionType", "senderId", "receiverId", "direction", "stream"])
      assert.strictEqual(out.tx[k], s.expect[k], name + " " + k);
    assert.strictEqual(out.tx.validationStatus, s.expect.validationStatus, name + " validationStatus");
    assert.strictEqual(out.tx.deliveryStatus, s.expect.deliveryStatus || "PENDING", name + " deliveryStatus");
    assert.strictEqual(out.tx.acknowledgmentStatus, s.expect.acknowledgmentStatus || "NOT_ACKNOWLEDGED", name + " ack");
    assert.strictEqual(out.tx.errors.length, s.n_errors, name + " errors");
  }
  const vague = extractTriageInputs({ id: "2", source: { body: "<p>my PO is stuck, help</p>" },
    conversation_parts: { conversation_parts: [{ author: { type: "admin" }, body: "sender X receiver Y" }] } });
  assert.deepStrictEqual(vague.missing, ["transaction id", "document type (e.g. 850_PURCHASE_ORDER)", "sender id", "receiver id"]);
  console.log("ok: " + Object.keys(seeds).length + " seeded tickets + 1 vague ticket");
}

module.exports = { extractTriageInputs };
