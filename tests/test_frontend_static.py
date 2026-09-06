import re
import unittest
from pathlib import Path

import edi_triage

ROOT = Path(__file__).resolve().parents[1]


class FrontendStaticTests(unittest.TestCase):
    def test_model_output_fields_are_escaped_before_rendering(self):
        html = (ROOT / "static" / "index.html").read_text()

        self.assertIn("${escapeHtml(h.hypothesis)}", html)
        self.assertIn("${escapeHtml(data.customer_reply)}", html)
        self.assertIn("${escapeHtml(t.title)}", html)
        self.assertIn("${escapeHtml(t.why)}", html)
        self.assertIn("${escapeHtml(h.check)}", html)
        self.assertIn("${escapeHtml(e.message)}", html)
        self.assertIn("${escapeHtml(tx.snippet)}", html)
        self.assertNotIn("<div class=\"hypothesis-text\">${h.hypothesis}</div>", html)

    def test_narration_covers_every_classification_leaf(self):
        html = (ROOT / "static" / "index.html").read_text()
        block = html.split("const STAGE_STORY = {", 1)[1].split("};", 1)[0]
        keys = set(re.findall(r'^\s*"([a-z.]+)":', block, re.MULTILINE))

        self.assertEqual(keys, set(edi_triage.LEAVES))
        self.assertIn("${renderNarration(tx, data)}", html)

    def test_escape_html_handles_quotes_and_nullish_values(self):
        html = (ROOT / "static" / "index.html").read_text()

        self.assertIn("String(str ?? \"\")", html)
        self.assertIn(".replace(/\"/g, \"&quot;\")", html)
        self.assertIn(".replace(/'/g, \"&#39;\")", html)


if __name__ == "__main__":
    unittest.main()
