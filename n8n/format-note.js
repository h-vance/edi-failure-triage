// Internal-note HTML from a triage result. A port of intercom_bridge.format_note;
// tests/test_n8n_js.py asserts both produce identical HTML for every fixture.
// Pasted into the "Format internal note" Set node as an IIFE expression.

function escapeHtml(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

function formatNote(r) {
  const e = escapeHtml;
  const lines = [
    "<b>EDI triage</b> for " + e(r.transaction_id) + ": tier " + e(r.tier) +
      ", classification " + e(r.classification) + ", mode " + e(r.mode),
    "<b>Hypotheses</b>",
  ];
  for (const h of r.hypotheses || []) {
    lines.push(h.rank + ". [" + e(h.confidence) + "] " + e(h.hypothesis));
    if (h.evidence && h.evidence.length) lines.push("&nbsp;&nbsp;evidence: " + e(h.evidence.join(", ")));
    if (h.check) lines.push("&nbsp;&nbsp;check: " + e(h.check));
  }
  const t = r.ticket_two || {};
  lines.push("<b>Draft customer reply</b> (not sent, send it yourself from the reply box)");
  lines.push(e(r.customer_reply));
  lines.push("<b>Ticket two</b> [" + e(t.kind) + ", effort " + e(t.effort) + "]: " + e(t.title));
  lines.push(e(t.why));
  return lines.map((l) => "<p>" + l + "</p>").join("");
}

if (typeof require !== "undefined" && require.main === module) {
  const r = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
  process.stdout.write(formatNote(r));
}

module.exports = { formatNote, escapeHtml };
