"""Signature helper unit test, plus the live smoke test when N8N_SMOKE=1 (see SmokeTests below).

    set -a; . ./.env; set +a
    N8N_SMOKE=1 python -m pytest -q tests/test_n8n_webhook.py
"""

import importlib.util
import os
import time
import unittest
from pathlib import Path

import intercom_bridge

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


if __name__ == "__main__":
    unittest.main()
