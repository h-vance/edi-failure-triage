# n8n workflow: Triage Intercom EDI ticket

An Intercom conversation arrives. n8n reads it, calls this repo's triage server, and leaves an internal note, four conversation attributes, a tag, and a Slack post. Nothing customer-facing is sent. The engineer sends the reply. If the ticket is missing the transaction id, document type, sender, or receiver, the workflow posts a note that asks for them and stops. It never guesses.

This is the ticket-to-triage-note workflow, the first of several planned workflows. Live in n8n as `Triage Intercom EDI ticket` (id `9z1LF0lH7PDaL9Mg`, tags `intercom`, `edi`, `triage`).

## Files

- `edi-ticket-triage.workflow.template.ts`: the workflow as n8n Workflow SDK code, with two `@@` markers.
- `edi-ticket-triage.workflow.ts`: generated. `python n8n/build-workflow.py` fills the markers from the two JS files below and escapes them for the template literal. `tests/test_n8n_js.py` fails if this file is stale.
- `extract-triage-inputs.js`: the ticket parser. Tested against every seeded fixture plus a vague ticket.
- `format-note.js`: the note formatter. The test asserts it matches `intercom_bridge.format_note` byte for byte.
- `build-workflow.py`: the generator.
- `mcp.py`: a small client for the n8n instance MCP server, so the workflow can be validated, created, tested, and published from this repo without Claude Code.

## One-time setup (done once on 2026-09-07; here so it can be redone)

1. n8n running at `http://localhost:5678` (built against n8n 2.34.5).
2. In n8n: Settings > Instance-level MCP > Enable MCP access > Connect > API key tab. Copy the access token into `.env` as `N8N_MCP_TOKEN`. The n8n public API key does not work for the MCP endpoint.
3. Optional, for Claude Code: `claude mcp add --scope user --transport http n8n-mcp-key http://localhost:5678/mcp-server/http --header "Authorization: Bearer $N8N_MCP_TOKEN"`. New MCP servers load at session start.
4. In n8n: a credential of type Bearer Auth named `Intercom (sandbox)` with the token from `.env` `INTERCOM_ACCESS_TOKEN`. The workflow binds it by id; if you recreate it, update the id in the template.
5. Intercom app client secret (Developer Hub > app > Basic information) in this repo's `.env` as `INTERCOM_CLIENT_SECRET`, and in the n8n container's env under the same name (restart n8n and any worker so both see it). The workflow reads it as `$env.INTERCOM_CLIENT_SECRET`; empty means every webhook is rejected.
6. In Intercom: four conversation attributes, type text: `edi_leaf`, `edi_tier`, `edi_confidence`, `edi_ticket_two_kind`. Created through `POST /conversations/attributes`.
7. Triage server up: `BEDROCK_MOCK=true python server.py`. n8n reaches it at `http://host.docker.internal:8001`.
8. Optional: `SLACK_WEBHOOK_URL` in the n8n container env. Empty means the Slack step is mocked.

## Hosted on Render

The live demo runs a second copy of this workflow on n8n at `https://edi-triage-n8n.onrender.com` (official `n8nio/n8n` image, free web service, free Postgres, created by hand in the dashboard). What differs from the local setup:

- The n8n env carries `TRIAGE_URL=https://edi-failure-triage.onrender.com/triage`, `INTERCOM_CLIENT_SECRET`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, and `NODE_OPTIONS=--max-old-space-size=400`. Without the last one Node caps its heap at about 256 MB and n8n dies at boot with "JavaScript heap out of memory".
- The triage server's env carries `N8N_URL`, `N8N_WORKFLOW_ID`, `N8N_MCP_TOKEN` and the two Intercom keys, so the Ticket flow tab fires the signed webhook at the hosted n8n and reads executions from it.
- The Intercom Bearer credential was created in the hosted n8n UI; its id replaces the local one when the workflow code is pushed there (`sed` on `newCredential(...)`, then `create_workflow_from_code` and `publish_workflow` through `n8n/mcp.py` with `N8N_URL` set).
- Only the console button reaches it. Intercom's own webhook still points wherever it was last set; paste the hosted `/webhook/intercom-conversation` URL into Developer Hub to route organic tickets there too.

## Push a change

```bash
python n8n/build-workflow.py
python -m pytest -q
python -c "import json;json.dump({'code':open('n8n/edi-ticket-triage.workflow.ts').read()},open('/tmp/v.json','w'))"
python n8n/mcp.py validate_workflow @/tmp/v.json
# then create_workflow_from_code (new id) or update_workflow (atomic ops) and publish_workflow
```

## Demo

```bash
set -a; . ./.env; set +a
python intercom_bridge.py seed fixtures/invalid_date_format_dtm.json   # prints the conversation id
python n8n/fire-webhook.py <conversation_id>                   # signs the body the way Intercom does, prints 200
python n8n/fire-webhook.py <conversation_id> --bad-signature   # dropped at "Reject: bad signature"
```

Open the conversation in Intercom. The internal note, the `edi:invalid.guideline` tag, and the four attributes are there. In n8n, the execution shows the mocked Slack branch. Verified live on 2026-09-07 with the signature check on: signed (about 3 seconds, note + tag + attributes), wrong signature (11 ms, nothing written), unsigned through the cloudflared tunnel (dropped). `N8N_SMOKE=1` run: 4 passed in 29 s.

Vague ticket: seed a conversation by hand with "my PO is stuck". The workflow posts a note asking for the transaction id, document type, sender, and receiver.

## Public URL (real tickets, no curl)

```bash
brew install cloudflared           # once
cloudflared tunnel --url http://localhost:5678
```

Paste `https://<words>.trycloudflare.com/webhook/intercom-conversation` into Developer Hub > app > Webhooks, topic `conversation.user.created`, save. The URL changes when cloudflared restarts; paste again. The save sends a `ping`, which the workflow ignores. Then `python intercom_bridge.py seed fixtures/<name>.json` and watch the execution arrive on its own.

## Smoke test

```bash
set -a; . ./.env; set +a
BEDROCK_MOCK=true python server.py &
N8N_SMOKE=1 python -m pytest -q tests/test_n8n_webhook.py
```

Three live cases against the sandbox: a signed webhook triages the ticket (note, tag, attributes), a wrongly signed one is dropped, a vague ticket is asked for its inputs. Without `N8N_SMOKE=1` the file only checks the signing helper.

## Not done yet

- Signature compare is a plain string equals in an If node, not constant-time. Upgrade: Code node with `crypto.timingSafeEqual` once `crypto` is in `NODE_FUNCTION_ALLOW_BUILTIN`.
- The tunnel is a cloudflared quick tunnel; the URL changes on restart. A named tunnel gives a stable one.
- Node groups: n8n rejects groups whose members have error outputs leaving the group, which is every HTTP node here. Sticky note carries the overview instead.
