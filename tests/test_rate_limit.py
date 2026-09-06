import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import rate_limit
import server

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ack_rejected_997.json"


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        rate_limit.reset()
        self._original_limit = rate_limit.RATE_LIMIT_REQUESTS
        rate_limit.RATE_LIMIT_REQUESTS = 2
        self.client = TestClient(server.app)

    def tearDown(self):
        rate_limit.RATE_LIMIT_REQUESTS = self._original_limit
        rate_limit.reset()

    def _triage_request(self):
        return self.client.post("/triage", json=json.loads(FIXTURE.read_text()))

    def test_allows_requests_within_the_limit(self):
        for _ in range(2):
            response = self._triage_request()
            self.assertEqual(response.status_code, 200)

    def test_blocks_requests_over_the_limit(self):
        for _ in range(2):
            self._triage_request()

        response = self._triage_request()

        self.assertEqual(response.status_code, 429)
        self.assertIn("Rate limit", response.json()["detail"])

    def test_unrelated_endpoints_are_not_rate_limited(self):
        for _ in range(5):
            response = self.client.get("/health")
            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
