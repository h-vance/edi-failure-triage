"""demo_journey.warm(): is the hosted n8n awake? Drives the cache with fixed clocks and a patched probe."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import demo_journey
import rate_limit
import server


class WarmStateTests(unittest.TestCase):
    def setUp(self):
        demo_journey.reset_warm()

    def test_warm_answer(self):
        with patch.object(demo_journey, "_probe_n8n", return_value=True) as probe:
            self.assertEqual(demo_journey.warm(now=1000.0), {"n8n": "warm", "waking_for": 0})
            self.assertEqual(probe.call_count, 1)

    def test_waking_counts_up_and_reprobes_after_five_seconds(self):
        with patch.object(demo_journey, "_probe_n8n", return_value=False) as probe:
            self.assertEqual(demo_journey.warm(now=1000.0), {"n8n": "waking", "waking_for": 0})
            self.assertEqual(demo_journey.warm(now=1003.0), {"n8n": "waking", "waking_for": 3})
            self.assertEqual(probe.call_count, 1)  # inside WAKING_TTL: many tabs, one probe
            self.assertEqual(demo_journey.warm(now=1006.0), {"n8n": "waking", "waking_for": 6})
            self.assertEqual(probe.call_count, 2)  # past WAKING_TTL: probed again, still counting from first sighting

    def test_warm_is_cached_then_rechecked_and_can_go_back_to_waking(self):
        with patch.object(demo_journey, "_probe_n8n", side_effect=[True, False]) as probe:
            self.assertEqual(demo_journey.warm(now=1000.0)["n8n"], "warm")
            self.assertEqual(demo_journey.warm(now=1059.0)["n8n"], "warm")
            self.assertEqual(probe.call_count, 1)  # inside WARM_TTL: served from cache
            self.assertEqual(demo_journey.warm(now=1060.0), {"n8n": "waking", "waking_for": 0})
            self.assertEqual(probe.call_count, 2)  # past WARM_TTL: re-probed, n8n had gone to sleep

    def test_probe_returns_false_when_urlopen_raises(self):
        with patch.object(demo_journey.urllib.request, "urlopen", side_effect=OSError("timed out")):
            self.assertFalse(demo_journey._probe_n8n(0.1))


class WarmEndpointTests(unittest.TestCase):
    def setUp(self):
        demo_journey.reset_warm()
        rate_limit.reset()
        self._original_limit = rate_limit.RATE_LIMIT_REQUESTS
        rate_limit.RATE_LIMIT_REQUESTS = 2
        self.client = TestClient(server.app)

    def tearDown(self):
        rate_limit.RATE_LIMIT_REQUESTS = self._original_limit
        rate_limit.reset()
        demo_journey.reset_warm()

    def test_demo_warm_is_not_rate_limited(self):
        # the console polls this every 10 s per tab; it must never eat the /triage budget
        with patch.object(demo_journey, "_probe_n8n", return_value=True):
            for _ in range(3):
                res = self.client.get("/demo/warm")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json(), {"n8n": "warm", "waking_for": 0})


if __name__ == "__main__":
    unittest.main()
