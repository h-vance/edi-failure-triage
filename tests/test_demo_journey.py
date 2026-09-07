"""build_journey is pure: conversation JSON + n8n executions + what /triage saw -> timeline steps."""

import unittest

import demo_journey

FIRED = 1_788_790_237.0  # the moment the signed webhook left the server


def _conv(note_body=None, tagged=True):
    parts = []
    if note_body is not None:
        parts.append({"part_type": "note", "body": note_body, "created_at": int(FIRED) + 2})
    return {
        "id": "215475827415413",
        "created_at": int(FIRED) - 1,
        "conversation_parts": {"conversation_parts": parts},
        "tags": {"tags": [{"name": "edi:invalid.guideline"}] if tagged else []},
        "custom_attributes": {"edi_leaf": "invalid.guideline", "edi_tier": "unknown", "edi_confidence": "high"} if tagged else {},
    }


def _exec(started=0.4, stopped=3.4):
    def iso(t):
        return demo_journey.to_iso(FIRED + t)
    return {"id": "52", "status": "success", "startedAt": iso(started), "stoppedAt": iso(stopped) if stopped is not None else None}


def _states(j):
    return {s["id"]: s["state"] for s in j["steps"]}


class BuildJourneyTests(unittest.TestCase):
    def test_full_success(self):
        j = demo_journey.build_journey(
            _conv(note_body="<p><b>EDI triage</b> for x</p>"), [_exec()],
            {"at": FIRED + 2.1, "classification": "invalid.guideline"}, FIRED, app_id_code="ui6470ff",
        )
        self.assertEqual(set(_states(j).values()), {"done"})
        self.assertTrue(j["done"])
        by_id = {s["id"]: s for s in j["steps"]}
        self.assertAlmostEqual(by_id["received"]["elapsed"], 0.4, places=1)
        # Intercom's whole-second note stamp (fired+2.0) must not show before the triage at fired+2.1.
        self.assertGreaterEqual(by_id["noted"]["elapsed"], by_id["triaged"]["elapsed"])
        elapsed = [s["elapsed"] for s in j["steps"][1:]]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertIn("invalid.guideline", by_id["triaged"]["detail"])
        self.assertIn("EDI triage", by_id["noted"]["detail"])
        self.assertEqual(j["links"]["n8n"], "http://localhost:5678/workflow/9z1LF0lH7PDaL9Mg/executions/52")
        self.assertEqual(j["links"]["intercom"], "https://app.intercom.com/a/inbox/ui6470ff/inbox/conversation/215475827415413")

    def test_nothing_yet(self):
        j = demo_journey.build_journey(_conv(tagged=False), [], None, FIRED)
        s = _states(j)
        self.assertEqual(s["opened"], "done")
        self.assertEqual(s["received"], "active")
        self.assertEqual({s[k] for k in ("triaged", "noted", "tagged", "slack")}, {"pending"})
        self.assertFalse(j["done"])
        self.assertIsNone(j["links"]["n8n"])
        self.assertIsNone(j["links"]["intercom"])

    def test_old_execution_is_ignored(self):
        # An execution from a minute ago belongs to someone else's ticket.
        j = demo_journey.build_journey(_conv(tagged=False), [_exec(started=-60, stopped=-57)], None, FIRED)
        self.assertEqual(_states(j)["received"], "active")

    def test_rejected_signature(self):
        j = demo_journey.build_journey(_conv(tagged=False), [_exec(started=0.1, stopped=0.111)], None, FIRED)
        s = _states(j)
        self.assertEqual(s["received"], "bad")
        self.assertEqual({s[k] for k in ("triaged", "noted", "tagged", "slack")}, {"skipped"})
        self.assertTrue(j["done"])
        self.assertIn("11 ms", next(x for x in j["steps"] if x["id"] == "received")["detail"])

    def test_missing_inputs_branch(self):
        j = demo_journey.build_journey(
            _conv(note_body="<p><b>EDI triage</b> cannot start yet. Missing: sender id.</p>", tagged=False),
            [_exec()], None, FIRED,
        )
        s = _states(j)
        self.assertEqual(s["noted"], "done")
        self.assertEqual({s[k] for k in ("triaged", "tagged", "slack")}, {"skipped"})
        self.assertTrue(j["done"])


class StatusTests(unittest.TestCase):
    def test_missing_env_is_named(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("INTERCOM_ACCESS_TOKEN", "INTERCOM_CLIENT_SECRET", "N8N_MCP_TOKEN")}
        try:
            st = demo_journey.status()
            self.assertFalse(st["ready"])
            self.assertEqual(st["missing"], ["INTERCOM_ACCESS_TOKEN", "INTERCOM_CLIENT_SECRET", "N8N_MCP_TOKEN"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
