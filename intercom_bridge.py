"""Intercom in the loop: read a ticket, post the triage as an internal note, seed a sandbox.

Reading in Claude Code normally goes through Intercom's own MCP plugin. This file covers
what that plugin cannot do (write a note) and what a demo needs (seed a conversation from
a fixture). It never sends a customer-facing reply.

    python intercom_bridge.py read <conversation_id>
    python intercom_bridge.py note <conversation_id> result.json [--dry-run]
    python intercom_bridge.py seed fixtures/<name>.json [--dry-run]
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.intercom.io"
VERSION = "2.16"


def _token() -> str:
    token = os.getenv("INTERCOM_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("INTERCOM_ACCESS_TOKEN is not set")
    return token


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Intercom-Version": VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Intercom {method} {path} -> {e.code}: {e.read().decode()[:300]}") from e


def _text(fragment: str) -> str:
    # ponytail: tag stripping plus html.unescape only; swap for html.parser if notes ever carry rich content back.
    plain = re.sub(r"<br\s*/?>|</p>", "\n", fragment or "", flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(plain)).strip()


def read_conversation(conversation_id: str) -> dict:
    """The customer's words, in order: the opening message plus every later user part."""
    conv = _request("GET", f"/conversations/{conversation_id}")
    pieces = [_text(conv.get("source", {}).get("body", ""))]
    for part in conv.get("conversation_parts", {}).get("conversation_parts", []):
        if part.get("author", {}).get("type") == "user" and part.get("body"):
            pieces.append(_text(part["body"]))
    return {"id": str(conv.get("id", conversation_id)), "customer_text": "\n\n".join(p for p in pieces if p)}


def format_note(result: dict) -> str:
    """Internal note body from a triage result. Simple HTML, which is what the reply endpoint takes."""
    e = html.escape
    head = (
        f"<b>EDI triage</b> for {e(result['transaction_id'])}: "
        f"tier {e(result['tier'])}, classification {e(result['classification'])}, mode {e(result['mode'])}"
    )
    lines = [head, "<b>Hypotheses</b>"]
    for h in result["hypotheses"]:
        lines.append(f"{h['rank']}. [{e(h['confidence'])}] {e(h['hypothesis'])}")
        if h.get("evidence"):
            lines.append(f"&nbsp;&nbsp;evidence: {e(', '.join(h['evidence']))}")
        if h.get("check"):
            lines.append(f"&nbsp;&nbsp;check: {e(h['check'])}")
    t = result["ticket_two"]
    lines += [
        "<b>Draft customer reply</b> (not sent, send it yourself from the reply box)",
        e(result["customer_reply"]),
        f"<b>Ticket two</b> [{e(t['kind'])}, effort {e(t['effort'])}]: {e(t['title'])}",
        e(t["why"]),
    ]
    return "".join(f"<p>{line}</p>" for line in lines)


def _admin_id() -> str:
    return os.getenv("INTERCOM_ADMIN_ID") or str(_request("GET", "/me")["id"])


def post_note(conversation_id: str, body: str) -> dict:
    """An internal note on the conversation. message_type=note is what keeps it off the customer's screen."""
    return _request(
        "POST",
        f"/conversations/{conversation_id}/reply",
        {"message_type": "note", "type": "admin", "admin_id": _admin_id(), "body": body},
    )


def _contact_id(email: str) -> str:
    found = _request(
        "POST",
        "/contacts/search",
        {"query": {"operator": "AND", "value": [{"field": "email", "operator": "=", "value": email}]}},
    ).get("data", [])
    if found:
        return str(found[0]["id"])
    try:
        return str(_request("POST", "/contacts", {"role": "user", "email": email})["id"])
    except RuntimeError as e:
        # Search lags a few seconds behind create; a 409 names the id we wanted.
        hit = re.search(r"already exists with id=([0-9a-f]+)", str(e))
        if not hit:
            raise
        return hit.group(1)


def seed_body(fixture: dict) -> str:
    """What a customer would paste: their note, the id, the statuses, the error text."""
    lines = [fixture["customer_note"], "", f"Transaction id: {fixture['id']}"]
    # A real ticket names the document and the two partners somewhere. Say it here too,
    # so anything reading the ticket back (the skill, an n8n workflow) has the required
    # fields without asking a follow-up question.
    lines.append(
        f"Document {fixture['ediTransactionType']}, sender {fixture['senderId']}, "
        f"receiver {fixture['receiverId']}, direction {fixture.get('direction', 'in')}, "
        f"stream {fixture.get('stream', 'live')}."
    )
    lines.append(
        f"It shows validation {fixture.get('validationStatus') or 'nothing (never created)'}, "
        f"delivery {fixture.get('deliveryStatus', 'PENDING')}, "
        f"acknowledgment {fixture.get('acknowledgmentStatus', 'NOT_ACKNOWLEDGED')}."
    )
    for err in fixture.get("errors", []):
        lines.append(f"Error: {err['message']}")
    return "\n".join(lines)


def seed(fixture_path: Path) -> dict:
    """Open a sandbox conversation as an invented contact named after the fixture's sender."""
    fixture = json.loads(fixture_path.read_text())
    email = f"{fixture['senderId'].lower()}@example.com"
    payload = {"from": {"type": "user", "id": _contact_id(email)}, "body": seed_body(fixture)}
    return {"payload": payload, "conversation_id": str(_request("POST", "/conversations", payload)["conversation_id"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intercom bridge for EDI failure triage.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_read = sub.add_parser("read", help="print the customer's words from a conversation")
    p_read.add_argument("conversation_id")
    p_note = sub.add_parser("note", help="post a triage result as an internal note")
    p_note.add_argument("conversation_id")
    p_note.add_argument("result", type=Path, help="JSON from: python edi_triage.py <tx.json> --json")
    p_note.add_argument("--dry-run", action="store_true")
    p_seed = sub.add_parser("seed", help="open a sandbox conversation from a fixture")
    p_seed.add_argument("fixture", type=Path)
    p_seed.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "read":
            print(read_conversation(args.conversation_id)["customer_text"])
        elif args.cmd == "note":
            body = format_note(json.loads(args.result.read_text()))
            if args.dry_run:
                print(body)
            else:
                post_note(args.conversation_id, body)
                print(f"Note posted on conversation {args.conversation_id}")
        elif args.cmd == "seed":
            if args.dry_run:
                print(seed_body(json.loads(args.fixture.read_text())))
            else:
                print(f"Conversation {seed(args.fixture)['conversation_id']} opened from {args.fixture.name}")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
