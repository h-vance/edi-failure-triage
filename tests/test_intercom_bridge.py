import json
import os
import unittest
from pathlib import Path
from unittest import mock

import intercom_bridge
from intercom_bridge import format_note, post_note, read_conversation, seed, seed_body

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FakeIntercom:
    """Records every request and answers from a small script keyed by (method, path)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.answers[(method, path)]


class ReadTests(unittest.TestCase):
    def test_joins_customer_parts_and_strips_html(self):
        fake = FakeIntercom({
            ("GET", "/conversations/42"): {
                "id": 42,
                "source": {"body": "<p>Every PO is <b>Invalid</b> since Saturday.</p>"},
                "conversation_parts": {"conversation_parts": [
                    {"author": {"type": "admin"}, "body": "<p>Looking now</p>"},
                    {"author": {"type": "user"}, "body": "<p>Transaction id: 0192f3a1-0000-7000-8000-000000000003</p>"},
                ]},
            }
        })
        with mock.patch.object(intercom_bridge, "_request", fake):
            out = read_conversation("42")

        self.assertEqual(out["id"], "42")
        self.assertEqual(
            out["customer_text"],
            "Every PO is Invalid since Saturday.\n\nTransaction id: 0192f3a1-0000-7000-8000-000000000003",
        )


class NoteTests(unittest.TestCase):
    def _result(self):
        fixture = json.loads((FIXTURES / "ack_rejected_997.json").read_text())
        return {"transaction_id": fixture["id"], "tier": "orderful_json", "classification": "ack.rejected",
                "mode": "mock", **fixture["mock_result"]}

    def test_note_is_internal_and_uses_admin_from_me(self):
        fake = FakeIntercom({("GET", "/me"): {"id": 7}, ("POST", "/conversations/42/reply"): {"ok": True}})
        with mock.patch.object(intercom_bridge, "_request", fake), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERCOM_ADMIN_ID", None)
            post_note("42", "<p>hi</p>")

        method, path, body = fake.calls[-1]
        self.assertEqual((method, path), ("POST", "/conversations/42/reply"))
        self.assertEqual(body["message_type"], "note")
        self.assertEqual(body["type"], "admin")
        self.assertEqual(body["admin_id"], "7")

    def test_admin_id_env_skips_me_lookup(self):
        fake = FakeIntercom({("POST", "/conversations/42/reply"): {}})
        with mock.patch.object(intercom_bridge, "_request", fake), mock.patch.dict(os.environ, {"INTERCOM_ADMIN_ID": "9"}):
            post_note("42", "x")
        self.assertEqual([c[1] for c in fake.calls], ["/conversations/42/reply"])

    def test_format_note_escapes_and_says_not_sent(self):
        result = self._result()
        result["customer_reply"] = "<script>alert(1)</script>"
        note = format_note(result)

        self.assertIn("ack.rejected", note)
        self.assertIn("&lt;script&gt;", note)
        self.assertNotIn("<script>", note)
        self.assertIn("not sent", note)
        self.assertIn("Ticket two", note)


class SeedTests(unittest.TestCase):
    def test_reuses_existing_contact(self):
        fake = FakeIntercom({
            ("POST", "/contacts/search"): {"data": [{"id": "c1"}]},
            ("POST", "/conversations"): {"conversation_id": "500"},
        })
        with mock.patch.object(intercom_bridge, "_request", fake):
            out = seed(FIXTURES / "invalid_date_format_dtm.json")

        self.assertEqual(out["conversation_id"], "500")
        self.assertNotIn(("POST", "/contacts"), [(m, p) for m, p, _ in fake.calls])
        self.assertEqual(out["payload"]["from"], {"type": "user", "id": "c1"})
        self.assertIn("Transaction id: 0192f3a1-0000-7000-8000-000000000003", out["payload"]["body"])
        self.assertIn("DTM02", out["payload"]["body"])

    def test_creates_contact_when_search_misses(self):
        fake = FakeIntercom({
            ("POST", "/contacts/search"): {"data": []},
            ("POST", "/contacts"): {"id": "c2"},
            ("POST", "/conversations"): {"conversation_id": "501"},
        })
        with mock.patch.object(intercom_bridge, "_request", fake):
            seed(FIXTURES / "ack_overdue_quiet_partner.json")

        create = next(b for m, p, b in fake.calls if p == "/contacts")
        self.assertEqual(create["role"], "user")
        self.assertTrue(create["email"].endswith("@example.com"))

    def test_conflict_on_create_yields_existing_contact_id(self):
        def fake(method, path, body=None):
            if path == "/contacts/search":
                return {"data": []}
            if path == "/contacts":
                raise RuntimeError("Intercom POST /contacts -> 409: already exists with id=6a9d8247eb2ec5e1ed5a4d6d")
            return {"conversation_id": "502"}

        with mock.patch.object(intercom_bridge, "_request", fake):
            out = seed(FIXTURES / "delivery_failed_as2_mdn.json")
        self.assertEqual(out["payload"]["from"]["id"], "6a9d8247eb2ec5e1ed5a4d6d")

    def test_seed_body_for_never_created_transaction(self):
        fixture = json.loads((FIXTURES / "unprocessed_unknown_partner.json").read_text())
        self.assertIn("never created", seed_body(fixture))


class TokenTests(unittest.TestCase):
    def test_missing_token_is_a_clear_error(self):
        with (
            mock.patch.dict(os.environ, {"INTERCOM_ACCESS_TOKEN": ""}),
            self.assertRaises(RuntimeError) as ctx,
        ):
            intercom_bridge._request("GET", "/me")
        self.assertIn("INTERCOM_ACCESS_TOKEN", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
