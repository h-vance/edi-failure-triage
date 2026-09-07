"""The console's "Ticket flow" tab: open a real Intercom ticket, fire the signed webhook, watch n8n work.

start() seeds a sandbox conversation from a fixture and fires the webhook the way Intercom would.
poll() reads the conversation back from Intercom and the execution list from n8n, and
build_journey() turns those into the six timeline steps the console draws. build_journey is pure
so tests/test_demo_journey.py can drive it with hand-written JSON.
"""

import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import intercom_bridge
from edi_triage import FIXTURES_DIR

ROOT = Path(__file__).resolve().parent
WORKFLOW_ID = "9z1LF0lH7PDaL9Mg"
N8N_URL = "http://localhost:5678"
REQUIRED_ENV = ("INTERCOM_ACCESS_TOKEN", "INTERCOM_CLIENT_SECRET", "N8N_MCP_TOKEN")

JOURNEYS: dict[str, dict] = {}     # conversation_id -> {fired_at, transaction_id, fixture}
TRIAGE_SEEN: dict[str, dict] = {}  # transaction_id -> {at, classification}; server.py writes it on every POST /triage


def _load_dotenv() -> None:
    # The demo must work when the server is started plainly. Existing env wins.
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _script(name: str):
    # n8n/mcp.py would shadow the MCP SDK package and fire-webhook.py has a dash: load by path.
    spec = importlib.util.spec_from_file_location(f"n8n_{name}", ROOT / "n8n" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status() -> dict:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    return {"ready": not missing, "missing": missing}


def start(fixture_name: str) -> dict:
    if fixture_name not in {p.stem for p in FIXTURES_DIR.glob("*.json")}:  # request input: never join it into a path
        raise KeyError(fixture_name)
    path = FIXTURES_DIR / f"{fixture_name}.json"
    fixture = json.loads(path.read_text())
    cid = intercom_bridge.seed(path)["conversation_id"]
    fired_at = time.time()
    _script("fire-webhook").fire(cid)
    JOURNEYS[cid] = {"fired_at": fired_at, "transaction_id": fixture["id"], "fixture": fixture_name}
    return {"conversation_id": cid, "fired_at": fired_at}


_mcp = None
_mcp_session = None


def _executions(fired_at: float) -> list:
    global _mcp, _mcp_session
    try:
        if _mcp is None:
            _mcp = _script("mcp")
            _, _mcp_session = _mcp._rpc(
                "initialize",
                {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "edi-failure-triage", "version": "1"}},
                None,
            )
        res, _ = _mcp._rpc(
            "tools/call",
            {"name": "search_workflow_executions",
             "arguments": {"workflowId": WORKFLOW_ID, "limit": 5, "startedAfter": to_iso(fired_at - 2)}},
            _mcp_session, 2, timeout=10,
        )
        return json.loads(res["result"]["content"][0]["text"])["data"]
    except (OSError, KeyError, ValueError, IndexError):  # n8n down, dead session, odd payload
        _mcp = None  # re-initialised on the next poll
        return []


_app_id_code = None


def _app_code() -> str | None:
    global _app_id_code
    if _app_id_code is None:
        try:
            _app_id_code = intercom_bridge._request("GET", "/me")["app"]["id_code"]
        except (OSError, RuntimeError, KeyError):  # no link is better than no timeline
            return None
    return _app_id_code


def poll(cid: str) -> dict:
    j = JOURNEYS[cid]
    conv = intercom_bridge._request("GET", f"/conversations/{cid}")
    return build_journey(conv, _executions(j["fired_at"]), TRIAGE_SEEN.get(j["transaction_id"]), j["fired_at"], _app_code())


# ---------- pure ----------

def to_iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_iso(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def build_journey(conv: dict, executions: list, triage_seen: dict | None, fired_at: float, app_id_code: str | None = None) -> dict:
    cid = str(conv.get("id", ""))
    runs = [e for e in executions if (from_iso(e.get("startedAt")) or 0) >= fired_at - 2]
    ex = min(runs, key=lambda e: from_iso(e["startedAt"])) if runs else None
    started = from_iso(ex["startedAt"]) if ex else None
    stopped = from_iso(ex.get("stoppedAt")) if ex else None
    parts = conv.get("conversation_parts", {}).get("conversation_parts", [])
    note = next((p for p in parts if p.get("part_type") == "note"), None)
    note_text = intercom_bridge._text(note.get("body", "")) if note else ""
    asked = "cannot start yet" in note_text
    triaged = bool(triage_seen) and triage_seen["at"] >= fired_at - 1
    tags = [t.get("name", "") for t in conv.get("tags", {}).get("tags", [])]
    attrs = conv.get("custom_attributes", {})
    tagged = any(t.startswith("edi:") for t in tags) and bool(attrs.get("edi_leaf"))

    def step(id_, label, done, at, detail):
        return {"id": id_, "label": label, "state": "done" if done else "pending", "at": at,
                "elapsed": round(at - fired_at, 1) if (at is not None and done) else None, "detail": detail}

    steps = [
        step("opened", "Ticket opened in Intercom", True, conv.get("created_at"), f"conversation {cid}"),
        step("received", "n8n received the webhook, signature verified", ex is not None, started, f"execution {ex['id']}" if ex else ""),
        step("triaged", "Triage ran (POST /triage)", triaged, triage_seen["at"] if triaged else None,
             f"classification {triage_seen['classification']}" if triaged else ""),
        step("noted", "Internal note posted", note is not None, note.get("created_at") if note else None,
             "asked for the missing inputs (no guess)" if asked else note_text[:140]),
        step("tagged", "Tag + 4 attributes set", tagged, stopped or (note.get("created_at") if note else None),
             f"{next((t for t in tags if t.startswith('edi:')), '')}, tier {attrs.get('edi_tier', '?')}, confidence {attrs.get('edi_confidence', '?')}" if tagged else ""),
        step("slack", "Slack post", stopped is not None and tagged, stopped,
             "#edi-support, mocked unless SLACK_WEBHOOK_URL is set on the n8n container"),
    ]
    by_id = {s["id"]: s for s in steps}

    if asked:
        for k in ("triaged", "tagged", "slack"):
            by_id[k]["state"] = "skipped"
    elif ex and stopped is not None and note is None and not triaged:
        by_id["received"]["state"] = "bad"
        by_id["received"]["detail"] = (f"n8n stopped after {round((stopped - started) * 1000)} ms and wrote nothing: "
                                       "rejected signature or ignored topic")
        for k in ("triaged", "noted", "tagged", "slack"):
            by_id[k]["state"] = "skipped"
    else:
        for s in steps:
            if s["state"] == "pending":
                s["state"] = "active"
                break

    # Intercom stamps parts in whole seconds and n8n in milliseconds, so a note can look
    # older than the triage that produced it. Never let a done step precede the one before it.
    floor = None
    for s in steps:
        if s["state"] == "done" and s["at"] is not None:
            if floor is not None and s["at"] < floor:
                s["at"] = floor
                s["elapsed"] = round(floor - fired_at, 1)
            floor = s["at"]

    live = [s for s in steps if s["state"] != "skipped"]
    done = any(s["state"] == "bad" for s in steps) or live[-1]["state"] == "done"
    return {
        "conversation_id": cid,
        "fired_at": fired_at,
        "done": done,
        "steps": steps,
        "links": {
            "n8n": f"{N8N_URL}/workflow/{WORKFLOW_ID}/executions/{ex['id']}" if ex else None,
            "intercom": f"https://app.intercom.com/a/inbox/{app_id_code}/inbox/conversation/{cid}" if app_id_code and cid else None,
        },
    }
