---
name: edi-failure-triage
description: Triage one failed EDI transaction. Classify the failure state, rank the likely causes with evidence, draft the customer reply, and propose the ticket-two fix that prevents the next ticket.
---

# EDI failure triage

## When to use this skill

Use this skill when a support engineer describes one failed or stuck EDI transaction. Typical prompts:

- "My 850 is stuck"
- "Transaction shows Invalid"
- "Partner rejected our 856"
- "Delivery stuck in Pending"
- "Mapping failed" or "missing required value"
- "Missing item lookup" or "lookup returned nothing"
- "We got no 997 back from the partner"
- "Orderful shows Valid and Delivered but the partner says they never got it"

Do not use it for questions about a whole partner onboarding, a guideline redesign, or a bulk reprocessing job. Those are not one transaction.

## Inputs the skill needs

If the ticket is in Intercom, start there. Use the Intercom MCP tools (search conversations, get a conversation) to read the customer's message, or run `python intercom_bridge.py read <conversation_id>`. Pull the transaction id, document type, statuses, error text, and what changed from the customer's own words. Anything the ticket does not say is a question back to the customer, not a guess.

Gather these before running anything. Each maps to a field on `FailedTransaction` in `edi_triage.py`.

| Input | Field | Notes |
|---|---|---|
| Transaction id | `id` | Placeholder ids only in examples and fixtures |
| Document type | `ediTransactionType` | For example `850_PURCHASE_ORDER`, `856_SHIP_NOTICE`, `810_INVOICE` |
| Sender and receiver | `senderId`, `receiverId` | Partner ids as shown on the transaction |
| Direction and stream | `direction`, `stream` | `in` or `out`; `test` or `live` |
| The three statuses | `validationStatus`, `deliveryStatus`, `acknowledgmentStatus` | Exact values from the transaction. `validationStatus` is null when the transaction was never created |
| Exact error text | `errors[]` with `path`, `code`, `message`, `source` | `source` is where it surfaced: `guideline`, `rule`, `mapping`, `channel`, `ack`, or `api` |
| Payload snippet | `snippet` | First lines of the payload. The tool reads the integration path from it (Mosaic, Orderful JSON, X12 passthrough, file over MFT) |
| What changed recently | `context` | ERP upgrade, new ship-to, certificate dates, counts, timestamps. Only what the customer or the platform actually reported |
| The customer's own words | `customer_note` | Verbatim, trimmed |

If an input is missing, ask for it. Do not fill it in.

## The recipe

1. Read the ticket (Intercom) or the engineer's description. Write the inputs to a JSON file shaped like the files under `fixtures/`. Leave out any field you do not have. Never guess a status or an error message.

2. Classify. Either call the `triage_edi_transaction` MCP tool with the JSON as `tx` (the server must be running: `BEDROCK_MOCK=true python server.py`, and `.mcp.json` points Claude Code at it), or run the CLI:

   ```bash
   python edi_triage.py path/to/transaction.json
   ```

   Read `tier` and `classification` from the result. Both come from code, not the model.

3. Check the classification against `reference/edi-failure-taxonomy.md`. The three statuses and the error `source` must agree with the leaf. If they point at different leaves, or the error text does not match any documented failure, stop and escalate with what you have.

4. Present two or three hypotheses, ranked. Each one names the field path it rests on (`errors[0].message`, `context.partner_cert_not_after`) and one concrete thing to check. Show the reasoning, not just the answer.

5. Draft the customer reply. Say what happened, whose side the fix is on, what they should do, and whether to resend. Three to five sentences. Crisp and kind. When the fix belongs to the trading partner, say so plainly and offer to contact them together.

6. Propose ticket two: the one rule, alert, knowledge article, mapping test, or partner contact that would stop the next ticket of this kind. One per run, with effort S, M, or L.

7. Stop. Present the reply and the ticket-two proposal and wait. Nothing is sent, resent, filed, or changed until the engineer says so.

8. After the engineer approves, and only then, leave the result where the team works. Post it as an internal note on the Intercom conversation:

   ```bash
   python edi_triage.py path/to/transaction.json --json > result.json
   python intercom_bridge.py note <conversation_id> result.json
   ```

   The note is for the team. The engineer sends the customer reply from Intercom themselves.

## Behavior rules

1. Propose before acting. Every run ends in a proposal, never in an action.
2. Never send a customer reply, resend a transaction, file a ticket, or change a rule, mapping, or channel without explicit approval in the conversation.
3. Never invent an input. A missing status, error, or timestamp is a question for the engineer, not a guess.
4. Reject ambiguous matches. If the evidence fits two leaves, or none, escalate with the evidence laid out. Do not pick the closest one.
5. Cite a field path for every claim. A hypothesis with no evidence path is not a hypothesis.
6. One run, one transaction, one ticket-two proposal. Do not bundle fixes for several partners or several failure kinds into one run.
7. Say when the fix is on the trading partner's side, and say it in the customer reply too. A rejected 997 or a missing certificate is theirs to fix, not the customer's.
8. Mock mode is the default. Call live Bedrock only when the engineer asks for it.
9. Placeholder ids and invented partners only in examples, fixtures, and this skill. No real customer names, ids, SKUs, or payloads.
10. Reprocessing is not automated. The tool tells the engineer whether to resend; a person does the resend.
11. Intercom writes are internal notes only. Never reply to the customer, assign, tag, close, or snooze a conversation from the skill.

## Reference material

- `reference/edi-failure-taxonomy.md`: the four failure families, the eight leaves, how to tell them apart, and which doc page covers each
- `skills/edi-failure-triage/examples.md`: three annotated sessions, one per family
- `fixtures/`: ten invented transactions, one per leaf, with the canned mock result each returns
- `intercom_bridge.py`: read a conversation, post an internal note, seed a sandbox conversation from a fixture
- `README.md`: how to run the CLI, the API, the MCP server, the console, and the Intercom setup
