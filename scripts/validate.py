#!/usr/bin/env python3
"""Deterministic validator for a codebase SCM file (skill section 5).

Usage:
  python3 validate.py path/to/scm.yaml [--allow-stale] [--today YYYY-MM-DD]

Checks (fail = exit 1):
  - parses as YAML; required top-level keys; granularity in L1-L4
  - node ids unique, lowercase-hyphenated
  - node/edge `type` in vocabulary; descriptions and evidence non-empty
  - every non-`refines` edge endpoint references an existing node
    (`refines` needs local `from`; `to` may point at another level)
  - `updated` is today (unless --allow-stale); changelog non-empty with
    matching entry for `updated`
  - no TODO/FIXME/XXX/REPLACE-WITH/placeholder outside `open_questions`
  - `model` rules: kind bool|numeric; domain matches kind; domain required
    when default/model set; default sits in domain; expr whitelist
    (and/or/not/if/else, comparisons, + - * / // %, min/max only);
    every identifier in expr is a parent via a non-`refines` incoming edge
Warnings (exit 0): stale-adjacent issues, constant expr with no parents.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: python3 -m pip install pyyaml")

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
GRANULARITIES = {"L1-landscape", "L2-module", "L3-unit", "L4-function"}
NODE_TYPES = {
    "service", "module", "data-store", "queue", "flag", "config",
    "slo", "test-suite", "external",
}
EDGE_TYPES = {
    "calls", "data-flow", "fails-if", "slows-when",
    "configures", "covers", "refines",
}
TODO_RE = re.compile(r"TODO|FIXME|XXX|REPLACE-WITH|placeholder", re.IGNORECASE)

ALLOWED_CALLS = {"min", "max"}
EXPR_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.IfExp,
    ast.Compare, ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def parse_date(v) -> str | None:
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        return v
    return None


def domain_allows(domain, value) -> bool:
    if domain == "bool":
        return isinstance(value, bool)
    if domain == "numeric":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(domain, str) and domain.startswith("enum:"):
        opts = domain[len("enum:"):].strip("[]").split(",")
        opts = [o.strip() for o in opts]
        return value in opts
    return False


def collect_names(expr: str) -> set[str]:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, EXPR_NODES):
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_CALLS:
                raise ValueError("disallowed call (only min()/max())")
            if node.keywords:
                raise ValueError("no keyword args allowed")
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scm")
    ap.add_argument("--allow-stale", action="store_true",
                    help="warn instead of fail when `updated` is not today")
    ap.add_argument("--today", default=None,
                    help="override today as YYYY-MM-DD (for tests)")
    args = ap.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    path = Path(args.scm)
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        print(f"FAIL: YAML parse error: {exc}")
        return 1
    if not isinstance(data, dict):
        print("FAIL: top level must be a mapping")
        return 1

    for key in ("version", "codebase", "granularity", "updated",
                "nodes", "edges", "open_questions", "changelog"):
        if key not in data:
            errors.append(f"missing top-level key: {key}")

    gran = data.get("granularity")
    if gran is not None and gran not in GRANULARITIES:
        errors.append(f"granularity {gran!r} not in {sorted(GRANULARITIES)}")

    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []

    ids: list[str] = []
    by_id: dict = {}
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"nodes[{i}] must be a mapping")
            continue
        nid = n.get("id", "")
        ids.append(nid)
        if nid in by_id:
            errors.append(f"duplicate node id: {nid!r}")
        else:
            by_id[nid] = n
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errors.append(f"nodes[{i}].id {nid!r} must be lowercase-hyphenated")
        if n.get("type") not in NODE_TYPES:
            errors.append(f"node {nid!r}: type {n.get('type')!r} not in {sorted(NODE_TYPES)}")
        if not n.get("description"):
            errors.append(f"node {nid!r}: missing description")
        ev = n.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x.strip() for x in ev):
            errors.append(f"node {nid!r}: evidence must be a non-empty list of strings")
        if not isinstance(n.get("owns_state"), bool):
            errors.append(f"node {nid!r}: owns_state must be bool")
        dom = n.get("domain")
        dflt = n.get("default", None)
        model = n.get("model")
        if (dflt is not None or (isinstance(model, dict) and model.get("expr"))) and dom not in ("bool", "numeric") and not (isinstance(dom, str) and dom.startswith("enum:")):
            errors.append(f"node {nid!r}: domain required (bool|numeric|enum:[..]) when default/model set")
        if dflt is not None and dom is not None and not domain_allows(dom, dflt):
            errors.append(f"node {nid!r}: default {dflt!r} not in domain {dom!r}")
        if model is not None and model is not None:
            if not isinstance(model, dict) or "kind" not in model or "expr" not in model:
                # allow explicit `model: null`
                if model is not None:
                    errors.append(f"node {nid!r}: model must be null or {{kind, expr}}")
            elif model.get("expr") is not None or model.get("kind") is not None:
                kind = model.get("kind")
                expr = model.get("expr")
                if kind not in ("bool", "numeric"):
                    errors.append(f"node {nid!r}: model.kind must be bool|numeric")
                if not isinstance(expr, str) or not expr.strip():
                    errors.append(f"node {nid!r}: model.expr must be a non-empty string")
                if dom == "bool" and kind != "bool":
                    errors.append(f"node {nid!r}: domain bool requires model.kind bool")
                if dom == "numeric" and kind != "numeric":
                    errors.append(f"node {nid!r}: domain numeric requires model.kind numeric")

    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            errors.append(f"edges[{i}] must be a mapping")
            continue
        et = e.get("type")
        fr, to = e.get("from"), e.get("to")
        if et not in EDGE_TYPES:
            errors.append(f"edges[{i}]: type {et!r} not in {sorted(EDGE_TYPES)}")
        if not e.get("description"):
            errors.append(f"edge {fr!r}->{to!r}: missing description")
        ev = e.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x.strip() for x in ev):
            errors.append(f"edge {fr!r}->{to!r}: evidence must be a non-empty list of strings")
        if et == "refines":
            if fr not in by_id:
                errors.append(f"refines edge from unknown node {fr!r}")
            if to not in by_id:
                warnings.append(f"refines edge {fr!r}->{to!r}: target outside this file (cross-level link)")
        else:
            if fr not in by_id:
                errors.append(f"edge from unknown node {fr!r}")
            if to not in by_id:
                errors.append(f"edge to unknown node {to!r}")

    # model expr identifier ⊆ parents (non-refines incoming)
    incoming: dict[str, set[str]] = {nid: set() for nid in by_id}
    for e in edges:
        if isinstance(e, dict) and e.get("type") != "refines":
            if e.get("from") in by_id and e.get("to") in by_id:
                incoming[e["to"]].add(e["from"].replace("-", "_"))
    for nid, n in by_id.items():
        model = n.get("model") if isinstance(n, dict) else None
        if isinstance(model, dict) and model.get("expr"):
            try:
                names = collect_names(model["expr"])
            except (SyntaxError, ValueError) as exc:
                errors.append(f"node {nid!r}: bad expr: {exc}")
                continue
            idents = names - {"min", "max", "and", "or", "not", "if", "else",
                              "true", "false", "True", "False", "None"}
            extra = idents - incoming[nid]
            if extra:
                errors.append(f"node {nid!r}: expr ids {sorted(extra)} are not parents {sorted(incoming[nid])}")
            if not idents:
                warnings.append(f"node {nid!r}: constant expr with no parent references")

    # equation coverage: parents but no expr => unmodeled_reason required
    for nid, n in by_id.items():
        model = n.get("model") if isinstance(n, dict) else None
        has_expr = isinstance(model, dict) and model.get("expr")
        reason = n.get("unmodeled_reason")
        if has_expr and reason is not None:
            errors.append(f"node {nid!r}: MUST NOT carry both model.expr and unmodeled_reason")
        elif incoming[nid] and not has_expr:
            if (not isinstance(reason, dict)
                    or reason.get("reason") not in (
                        "needs-parents", "grammar-limited",
                        "unmeasured", "deferred")
                    or not (isinstance(reason.get("detail"), str)
                            and reason["detail"].strip())):
                errors.append(
                    f"node {nid!r}: has parents {sorted(incoming[nid])} but no "
                    "model.expr — unmodeled_reason {reason, detail} required")

    # acyclicity over causal edges (propagate evaluates in topo order)
    incoming = {nid: 0 for nid in by_id}
    children: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        if isinstance(e, dict) and e.get("type") != "refines":
            fr, to = e.get("from"), e.get("to")
            if fr in by_id and to in by_id:
                incoming[to] += 1
                children[fr].append(to)
    queue = sorted(n for n, k in incoming.items() if k == 0)
    seen_count = 0
    while queue:
        n = queue.pop(0)
        seen_count += 1
        for m in children[n]:
            incoming[m] -= 1
            if incoming[m] == 0:
                queue.append(m)
    if seen_count != len(by_id):
        stuck = sorted(n for n, k in incoming.items() if k > 0)
        errors.append(f"cycle involving {stuck} (causal edges must form a DAG)")
    # updated / changelog
    today = args.today or datetime.date.today().isoformat()
    upd = parse_date(data.get("updated"))
    if upd is None:
        errors.append("updated must be YYYY-MM-DD")
    elif upd != today:
        msg = f"updated {upd} is not today {today}"
        (warnings if args.allow_stale else errors).append(msg)
    clog = data.get("changelog")
    if not isinstance(clog, list) or not clog:
        errors.append("changelog must be a non-empty list")
    else:
        for i, c in enumerate(clog):
            if not isinstance(c, dict) or not c.get("date") or not c.get("author") or not c.get("change"):
                errors.append(f"changelog[{i}] needs {{date, author, change}}")
        if upd is not None and not any(parse_date(c.get("date")) == upd for c in clog if isinstance(c, dict)):
            errors.append(f"no changelog entry dated {upd} (must append one per change)")

    # TODO scan outside open_questions
    probe = {k: v for k, v in data.items() if k != "open_questions"}
    if TODO_RE.search(yaml.safe_dump(probe)):
        errors.append("TODO/FIXME/XXX/REPLACE-WITH/placeholder found outside open_questions")

    print(f"nodes: {len(by_id)}, edges: {len(edges)}")
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1
    print("PASS: SCM valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
