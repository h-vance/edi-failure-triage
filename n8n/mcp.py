"""Talk to the n8n instance-level MCP server without Claude Code in the loop.

    python n8n/mcp.py --list
    python n8n/mcp.py validate_workflow @args.json
    python n8n/mcp.py get_workflow_details '{"workflowId": "..."}'

Reads N8N_MCP_TOKEN from .env (Settings > Instance-level MCP > Connect > API key).
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

URL = os.getenv("N8N_URL", "http://localhost:5678").rstrip("/") + "/mcp-server/http"
ENV = Path(__file__).resolve().parent.parent / ".env"


def _token() -> str:
    if tok := os.getenv("N8N_MCP_TOKEN"):
        return tok
    for line in ENV.read_text().splitlines() if ENV.exists() else []:
        if line.startswith("N8N_MCP_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("N8N_MCP_TOKEN is not set (checked the environment and .env)")


def _rpc(method: str, params: dict, session: str | None, _id: int = 1, timeout: float = 600) -> tuple[dict, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {_token()}",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    body = json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params}).encode()
    with urllib.request.urlopen(urllib.request.Request(URL, data=body, headers=headers, method="POST"), timeout=timeout) as r:
        text = r.read().decode()
        session = r.headers.get("Mcp-Session-Id") or session
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:]), session
    return json.loads(text), session


def main(argv: list[str]) -> int:
    _, session = _rpc(
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "edi-failure-triage", "version": "1"}},
        None,
    )
    if argv == ["--list"]:
        res, _ = _rpc("tools/list", {}, session, 2)
        print("\n".join(t["name"] for t in res["result"]["tools"]))
        return 0
    if not argv:
        print(__doc__)
        return 2
    raw = argv[1] if len(argv) > 1 else "{}"
    args = json.loads(Path(raw[1:]).read_text()) if raw.startswith("@") else json.loads(raw)
    res, _ = _rpc("tools/call", {"name": argv[0], "arguments": args}, session, 2)
    if "error" in res:
        print(json.dumps(res["error"], indent=1))
        return 1
    for chunk in res["result"].get("content", []):
        print(chunk.get("text", json.dumps(chunk)))
    return 1 if res["result"].get("isError") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
