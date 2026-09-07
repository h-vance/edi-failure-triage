"""The two JS snippets that live inside n8n Set nodes, checked outside n8n.

Node is only needed for these tests; they skip cleanly without it.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from edi_triage import FailedTransaction, run_edi_triage
from intercom_bridge import format_note, seed_body

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node is not installed")
class N8nJsTests(unittest.TestCase):
    def test_extractor_reads_every_seeded_ticket(self):
        seeds = {}
        for path in sorted(FIXTURES.glob("*.json")):
            fx = json.loads(path.read_text())
            keys = ("id", "ediTransactionType", "senderId", "receiverId", "direction", "stream",
                    "validationStatus", "deliveryStatus", "acknowledgmentStatus")
            seeds[path.stem] = {
                "text": seed_body(fx),
                "expect": {k: fx.get(k) for k in keys},
                "n_errors": len(fx.get("errors", [])),
            }
        seeds_path = ROOT / ".pytest_cache" / "seeds.json"
        seeds_path.parent.mkdir(exist_ok=True)
        seeds_path.write_text(json.dumps(seeds))
        out = subprocess.run([NODE, str(ROOT / "n8n" / "extract-triage-inputs.js"), str(seeds_path)],
                             capture_output=True, text=True, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("10 seeded tickets", out.stdout)

    def test_note_formatter_matches_python_for_every_fixture(self):
        for path in sorted(FIXTURES.glob("*.json")):
            fx = json.loads(path.read_text())
            tx = FailedTransaction(**{k: v for k, v in fx.items() if not k.startswith("_") and k != "mock_result"})
            result = run_edi_triage(tx, mock=True)
            result_path = ROOT / ".pytest_cache" / f"{path.stem}.result.json"
            result_path.write_text(json.dumps(result))
            out = subprocess.run([NODE, str(ROOT / "n8n" / "format-note.js"), str(result_path)],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout, format_note(result), path.name)

    def test_committed_workflow_matches_generator(self):
        # The .ts embeds both JS functions. If either changes, regenerate:
        #   python n8n/build-workflow.py
        out = subprocess.run([sys.executable, str(ROOT / "n8n" / "build-workflow.py"), "--check"],
                             capture_output=True, text=True, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_workflow_expressions_never_close_early(self):
        # Inside {{ ... }} a literal "}}" ends the expression. The IIFEs must not contain one.
        ts = (ROOT / "n8n" / "edi-ticket-triage.workflow.ts").read_text()
        for block in ts.split("{{ (() => {")[1:]:
            body = block.split("})() }}")[0]
            self.assertNotIn("}}", body)


if __name__ == "__main__":
    unittest.main()
