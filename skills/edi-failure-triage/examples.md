# EDI failure triage: example sessions

Three annotated sessions, one per failure family that has an error to read. Every id, partner, and payload is invented. Each session uses one file under `fixtures/`, and the tool output shown is that fixture's mock result, so the sessions replay exactly in `BEDROCK_MOCK=true`.

## 1. Invalid: guideline data type

**Engineer says:** "Every PO to Acme has been Invalid since the weekend. Transaction 0192f3a1-0000-7000-8000-000000000003."

**Inputs gathered:** id, `850_PURCHASE_ORDER`, inbound live, statuses `INVALID` / `PENDING` / `NOT_ACKNOWLEDGED`, one error from the guideline validator on `/dateTimeReference/0/date` with code `DATA_TYPE_DT`, the first lines of the payload, the customer's note about an ERP upgrade, and two context facts the customer gave: when the upgrade happened and how many POs failed in the last day. File: `fixtures/invalid_date_format_dtm.json`.

**Tool call:** `triage_edi_transaction` with that JSON.

**Tool output:** tier `orderful_json`, classification `invalid.guideline`. Three hypotheses. Rank 1, high: the ERP upgrade changed the date export to MM-DD-YYYY and the guideline wants CCYYMMDD, resting on `errors[0].message`, `context.erp_change_at`, `context.invalid_count_24h`. Rank 2, medium: an existing FORMATDATE rule now runs on a value it was not written for, resting on `errors[0].path`. Rank 3, low: the change is limited to DTM because BEG05 still validates. Customer reply names the failing element, says it is the only one failing, and offers a choice: fix the export or let the platform add a converting rule. Ticket two: a `rule`, effort S, FORMATDATE on DTM02 for this partner, wrapped in IFERROR.

**What the skill did:** checked that `validationStatus=INVALID` plus `source=guideline` agree with `invalid.guideline` in the taxonomy. Presented the three hypotheses with their paths. Presented the reply and the rule proposal. Asked the engineer which of the two paths the customer prefers before anything else.

**What the skill did not do:** add the rule, resend anything, or promise the customer a timeline.

## 2. Delivery: failed on the channel

**Engineer says:** "All of Acme's ASNs to Retailer One are failing since this morning. Neither side changed anything. 0192f3a1-0000-7000-8000-000000000007."

**Inputs gathered:** id, `856_SHIP_NOTICE`, outbound live, statuses `VALID` / `FAILED` / `NOT_ACKNOWLEDGED`, one error from the channel with code `MDN_ERROR` saying the MDN signature could not be verified, the payload head, the customer's note, and four context facts from the channel page: channel type, attempt count, the partner certificate's expiry, and the first failure time. File: `fixtures/delivery_failed_as2_mdn.json`.

**Tool call:** `triage_edi_transaction` with that JSON.

**Tool output:** tier `orderful_json`, classification `delivery.failed`. Rank 1, high: the partner's AS2 certificate expired at the end of the day and the first failure is four minutes later, resting on `errors[0].message`, `context.partner_cert_not_after`, `context.first_failure_at`. Rank 2, medium: the partner renewed the certificate but did not send it, resting on `customer_note`. Customer reply says the certificate is the cause, not their files, that nothing needs resending, and that redelivery happens once the renewed certificate is on the channel. Ticket two: an `alert`, effort M, a certificate expiry watcher that warns both parties 30 and 7 days ahead.

**What the skill did:** confirmed `deliveryStatus=FAILED` plus `source=channel` is `delivery.failed`, not `delivery.pending` (the channel was reached and refused, three attempts). Said in the reply that the fix is on the partner's side. Proposed the watcher.

**What the skill did not do:** upload a certificate, retry delivery, or contact the partner. It offered to draft the partner email if the engineer wants one.

## 3. Acknowledgment: rejected by the partner's 997

**Engineer says:** "Customer is confused. The PO says Valid and Delivered but Acme rejected it. Whose problem is this? 0192f3a1-0000-7000-8000-000000000008."

**Inputs gathered:** id, `850_PURCHASE_ORDER`, outbound live, statuses `VALID` / `DELIVERED` / `REJECTED`, one error from the acknowledgment saying the ship-to location is unknown, the payload head showing the ship-to code, the customer's note, and three context facts: the ship-to code, when that location opened, and when the partner last updated their location list. File: `fixtures/ack_rejected_997.json`.

**Tool call:** `triage_edi_transaction` with that JSON.

**Tool output:** tier `orderful_json`, classification `ack.rejected`. Rank 1, high: the location opened after the partner's last location update, so their backend does not know it, resting on `errors[0].message`, `context.ship_to_opened_at`, `context.partner_location_list_updated_at`. Rank 2, low: the N104 qualifier is not what this partner expects, resting on the snippet. Customer reply explains that Delivered means the PO reached the partner, that the partner's system then rejected it for a setup gap on their side, and that a resend after they add the location will be accepted. Ticket two: a `rule`, effort M, validate ship-to codes against the partner's location list before sending.

**What the skill did:** answered the customer's actual question ("whose problem is this?") first: the partner's. Explained why Valid and Delivered can both be true and the partner can still say no. Presented the rule as a way to turn a next-day rejection into an immediate validation error.

**What the skill did not do:** resend the PO, or promise the partner would add the location. It proposed contacting the partner's EDI team together and waited.

## When nothing is broken

`fixtures/ack_overdue_quiet_partner.json` is the case with no error at all: Delivered, no 997 yet, and the partner has a history of slow acknowledgments. The tool returns `ack.overdue` and ticket two is a `partner_contact`, not an engineering change. The skill's job there is to say so and stop. Not every ticket has a fix inside it.
