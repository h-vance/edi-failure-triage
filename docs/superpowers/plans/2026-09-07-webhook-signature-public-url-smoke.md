# Webhook Signature, Public URL and Smoke Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The n8n workflow `Triage Intercom EDI ticket` (id `9z1LF0lH7PDaL9Mg`) rejects webhooks that Intercom did not sign, is reachable from Intercom over a public URL, and has one runnable smoke test that proves the whole path.

**Architecture:** Two nodes go between the Webhook trigger and the existing topic check: a Crypto node computes HMAC-SHA1 of the raw body with the app client secret taken from the n8n container env, and an If node compares it with the `X-Hub-Signature` header. Bad or unsigned requests go to a NoOp and stop. The webhook still answers 200 first (`responseMode: onReceived`), so Intercom's 5-second rule holds. A tiny Python script signs a fake webhook the way Intercom does, replacing the curl in the README, and a gated pytest file uses it to drive three live cases. The public URL is a cloudflared quick tunnel pasted into the Intercom Developer Hub.

**Tech Stack:** n8n 2.34.5 (Workflow SDK template + `n8n/build-workflow.py` + `n8n/mcp.py`), Python 3.14 stdlib (`hmac`, `hashlib`, `urllib`), `cloudflared` from Homebrew, Intercom API 2.16.

**Spec:** `n8n/README.md` section "Not done yet" (first two bullets) plus `IDEAS.md` workflow 1 step 1. No separate spec doc exists; this plan is derived from those two files and the template `n8n/edi-ticket-triage.workflow.template.ts`.

## Global Constraints

- Secrets never appear as literals in workflow text. The client secret is read with `$env.INTERCOM_CLIENT_SECRET` (the n8n containers already run with `N8N_BLOCK_ENV_ACCESS_IN_NODE: "false"`, and the workflow already reads `$env.SLACK_WEBHOOK_URL`).
- The workflow must still answer 200 within 5 seconds. Keep `responseMode: 'onReceived'`.
- The workflow never sends anything customer-facing. Nothing in this plan changes that.
- `n8n/edi-ticket-triage.workflow.ts` is generated. Edit the template, then run `python n8n/build-workflow.py`. `tests/test_n8n_js.py` fails if the generated file is stale.
- Every network node keeps `onError: 'continueErrorOutput'` and its error output wired to `Build error summary`. The two new nodes are not network nodes, so they need no error wiring.
- The n8n instance is the one in `~/Projects/n8n-workflow-as-code` (`docker-compose.yml`, queue mode: `n8n` and `n8n-worker` services both need the new env var).
- Never commit `.env`. Only `.env.example` changes in git.
- The workflow id stays `9z1LF0lH7PDaL9Mg`. Push with `update_workflow` atomic ops, not a new `create_workflow_from_code`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `n8n/edi-ticket-triage.workflow.template.ts` | modify | Webhook gets `rawBody: true`; two new nodes (`Compute signature`, `Signature valid?`) and one NoOp (`Reject: bad signature`); rewire. |
| `n8n/edi-ticket-triage.workflow.ts` | regenerate | Output of `python n8n/build-workflow.py`. |
| `n8n/fire-webhook.py` | create | `sign(secret, body)` and `fire(conversation_id, url, bad_signature)`. The demo and the smoke test both use it. |
| `tests/test_n8n_webhook.py` | create | One unit test for `sign` (known HMAC-SHA1 vector). Three smoke tests gated on `N8N_SMOKE=1`. |
| `.env.example` | modify | Add `INTERCOM_CLIENT_SECRET=`. |
| `~/Projects/n8n-workflow-as-code/docker-compose.yml` | modify (other repo) | Pass `INTERCOM_CLIENT_SECRET` into `n8n` and `n8n-worker`. |
| `~/Projects/n8n-workflow-as-code/.env.example` | modify (other repo) | Document `INTERCOM_CLIENT_SECRET=`. |
| `n8n/README.md` | modify | Setup step for the secret, new demo command, "Public URL" section, "Smoke test" section, shorter "Not done yet". |
| `/private/tmp/.../scratchpad/update-ops.json` | scratch | The `update_workflow` operations for `n8n/mcp.py`. Not committed. |

---

### Task 1: Signing helper `n8n/fire-webhook.py`

**Files:**
- Create: `n8n/fire-webhook.py`
- Create: `tests/test_n8n_webhook.py` (unit test part only; Task 5 adds the smoke tests)

**Interfaces:**
- Consumes: nothing from other tasks. Reads env `INTERCOM_CLIENT_SECRET`.
- Produces: `sign(secret: str, body: bytes) -> str` returning `"sha1=<40 hex chars>"`. `fire(conversation_id: str, url: str = DEFAULT_URL, bad_signature: bool = False) -> int` returning the HTTP status. `DEFAULT_URL = "http://localhost:5678/webhook/intercom-conversation"`. Task 5 imports both.

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_n8n_webhook.py
"""Signature helper unit test, plus the live smoke test when N8N_SMOKE=1 (see SmokeTests below).

    set -a; . ./.env; set +a
    N8N_SMOKE=1 python -m pytest -q tests/test_n8n_webhook.py
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The file name has a dash, so it cannot be a plain import.
_spec = importlib.util.spec_from_file_location("fire_webhook", ROOT / "n8n" / "fire-webhook.py")
fire_webhook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fire_webhook)


class SignTests(unittest.TestCase):
    def test_sign_matches_the_rfc2202_style_vector(self):
        # Standard HMAC-SHA1 vector: key "key", the fox sentence.
        body = b"The quick brown fox jumps over the lazy dog"
        self.assertEqual(fire_webhook.sign("key", body), "sha1=de7c9b85b8b78aa6bc8a7a36f70a90701c9db4d9")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest -q tests/test_n8n_webhook.py`
Expected: FAIL with `FileNotFoundError` for `n8n/fire-webhook.py`.

- [ ] **Step 3: Write the helper**

```python
# n8n/fire-webhook.py
"""Sign a fake Intercom webhook the way Intercom does, then POST it to n8n.

Intercom sets X-Hub-Signature to "sha1=" + HMAC-SHA1(raw body, app client secret).
The workflow rejects anything else, so the old curl demo no longer works; use this.

    set -a; . ./.env; set +a
    python n8n/fire-webhook.py <conversation_id> [--url URL] [--bad-signature]
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.request

DEFAULT_URL = "http://localhost:5678/webhook/intercom-conversation"


def sign(secret: str, body: bytes) -> str:
    return "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()


def fire(conversation_id: str, url: str = DEFAULT_URL, bad_signature: bool = False) -> int:
    secret = os.getenv("INTERCOM_CLIENT_SECRET", "")
    if not secret:
        raise SystemExit("INTERCOM_CLIENT_SECRET is not set (Developer Hub > your app > Basic information)")
    body = json.dumps({"topic": "conversation.user.created", "data": {"item": {"id": conversation_id}}}).encode()
    signature = sign("not-the-secret" if bad_signature else secret, body)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Hub-Signature": signature},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("conversation_id")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret; the workflow must drop it")
    args = parser.parse_args(argv)
    print(fire(args.conversation_id, args.url, args.bad_signature))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest -q tests/test_n8n_webhook.py`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add n8n/fire-webhook.py tests/test_n8n_webhook.py
git commit -m "Sign fake Intercom webhooks the way Intercom does

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Secret plumbing (both repos)

**Files:**
- Modify: `.env.example` (this repo)
- Modify: `~/Projects/n8n-workflow-as-code/docker-compose.yml` lines 59-61 (service `n8n`) and lines 103-105 (service `n8n-worker`)
- Modify: `~/Projects/n8n-workflow-as-code/.env.example`
- Modify (not committed): `.env` in both repos

**Interfaces:**
- Produces: env var `INTERCOM_CLIENT_SECRET` inside both n8n containers, read by Task 3's Crypto node as `$env.INTERCOM_CLIENT_SECRET`. The same value in this repo's `.env`, read by Task 1's `fire`.

- [ ] **Step 1: Get the client secret**

Intercom Developer Hub, open the sandbox app used for `INTERCOM_ACCESS_TOKEN`, then Basic information. Copy "Client secret".

- [ ] **Step 2: Add the variable to this repo's `.env.example` and `.env`**

Append to `.env.example`:

```
# Intercom app client secret (Developer Hub > app > Basic information). n8n verifies
# X-Hub-Signature with it; n8n/fire-webhook.py signs the demo webhook with it.
INTERCOM_CLIENT_SECRET=
```

Then put the real value in `.env` (gitignored).

- [ ] **Step 3: Pass it into both n8n containers**

In `~/Projects/n8n-workflow-as-code/docker-compose.yml`, directly under each `SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:-}` line (there are two, one per service), add:

```yaml
      INTERCOM_CLIENT_SECRET: ${INTERCOM_CLIENT_SECRET:-}
```

Append to `~/Projects/n8n-workflow-as-code/.env.example`:

```
# Optional: Intercom app client secret. The edi-failure-triage workflow verifies
# X-Hub-Signature with it. Empty means every webhook is rejected (fails closed).
INTERCOM_CLIENT_SECRET=
```

Put the real value in `~/Projects/n8n-workflow-as-code/.env`.

- [ ] **Step 4: Recreate the containers and verify the env landed**

```bash
cd ~/Projects/n8n-workflow-as-code && docker compose up -d n8n n8n-worker
docker exec n8n-workflow-as-code-n8n-1 sh -c 'test -n "$INTERCOM_CLIENT_SECRET" && echo n8n ok'
docker exec n8n-workflow-as-code-n8n-worker-1 sh -c 'test -n "$INTERCOM_CLIENT_SECRET" && echo worker ok'
```

Expected: both lines print `ok`. Then `curl -s http://localhost:5678/healthz` returns `{"status":"ok"}`.

- [ ] **Step 5: Commit (this repo only; commit the other repo separately with its own message)**

```bash
git add .env.example
git commit -m "Document INTERCOM_CLIENT_SECRET

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
cd ~/Projects/n8n-workflow-as-code && git add docker-compose.yml .env.example && git commit -m "Pass INTERCOM_CLIENT_SECRET into n8n and the worker

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Signature check in the workflow template

**Files:**
- Modify: `n8n/edi-ticket-triage.workflow.template.ts` (the `intercomWebhook` trigger at lines 38-54, and the wiring block at the end)
- Regenerate: `n8n/edi-ticket-triage.workflow.ts`
- Test: `tests/test_n8n_js.py` (existing, unchanged)

**Interfaces:**
- Consumes: env `INTERCOM_CLIENT_SECRET` from Task 2.
- Produces: node names `Compute signature`, `Signature valid?`, `Reject: bad signature`. Task 4 references these names in the `update_workflow` ops.

**Design notes for whoever implements this:**
- `n8n-nodes-base.crypto` is n8n's built-in hashing node, nothing to do with cryptocurrency. It is used here only to compute the HMAC-SHA1 fingerprint Intercom puts in `X-Hub-Signature`. The node is named `Compute signature` on the canvas.
- Webhook option `rawBody: true` keeps the exact bytes Intercom signed as binary property `data`. The parsed JSON `body` is still there, so every existing `$('Intercom webhook').item.json.body...` expression keeps working.
- Crypto node version 1 has a plain `secret` parameter; we fill it with an expression, never a literal. Version 2 wants a `crypto` credential type whose HMAC parameters this instance's type definitions do not show, so version 1 is the version that can be validated today.
- Express lower-cases header names: read `headers['x-hub-signature']`.
- The If node has two conditions joined with `and`: the secret is not empty (fail closed) and the computed value equals the header. A plain string compare is not constant-time. Mark it with a `ponytail:` note; the upgrade path is a Code node with `crypto.timingSafeEqual`, which needs `crypto` added to `NODE_FUNCTION_ALLOW_BUILTIN`.

- [ ] **Step 1: Run the existing test to confirm the current file is fresh**

Run: `python -m pytest -q tests/test_n8n_js.py`
Expected: `4 passed`.

- [ ] **Step 2: Edit the webhook trigger**

Replace the whole `const intercomWebhook = trigger({ ... })` block with:

```ts
const intercomWebhook = trigger({
  type: 'n8n-nodes-base.webhook',
  version: 2.1,
  config: {
    name: 'Intercom webhook',
    parameters: {
      httpMethod: 'POST',
      path: 'intercom-conversation',
      // Intercom wants a 200 within 5 seconds or it retries. Answer first, work after.
      responseMode: 'onReceived',
      // Keep the exact bytes Intercom signed (binary property "data"); body is still parsed.
      options: { rawBody: true },
    },
    notes: 'Raw body kept as binary "data" so the signature check hashes the exact bytes Intercom signed. The parsed JSON body is still available.',
  },
  output: [{ headers: { 'x-hub-signature': 'sha1=0000' }, body: { topic: 'conversation.user.created', data: { item: { id: '123' } } } }],
})
```

- [ ] **Step 3: Add the three new nodes directly after the webhook block**

```ts
// ---------- Verify ----------

const computeSignature = node({
  type: 'n8n-nodes-base.crypto',
  version: 1,
  config: {
    name: 'Compute signature',
    parameters: {
      action: 'hmac',
      type: 'SHA1',
      binaryData: true,
      binaryPropertyName: 'data',
      // The app client secret lives in the n8n container env, never in this file.
      secret: expr('{{ $env.INTERCOM_CLIENT_SECRET }}'),
      dataPropertyName: 'sig',
      encoding: 'hex',
    },
    notes: 'HMAC-SHA1 of the raw body with the Intercom app client secret (env INTERCOM_CLIENT_SECRET on both n8n containers).',
  },
  output: [{ sig: 'de7c9b85b8b78aa6bc8a7a36f70a90701c9db4d9' }],
})

const signatureValid = ifElse({
  version: 2.3,
  config: {
    name: 'Signature valid?',
    parameters: {
      options: {},
      conditions: {
        options: { version: 3, leftValue: '', caseSensitive: true, typeValidation: 'strict' },
        combinator: 'and',
        conditions: [
          {
            // Fails closed: no secret configured means nothing is trusted.
            id: 'secret-set',
            operator: { type: 'string', operation: 'notEmpty', singleValue: true },
            leftValue: expr("{{ $env.INTERCOM_CLIENT_SECRET || '' }}"),
            rightValue: '',
          },
          {
            id: 'signature-matches',
            operator: { type: 'string', operation: 'equals' },
            leftValue: expr("{{ 'sha1=' + $json.sig }}"),
            rightValue: expr("{{ $('Intercom webhook').item.json.headers['x-hub-signature'] || '' }}"),
          },
        ],
      },
    },
    notes: 'ponytail: plain string equals, not constant-time. Upgrade path: a Code node with crypto.timingSafeEqual once NODE_FUNCTION_ALLOW_BUILTIN includes crypto.',
  },
})

const rejectBadSignature = node({
  type: 'n8n-nodes-base.noOp',
  version: 1,
  config: {
    name: 'Reject: bad signature',
    parameters: {},
    notes: 'The webhook already answered 200. Unsigned or wrongly signed requests stop here and touch nothing.',
  },
  output: [{}],
})
```

- [ ] **Step 4: Rewire**

In the `export default workflow(...)` block, replace the first two lines

```ts
  .add(intercomWebhook)
  .to(isNewCustomerConversation
```

with

```ts
  .add(intercomWebhook)
  .to(computeSignature)
  .to(signatureValid.onFalse(rejectBadSignature))
  .to(isNewCustomerConversation
```

Wait: the SDK chains `.to()` off the If node's true branch only when written as `.onTrue(...)`. Write it in the same style as the existing `hasAllRequiredInputs`, so the final wiring block reads:

```ts
export default workflow('edi-ticket-triage', 'Triage Intercom EDI ticket')
  .add(intercomWebhook)
  .to(computeSignature)
  .to(signatureValid
    .onTrue(isNewCustomerConversation
      .onTrue(fetchConversation
        .to(extractTriageInputs)
        .to(lookUpAdminId)
        .to(hasAllRequiredInputs
          .onTrue(runTriage.to(formatInternalNote).to(postInternalNote).to(setConversationAttributes)
            .to(createOrGetTag).to(attachTag).to(formatSlackMessage)
            .to(slackConfigured.onTrue(postToSlackLive).onFalse(postToSlackMocked)))
          .onFalse(askForMissingInputs)))
      .onFalse(ignoreOtherTopics))
    .onFalse(rejectBadSignature))
  // Every network call's error output goes to one summary, then the same Slack switch.
  .add(fetchConversation.output(1).to(buildErrorSummary))
  .add(lookUpAdminId.output(1).to(buildErrorSummary))
  .add(askForMissingInputs.output(1).to(buildErrorSummary))
  .add(runTriage.output(1).to(buildErrorSummary))
  .add(postInternalNote.output(1).to(buildErrorSummary))
  .add(setConversationAttributes.output(1).to(buildErrorSummary))
  .add(createOrGetTag.output(1).to(buildErrorSummary))
  .add(attachTag.output(1).to(buildErrorSummary))
  .add(buildErrorSummary.to(slackConfigured))
```

Also update the header comment at the top of the template: after the line ending "It never guesses." add:

```ts
// Every request is checked against X-Hub-Signature (HMAC-SHA1 of the raw body with the app
// client secret) before anything else runs. Unsigned requests are dropped, not answered.
```

- [ ] **Step 5: Regenerate and run the JS tests**

```bash
python n8n/build-workflow.py
python -m pytest -q tests/test_n8n_js.py
```

Expected: `wrote .../edi-ticket-triage.workflow.ts` then `4 passed`.

- [ ] **Step 6: Validate against the live instance**

```bash
set -a; . ./.env; set +a
python -c "import json;json.dump({'code':open('n8n/edi-ticket-triage.workflow.ts').read()},open('/tmp/v.json','w'))"
python n8n/mcp.py validate_workflow @/tmp/v.json
```

Expected: no errors. If the validator rejects `type: 'SHA1'` on the Crypto node, the instance's HMAC type field is separate from the hash one; keep the parameter (n8n's Crypto node names both `type`) and re-read the validator message for the exact name it wants. If it rejects `rawBody`, the option name is wrong for this version; run `python n8n/mcp.py get_node_types '{"nodeIds":[{"nodeId":"n8n-nodes-base.webhook","version":"2.1"}]}'` and use the name shown.

- [ ] **Step 7: Commit**

```bash
git add n8n/edi-ticket-triage.workflow.template.ts n8n/edi-ticket-triage.workflow.ts
git commit -m "Verify X-Hub-Signature before touching the ticket

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Push the change to workflow `9z1LF0lH7PDaL9Mg` and publish

**Files:**
- Scratch: `/private/tmp/claude-501/-Users-harrison-Projects-edi-failure-triage/ba9b7b90-5bb2-4bca-b8ec-5ad8f2063be5/scratchpad/update-ops.json`
- Tool: `n8n/mcp.py`

**Interfaces:**
- Consumes: node names from Task 3.
- Produces: the live, published workflow that Task 5 tests.

- [ ] **Step 1: Read the current live node positions**

```bash
python n8n/mcp.py get_workflow_details '{"workflowId":"9z1LF0lH7PDaL9Mg"}' | head -80
```

Note the `position` of `Intercom webhook` and `Is new customer conversation?`. Use them below to place the new nodes between (the values `[x, y]` in the ops file are examples; shift the whole downstream chain right by 640 if you want the canvas tidy, or leave overlap; layout is cosmetic).

- [ ] **Step 2: Write the ops file**

```json
{
  "workflowId": "9z1LF0lH7PDaL9Mg",
  "versionName": "Verify X-Hub-Signature before the topic check",
  "versionDescription": "Webhook keeps the raw body; Crypto node computes HMAC-SHA1 with $env.INTERCOM_CLIENT_SECRET; If node compares it with the header and fails closed when the secret is unset. Bad signatures end at a NoOp.",
  "operations": [
    { "type": "setNodeParameter", "nodeName": "Intercom webhook", "path": "/options/rawBody", "value": true },
    { "type": "addNode", "node": {
        "name": "Compute signature", "type": "n8n-nodes-base.crypto", "typeVersion": 1, "position": [220, 300],
        "notes": "HMAC-SHA1 of the raw body with the Intercom app client secret (env INTERCOM_CLIENT_SECRET on both n8n containers).",
        "parameters": {
          "action": "hmac", "type": "SHA1", "binaryData": true, "binaryPropertyName": "data",
          "secret": "={{ $env.INTERCOM_CLIENT_SECRET }}", "dataPropertyName": "sig", "encoding": "hex"
        } } },
    { "type": "addNode", "node": {
        "name": "Signature valid?", "type": "n8n-nodes-base.if", "typeVersion": 2.3, "position": [440, 300],
        "notes": "ponytail: plain string equals, not constant-time. Upgrade path: a Code node with crypto.timingSafeEqual once NODE_FUNCTION_ALLOW_BUILTIN includes crypto.",
        "parameters": {
          "options": {},
          "conditions": {
            "options": { "version": 3, "leftValue": "", "caseSensitive": true, "typeValidation": "strict" },
            "combinator": "and",
            "conditions": [
              { "id": "secret-set", "operator": { "type": "string", "operation": "notEmpty", "singleValue": true },
                "leftValue": "={{ $env.INTERCOM_CLIENT_SECRET || '' }}", "rightValue": "" },
              { "id": "signature-matches", "operator": { "type": "string", "operation": "equals" },
                "leftValue": "={{ 'sha1=' + $json.sig }}",
                "rightValue": "={{ $('Intercom webhook').item.json.headers['x-hub-signature'] || '' }}" }
            ]
          }
        } } },
    { "type": "addNode", "node": {
        "name": "Reject: bad signature", "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [660, 480],
        "notes": "The webhook already answered 200. Unsigned or wrongly signed requests stop here and touch nothing.",
        "parameters": {} } },
    { "type": "removeConnection", "source": "Intercom webhook", "target": "Is new customer conversation?" },
    { "type": "addConnection", "source": "Intercom webhook", "target": "Compute signature" },
    { "type": "addConnection", "source": "Compute signature", "target": "Signature valid?" },
    { "type": "addConnection", "source": "Signature valid?", "sourceIndex": 0, "target": "Is new customer conversation?" },
    { "type": "addConnection", "source": "Signature valid?", "sourceIndex": 1, "target": "Reject: bad signature" }
  ]
}
```

Also clear the old TODO on the webhook node. `notes` is not a parameter, so it is not reachable through `setNodeParameter`; open the node in the n8n editor, Settings tab, and replace the note text with the sentence from Task 3 Step 2. One manual edit; do it right after Step 3 below and before publishing.

- [ ] **Step 3: Apply, then verify the connections**

```bash
python n8n/mcp.py update_workflow @"$SCRATCH/update-ops.json"
python n8n/mcp.py get_workflow_details '{"workflowId":"9z1LF0lH7PDaL9Mg"}' | grep -n -A3 '"Signature valid?"'
```

Expected: the update prints a new version; the details show `Intercom webhook -> Compute signature -> Signature valid?`, with output 0 to `Is new customer conversation?` and output 1 to `Reject: bad signature`. If any op fails, nothing is saved; fix the op and rerun.

- [ ] **Step 4: Validate and publish**

```bash
# validate_workflow takes SDK code, not an id; the .ts was validated in Task 3 Step 6 and update_workflow reported no warnings.
python n8n/mcp.py publish_workflow '{"workflowId":"9z1LF0lH7PDaL9Mg"}'
```

Expected: validation clean, publish reports the active version.

- [ ] **Step 5: First live proof, by hand**

```bash
set -a; . ./.env; set +a
BEDROCK_MOCK=true python server.py &    # if not already up on :8001
python intercom_bridge.py seed fixtures/invalid_date_format_dtm.json      # prints Conversation <id>
python n8n/fire-webhook.py <id>                                            # prints 200
python n8n/fire-webhook.py <id> --bad-signature                            # prints 200
python n8n/mcp.py search_workflow_executions '{"workflowId":"9z1LF0lH7PDaL9Mg","limit":2}'
```

Expected: two executions, both `success`. Open the second one in n8n: it ends at `Reject: bad signature`. Open the first: it runs through to `Post to Slack (mocked)`. Open the conversation in Intercom: exactly one note.

If the first execution fails at `Compute signature` with "binary property data not found", the `rawBody` option did not take. Open the webhook node in the n8n editor, Options, toggle "Raw Body" on, save, publish, rerun.

No commit; nothing in the repo changed in this task.

---

### Task 5: Smoke test

**Files:**
- Modify: `tests/test_n8n_webhook.py` (append `SmokeTests`)

**Interfaces:**
- Consumes: `fire_webhook.fire(conversation_id, url=..., bad_signature=...)` from Task 1; `intercom_bridge.seed(Path) -> {"conversation_id": str}`, `intercom_bridge._request(method, path, body)`, `intercom_bridge._contact_id(email)` (all existing); the published workflow from Task 4.
- Produces: the one runnable check for the whole path. Gated on `N8N_SMOKE=1` so `python -m pytest -q` stays offline-safe.

- [ ] **Step 1: Append the smoke tests**

Add these imports at the top of `tests/test_n8n_webhook.py` (keep the existing ones):

```python
import os
import time

import intercom_bridge
```

Append after `SignTests`:

```python
@unittest.skipUnless(os.getenv("N8N_SMOKE") == "1", "live smoke test: set N8N_SMOKE=1 with .env loaded, n8n and the triage server up")
class SmokeTests(unittest.TestCase):
    """Drives the published workflow end to end through the Intercom sandbox.

    Three cases: a signed webhook triages the ticket; a wrongly signed one is dropped;
    a vague ticket gets asked for the missing inputs. Each case seeds a fresh conversation.
    """

    def _conversation(self, cid: str) -> dict:
        return intercom_bridge._request("GET", f"/conversations/{cid}")

    def _notes(self, cid: str) -> list[dict]:
        parts = self._conversation(cid).get("conversation_parts", {}).get("conversation_parts", [])
        return [p for p in parts if p.get("part_type") == "note"]

    def _wait_for_note(self, cid: str, needle: str, timeout: float = 60) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for note in self._notes(cid):
                if needle in (note.get("body") or ""):
                    return note
            time.sleep(3)
        self.fail(f"no note containing {needle!r} on conversation {cid} within {timeout}s")

    def test_signed_webhook_triages_the_ticket(self):
        cid = intercom_bridge.seed(ROOT / "fixtures" / "invalid_date_format_dtm.json")["conversation_id"]
        self.assertEqual(fire_webhook.fire(cid), 200)
        self._wait_for_note(cid, "EDI triage")
        conv = self._conversation(cid)
        self.assertEqual(conv.get("custom_attributes", {}).get("edi_leaf"), "invalid.guideline")
        self.assertIn("edi:invalid.guideline", [t["name"] for t in conv.get("tags", {}).get("tags", [])])

    def test_wrongly_signed_webhook_is_dropped(self):
        cid = intercom_bridge.seed(ROOT / "fixtures" / "invalid_date_format_dtm.json")["conversation_id"]
        self.assertEqual(fire_webhook.fire(cid, bad_signature=True), 200)
        time.sleep(15)  # longer than a full successful run (about 3 s live)
        self.assertEqual(self._notes(cid), [])
        self.assertNotIn("edi_leaf", self._conversation(cid).get("custom_attributes", {}))

    def test_vague_ticket_is_asked_for_inputs(self):
        contact = intercom_bridge._contact_id("vague-ticket@example.com")
        cid = str(intercom_bridge._request(
            "POST", "/conversations", {"from": {"type": "user", "id": contact}, "body": "my PO is stuck"}
        )["conversation_id"])
        self.assertEqual(fire_webhook.fire(cid), 200)
        note = self._wait_for_note(cid, "cannot start yet")
        self.assertIn("transaction id", note["body"].lower())
```

- [ ] **Step 2: Confirm the offline run still skips**

Run: `python -m pytest -q tests/test_n8n_webhook.py`
Expected: `1 passed, 3 skipped`.

- [ ] **Step 3: Run it live**

```bash
set -a; . ./.env; set +a
# n8n up (docker), workflow published (Task 4), triage server up: BEDROCK_MOCK=true python server.py
N8N_SMOKE=1 python -m pytest -q tests/test_n8n_webhook.py
```

Expected: `4 passed` in under two minutes. If `test_vague_ticket_is_asked_for_inputs` fails on the `transaction id` assertion, print `note["body"]` and match the exact wording of the missing-inputs note in the template (`Missing: ...` followed by the field names from `n8n/extract-triage-inputs.js`); adjust the needle, not the workflow.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass, three skipped.

- [ ] **Step 5: Commit**

```bash
git add tests/test_n8n_webhook.py
git commit -m "Smoke test the published workflow through the Intercom sandbox

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Public URL so real Intercom tickets trigger it

**Files:**
- None in the repo except the README (Task 7). This task is setup and a live check.

**Interfaces:**
- Consumes: the published workflow (Task 4), the client secret in the containers (Task 2).
- Produces: a `https://<random>.trycloudflare.com` URL subscribed in the Intercom Developer Hub.

**Why a quick tunnel:** no account, one command, and the demo is short-lived. The URL changes every time `cloudflared` restarts; re-paste it in the Developer Hub when that happens. A named tunnel or ngrok is the upgrade if the demo needs a stable URL.

- [ ] **Step 1: Install and start the tunnel**

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:5678
```

Expected: a line like `https://<words>.trycloudflare.com` in the output. Leave it running in its own terminal.

- [ ] **Step 2: Check the public path answers**

```bash
set -a; . ./.env; set +a
python n8n/fire-webhook.py 0 --url https://<words>.trycloudflare.com/webhook/intercom-conversation
```

Expected: `200`. In n8n, one execution that fails at `Fetch conversation` (id `0` does not exist) and ends in `Post to Slack (mocked)` with the error summary. That proves the tunnel, the signature and the error branch in one shot.

- [ ] **Step 3: Subscribe in the Intercom Developer Hub**

Developer Hub, the sandbox app, Webhooks:
1. Endpoint URL: `https://<words>.trycloudflare.com/webhook/intercom-conversation`
2. Topics: tick `conversation.user.created` only.
3. Save. Intercom sends a `ping` topic on save. The workflow routes it to `Ignore other topics`. Confirm that execution appears in n8n.

The "Send test request" button on that page posts a sample conversation with a fake id, which lands in the error branch like Step 2. That is expected.

- [ ] **Step 4: A real ticket, no curl**

```bash
python intercom_bridge.py seed fixtures/invalid_date_format_dtm.json
```

Expected: within about 10 seconds an execution appears in n8n on its own, and the conversation in Intercom has the note, the `edi:invalid.guideline` tag and the four attributes. Note the execution id and time for the README.

- [ ] **Step 5: Negative check from outside**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<words>.trycloudflare.com/webhook/intercom-conversation \
  -H 'Content-Type: application/json' \
  -d '{"topic":"conversation.user.created","data":{"item":{"id":"1"}}}'
```

Expected: `200`, and the execution in n8n ends at `Reject: bad signature`. Nothing is posted anywhere.

No commit.

---

### Task 7: README

**Files:**
- Modify: `n8n/README.md`

**Interfaces:** none. Documentation of Tasks 1 to 6.

- [ ] **Step 1: One-time setup**

After step 4 (the Bearer Auth credential) insert a new step and renumber the rest:

```markdown
5. Intercom app client secret (Developer Hub > app > Basic information) in this repo's `.env` as `INTERCOM_CLIENT_SECRET`, and in `~/Projects/n8n-workflow-as-code/.env` under the same name. `docker compose up -d n8n n8n-worker` there so both containers see it. The workflow reads it as `$env.INTERCOM_CLIENT_SECRET`; empty means every webhook is rejected.
```

- [ ] **Step 2: Demo section**

Replace the `curl` command with:

```bash
python n8n/fire-webhook.py <conversation_id>          # signs the body the way Intercom does, prints 200
python n8n/fire-webhook.py <conversation_id> --bad-signature   # dropped at "Reject: bad signature"
```

Keep the paragraph after it. Update the "Verified live" sentence with the execution id and date from Task 6 Step 4.

- [ ] **Step 3: New sections after Demo**

```markdown
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
```

- [ ] **Step 4: Not done yet**

Delete the first two bullets (signature check, public URL). Keep the node-groups bullet. Add:

```markdown
- Signature compare is a plain string equals in an If node, not constant-time. Upgrade: Code node with `crypto.timingSafeEqual` once `crypto` is in `NODE_FUNCTION_ALLOW_BUILTIN`.
- The tunnel is a cloudflared quick tunnel; the URL changes on restart. A named tunnel gives a stable one.
```

- [ ] **Step 5: Commit**

```bash
git add n8n/README.md
git commit -m "Document the signature check, the public URL and the smoke test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage.** README bullet 1 (signature check) is Tasks 2, 3, 4. README bullet 2 (public URL + Developer Hub subscription) is Task 6. "Full smoke testing" is Task 5 plus the hand checks in Tasks 4 and 6. `IDEAS.md` step 1 (200 within 5 seconds, then check) is kept by `responseMode: onReceived`.
- **Placeholders.** `<id>`, `<words>` and `$SCRATCH` are values the runner reads off their own terminal; every command is otherwise complete.
- **Names.** `Compute signature`, `Signature valid?`, `Reject: bad signature` are identical in Task 3 (template), Task 4 (ops) and Task 5 (expectations). `fire_webhook.fire` and `sign` match between Task 1 and Task 5. Env name `INTERCOM_CLIENT_SECRET` is the same in every task.
- **Known unknowns, in order of likelihood.** (1) Whether the validator accepts `type: 'SHA1'` for HMAC on Crypto v1: Task 3 Step 6 says what to do. (2) Whether `rawBody: true` also keeps the parsed JSON body in this n8n version: Task 4 Step 5 says how it shows up and what to do. Both are settled by the first live run, not by more reading.
