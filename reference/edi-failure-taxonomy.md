# EDI failure taxonomy

How `edi_triage.py` sorts one failed transaction into a failure state, and how to tell neighboring states apart. The source for the status model is Orderful's public documentation. Page names are cited; the text is not copied. All partner names, ids, and payloads in this repo are invented.

## The three status axes

A transaction carries three statuses, one per lifecycle stage. Each has a small, fixed set of values, which is why classification is code and not a model call.

| Axis | Values | Stage |
|---|---|---|
| `validationStatus` | `PROCESSING`, `VALID`, `INVALID`, or null when the transaction was never created | Did the payload pass the guideline? |
| `deliveryStatus` | `PENDING`, `SENT`, `DELIVERED`, `FAILED` | Did the receiver's channel take it? |
| `acknowledgmentStatus` | `NOT_ACKNOWLEDGED`, `ACCEPTED`, `REJECTED`, `OVERDUE`, `ACCEPTED_WITH_ERRORS` | What did the receiver's 997 say? |

Doc pages: "Transaction statuses", "Transaction delivery success and failure", "Acknowledge a Transaction".

`classify()` walks the stages in order. The first failed stage wins, because later stages cannot have run. A transaction that is `INVALID` is always an invalid case even if `deliveryStatus` is `PENDING`, because delivery never started.

## Where errors surface

The `errors[].source` field says where the error text came from. It is the second input to classification.

| Source | Where the engineer sees it | Doc page |
|---|---|---|
| `api` | A non-200 response when the transaction was created | "Unprocessed Transactions" |
| `guideline` | The Validation Errors tab: mandatory segment or element missing, code not allowed, data type mismatch | "Configure Guideline Requirements", "Fix an Invalid Transaction" |
| `rule` | The rules engine: a lookup that returned nothing, a date that would not format | "Use the Rules Engine" |
| `mapping` | The Validation Errors tab, on the transform between the customer's shape and the partner's | "Fix an Invalid Transaction" |
| `channel` | The channel page: AS2 MDN errors, HTTP retries exhausted | "AS2 Troubleshooting", "Transaction delivery success and failure" |
| `ack` | The acknowledgment endpoint's `errors[]` with path, code, and message | "Acknowledge a Transaction" |

## Four families, eight leaves

### Family 1: never created

**`unprocessed`**. `validationStatus` is null. The API refused the payload before a transaction existed. Typical causes: unknown sender or receiver id, no partnership or relationship between them, or a payload that does not match the schema. Whose side: usually the customer's setup, sometimes the platform's partner configuration. Tell it apart: there is no transaction id from the platform, only the customer's own reference and an HTTP status. Doc page: "Unprocessed Transactions".

### Family 2: invalid

All three leaves have `validationStatus=INVALID`. The error source splits them. Rules run before validation, so a rule can be the cause of an Invalid, and that is why `rule` outranks `guideline` when both appear.

**`invalid.rule`**. An error with `source=rule`. Typical causes: a lookup table with no matching row, a date function fed a shape it did not expect, a rule written for one partner running on another. Whose side: the platform configuration, which support owns. Tell it apart from `invalid.guideline`: the message names a function or a table, not a segment requirement. Doc page: "Use the Rules Engine".

**`invalid.mapping`**. An error with `source=mapping` and no rule error. Typical causes: a required output value the mapping could not produce, a path that does not exist in the source shape, a transform that fails on an edge case. Whose side: the platform configuration. Tell it apart from `invalid.guideline`: the message is about the transform, not the guideline. Doc page: "Fix an Invalid Transaction".

**`invalid.guideline`**. Any other `INVALID`. Typical causes: mandatory segment or element missing, a code not in the allowed list, a data type mismatch such as a date not in CCYYMMDD. Whose side: the customer's export, or the guideline if the requirement is wrong. Tell it apart from the other two: the message cites a segment or element and a requirement. Doc pages: "Configure Guideline Requirements", "Configure Conditional Guideline Requirements", "Fix an Invalid Transaction".

### Family 3: delivery

Both leaves have `validationStatus=VALID`.

**`delivery.failed`**. `deliveryStatus=FAILED`. The channel was reached and refused the transaction after retries. Typical causes on AS2: an MDN with errors, an unverifiable signature, a decryption failure, an expired certificate on either side. On HTTP: the endpoint kept returning errors through the retry budget. Whose side: usually the receiving partner's channel, sometimes the platform's copy of their certificate. Tell it apart from `delivery.pending`: there is an error from the channel and an attempt count. Doc pages: "AS2 Troubleshooting", "Transaction delivery success and failure".

**`delivery.pending`**. `deliveryStatus=PENDING` with no channel error. The transaction is valid but has not been handed to the receiver's channel. Typical causes: the channel is paused or misconfigured, a queue is backed up, the receiver's relationship has no delivery channel. Whose side: the platform, and it is time-sensitive. Tell it apart from `delivery.failed`: no error, no attempts, just elapsed time. Doc page: "Transaction delivery success and failure".

### Family 4: acknowledgment

Both leaves have `validationStatus=VALID` and `deliveryStatus=DELIVERED`. The platform did its job. The 997 is the receiver's opinion.

**`ack.rejected`**. `acknowledgmentStatus` is `REJECTED` or `ACCEPTED_WITH_ERRORS`. The partner's system took the file and said no. Typical causes: a value their backend does not know (a ship-to, an item, a vendor number), a structure their translator rejects, a business rule on their side. Whose side: the trading partner's, or the customer's data if the value is genuinely wrong. Tell it apart from `invalid.guideline`: the platform said Valid; only the partner objected. Doc page: "Acknowledge a Transaction".

**`ack.overdue`**. `acknowledgmentStatus=OVERDUE`. Delivered, and no 997 within the expected window. Typical causes: the partner does not send 997s promptly, or at all, for this document; their translator is down; the acknowledgment relationship is not configured. Whose side: the partner's. Tell it apart from `delivery.pending`: delivery is done. Tell it apart from `ack.rejected`: there is no error, only silence. Doc page: "Acknowledge a Transaction".

### The ninth value

**`healthy`** is what `classify()` returns when no axis shows a failure. It is not a failure state. It usually means the customer is reading one status as all three. The right output is a short explanation, not a ticket.

## Integration paths (tiers)

`detect_tier()` reads the first lines of the payload. The tier changes where the fix lives.

| Tier | How it is detected | Where a fix usually lives |
|---|---|---|
| `mosaic` | JSON with a business-friendly `type` field | The customer's integration code, or the platform's mapping |
| `orderful_json` | JSON with camelCase segment names and a `transactionType` field | The customer's export, or a platform rule |
| `x12_passthrough` | Raw text starting with `ISA` | The customer's own translator. The platform validates but does not transform |
| `mft_file` | Non-JSON text such as CSV or XML | The file layout agreed with the partner |
| `unknown` | Empty or unrecognized | Ask for the payload |

Doc page: "Integration Overview".

## Quick decision table

| Statuses | Error source | Leaf |
|---|---|---|
| validation null | `api` | `unprocessed` |
| `INVALID` | `rule` (any) | `invalid.rule` |
| `INVALID` | `mapping`, no `rule` | `invalid.mapping` |
| `INVALID` | anything else | `invalid.guideline` |
| `VALID` / `FAILED` | `channel` | `delivery.failed` |
| `VALID` / `PENDING` | none | `delivery.pending` |
| `VALID` / `DELIVERED` / `REJECTED` or `ACCEPTED_WITH_ERRORS` | `ack` | `ack.rejected` |
| `VALID` / `DELIVERED` / `OVERDUE` | none | `ack.overdue` |
| `VALID` / `DELIVERED` / `ACCEPTED` | none | `healthy` |

If the statuses and the error source disagree with this table, the skill stops and escalates. That is rule 4 in `skills/edi-failure-triage/SKILL.md`.
