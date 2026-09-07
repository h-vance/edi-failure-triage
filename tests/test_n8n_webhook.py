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
