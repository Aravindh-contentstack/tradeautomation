"""Injects the precomputed trades into the explorer page.

Run:  ./.venv/bin/python scripts/build_explorer_page.py [OUT.html]

The page is published as an Artifact, which cannot fetch anything at
runtime, so the data has to live inside the HTML. Keeping the template and
the data separate in the repo (rather than committing one 800KB blob) means
the page's markup stays reviewable and diffable, and rebuilding after a new
backtest is one command.
"""

import json
import os
import sys

TEMPLATE = os.path.join("scripts", "explorer_template.html")
DATA = os.path.join("data", "research", "explorer.json")
DEFAULT_OUT = os.path.join("data", "research", "explorer.html")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    template = open(TEMPLATE).read()
    if "__DATA__" not in template:
        raise SystemExit("template has no __DATA__ placeholder")

    with open(DATA) as f:
        payload = json.load(f)

    # Re-serialised rather than pasted verbatim so a malformed data file
    # fails here, with a JSON error naming the line, instead of silently
    # producing a page that renders blank.
    blob = json.dumps(payload, separators=(",", ":"))
    # The blob sits inside <script type="application/json">, so the only
    # sequence that could break out of it is a literal closing script tag.
    blob = blob.replace("</", "<\\/")

    html = template.replace("__DATA__", blob)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        f.write(html)

    insts = payload["instruments"]
    cells = sum(len(i["cells"]) for i in insts.values())
    rows = sum(len(v) for i in insts.values() for v in i["cells"].values())
    print("wrote %s  (%s | %d cells, %d trades, %.1f MB)"
          % (out, ", ".join(insts), cells, rows,
             os.path.getsize(out) / 1048576))


if __name__ == "__main__":
    main()
