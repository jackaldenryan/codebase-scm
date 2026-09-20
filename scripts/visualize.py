#!/usr/bin/env python3
"""Render an SCM file as a self-contained interactive graph page.

Usage:
  python3 visualize.py path/to/scm.yaml [-o graph.html] [--open] [--title TEXT]

The output is a single HTML file (no CDN, no build step, works offline):
layered DAG layout, click-to-inspect side panel, live do-interventions with
the same semantics as propagate.py, flow tracing, and search. Open it with
any browser; --open pops it in the default browser (macOS `open`).

This is what backs "show me the graph": run this script, open the file.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: python3 -m pip install pyyaml")


def jsonable(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scm")
    ap.add_argument("-o", "--output", default=None,
                    help="output HTML path (default: <scm-dir>/graph.html)")
    ap.add_argument("--open", action="store_true",
                    help="open the page in the default browser")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    scm_path = Path(args.scm)
    try:
        data = yaml.safe_load(scm_path.read_text())
    except Exception as exc:
        print(f"FAIL: cannot parse {scm_path}: {exc}")
        return 1
    if not isinstance(data, dict) or not data.get("nodes"):
        print("FAIL: SCM must be a mapping with a non-empty nodes list")
        return 1

    tpl_path = Path(__file__).with_name("graph_template.html")
    if not tpl_path.exists():
        print(f"FAIL: template missing: {tpl_path}")
        return 1
    tpl = tpl_path.read_text()
    if "/*__SCM_DATA__*/null" not in tpl:
        print("FAIL: template placeholder missing")
        return 1

    if args.title:
        data = dict(data)
        data["codebase"] = args.title
    payload = json.dumps(jsonable(data))
    html = tpl.replace("/*__SCM_DATA__*/null", payload, 1)

    out = Path(args.output) if args.output else scm_path.parent / "graph.html"
    out.write_text(html)
    n = len(data.get("nodes") or [])
    m = len(data.get("edges") or [])
    print(f"wrote {out} ({n} nodes, {m} edges)")

    if args.open:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(out)], check=False)
            elif sys.platform.startswith("linux"):
                subprocess.run(["xdg-open", str(out)], check=False)
            else:
                print(f"open this file in a browser: {out}")
        except Exception as exc:
            print(f"could not auto-open ({exc}); file is at {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
