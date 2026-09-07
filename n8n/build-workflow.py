"""Generate n8n/edi-ticket-triage.workflow.ts.

The workflow embeds two JS functions inside n8n expressions. They are tested outside n8n
(n8n/extract-triage-inputs.js, n8n/format-note.js), so the .ts is generated from them
rather than hand-copied. tests/test_n8n_js.py fails if the committed .ts is stale.

    python n8n/build-workflow.py            # rewrite the .ts
    python n8n/build-workflow.py --check    # exit 1 if it would change
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "edi-ticket-triage.workflow.ts"


def js_function(path: Path, name: str) -> str:
    match = re.search(rf"(function {name}\(.*?\n\}}\n)", path.read_text(), re.DOTALL)
    if not match:
        raise SystemExit(f"{name} not found in {path}")
    body = match.group(1).rstrip("\n")
    # The function lands inside a JS template literal in the .ts, so backslashes, backticks
    # and ${ must be escaped or the regexes lose their \b and \d at parse time.
    body = body.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "\n".join(("    " + line) if line.strip() else line for line in body.splitlines())


TEMPLATE = (HERE / "edi-ticket-triage.workflow.template.ts").read_text()


def render() -> str:
    return (
        TEMPLATE.replace("@@EXTRACT@@", js_function(HERE / "extract-triage-inputs.js", "extractTriageInputs"))
        .replace("@@ESCAPE@@", js_function(HERE / "format-note.js", "escapeHtml"))
        .replace("@@FORMAT@@", js_function(HERE / "format-note.js", "formatNote"))
    )


if __name__ == "__main__":
    rendered = render()
    if "--check" in sys.argv:
        current = OUT.read_text() if OUT.exists() else ""
        if current != rendered:
            print(f"{OUT.name} is stale; run python n8n/build-workflow.py", file=sys.stderr)
            sys.exit(1)
        print("up to date")
    else:
        OUT.write_text(rendered)
        print(f"wrote {OUT}")
