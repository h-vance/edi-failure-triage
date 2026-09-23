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

- The n8n env carries `TRIAGE_URL=https://edi-failure-triage.onrender.com/triage`, `INTERCOM_CLIENT_SECRET`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, and a `NODE_OPTIONS` heap setting. Without one Node caps its heap at about 256 MB and n8n dies at boot with "JavaScript heap out of memory"; too large a value and the container runs out of RAM instead. See "Memory on the free instance" below.
- The triage server's env carries `N8N_URL`, `N8N_WORKFLOW_ID`, `N8N_MCP_TOKEN` and the two Intercom keys, so the Ticket flow tab fires the signed webhook at the hosted n8n and reads executions from it.
- The Intercom Bearer credential was created in the hosted n8n UI; its id replaces the local one when the workflow code is pushed there (`sed` on `newCredential(...)`, then `create_workflow_from_code` and `publish_workflow` through `n8n/mcp.py` with `N8N_URL` set).
- Only the console button reaches it. Intercom's own webhook still points wherever it was last set; paste the hosted `/webhook/intercom-conversation` URL into Developer Hub to route organic tickets there too.
- The instance sleeps after 15 idle minutes. The console's Ticket flow tab polls `GET /demo/warm` on the triage server, which probes n8n's `/healthz`; the probe is what starts the boot, and the Send button stays off until n8n answers.

### Memory on the free instance

On 2026-09-22 Render alerted that this service exceeded its 512 MB limit during a cold start and restarted it. The keep-warm run history shows the same signature (90 s timeout, then two 502s, then healthy) several times a week since the service was created. The cause was the first fix: `NODE_OPTIONS=--max-old-space-size=400` gives V8 400 MB of old space on a 512 MB box, leaving about 112 MB for the young generation, native buffers, and the runtime. n8n also starts a task-runner child at boot, and the child inherits `NODE_OPTIONS`, so it gets its own 400 MB ceiling. Boot is the hungriest moment (every node package loads, migrations run, workflows activate, the runner starts), so a fraction of cold starts cross the limit.

Settings on the `edi-triage-n8n` service in the Render dashboard (Environment tab):

| Variable | Value | Why |
|---|---|---|
| `NODE_OPTIONS` | `--max-old-space-size=320` | boot needs more than the 256 default; 400 overshoots. If 320 fails to boot, try 352. |
| `N8N_RUNNERS_MAX_OLD_SPACE_SIZE` | `128` | caps the task-runner child instead of letting it inherit the main heap. Verify the name against the docs for the deployed n8n version. |
| `N8N_DIAGNOSTICS_ENABLED` | `false` | boot-time extras off |
| `N8N_VERSION_NOTIFICATIONS_ENABLED` | `false` | boot-time extras off |
| `N8N_TEMPLATES_ENABLED` | `false` | boot-time extras off |
| `N8N_PERSONALIZATION_ENABLED` | `false` | boot-time extras off |
| `EXECUTIONS_DATA_PRUNE` | `true` | keep the Postgres small |
| `EXECUTIONS_DATA_MAX_AGE` | `168` | hours; one week of executions |
| `EXECUTIONS_DATA_PRUNE_MAX_COUNT` | `200` | hard cap on stored executions |

Keep saving successful executions: the Ticket flow tab reads them through the n8n MCP endpoint.

Verify: force four or five cold starts (suspend and resume the service, or wait 15 minutes between hits to `/healthz`) and watch the Events tab. Expect boots of 60 to 90 s and no "exceeded memory" restarts.

Also check the database. Render free Postgres expires about 30 days after creation; this one was created on 2026-09-08 and the dashboard showed 2026-10-08 as the deletion date. When it goes, the workflow, the credential and the MCP token go with it.

The honest ceiling: n8n on 512 MB is below the vendor minimum and stays marginal after tuning. Two ways out. One month of Render Standard (2 GB) for the n8n service only, or run n8n locally through a named cloudflared tunnel during live demos (the "Public URL" section above) with the hosted copy as the fallback.

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
