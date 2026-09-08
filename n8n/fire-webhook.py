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

DEFAULT_URL = os.getenv("N8N_URL", "http://localhost:5678").rstrip("/") + "/webhook/intercom-conversation"


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
