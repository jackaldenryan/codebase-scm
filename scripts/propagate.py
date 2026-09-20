#!/usr/bin/env python3
"""Forward-simulate interventions on a codebase SCM.

Usage:
  python3 propagate.py path/to/scm.yaml --set node=value [--set node=value ...]
  python3 propagate.py path/to/scm.yaml --trace node [--via calls,data-flow]
  python3 propagate.py path/to/scm.yaml --fail node
  python3 propagate.py path/to/scm.yaml --tests node

--trace walks invocation/production structure (default: calls + data-flow
edges) forward from a node and prints what gets turned on or called.
--fail propagates falseness forward along fails-if edges only ("definitely
down" set; --fail node[=value] sets the failure value, default false, use
=true for nodes whose unhealthy state is true) and cross-checks it against
model.expr equations, warning on label/model disagreement. --tests lists test-suite nodes covering the
changed node or anything downstream of it (affected-test selection).
Trace/fail/tests need no equations on the traversed nodes; --set simulates
state values instead. The --set table prints only changed/unknown rows by
default (--full shows all nodes; --json always dumps the complete result).
Direction convention: calls = caller->callee, data-flow = producer->consumer.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: python3 -m pip install pyyaml")


ALLOWED_FUNCS = {"min": min, "max": max}
ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
ALLOWED_UNARY = (ast.UAdd, ast.USub, ast.Not)
ALLOWED_CMP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def nid_to_var(node_id: str) -> str:
    return node_id.replace("-", "_")


class ExprError(ValueError):
    pass


class SafeEval(ast.NodeVisitor):
    def __init__(self, env):
        self.env = env

    def visit(self, node):
        method = "visit_" + type(node).__name__
        visitor = getattr(self, method, None)
        if visitor is None:
            raise ExprError(f"disallowed syntax: {type(node).__name__}")
        return visitor(node)

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float, bool, str)) or node.value is None:
            return node.value
        raise ExprError(f"disallowed constant {node.value!r}")

    def visit_Name(self, node):
        if node.id not in self.env:
            raise ExprError(f"unknown identifier {node.id!r}")
        return self.env[node.id]

    def visit_BoolOp(self, node):
        vals = [self.visit(v) for v in node.values]
        if any(v is None for v in vals):
            return None
        if isinstance(node.op, ast.And):
            return all(vals)
        if isinstance(node.op, ast.Or):
            return any(vals)
        raise ExprError("disallowed boolop")

    def visit_UnaryOp(self, node):
        if not isinstance(node.op, ALLOWED_UNARY):
            raise ExprError("disallowed unary")
        v = self.visit(node.operand)
        if v is None:
            return None
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return -v
        return +v

    def visit_BinOp(self, node):
        if not isinstance(node.op, ALLOWED_BINOPS):
            raise ExprError("disallowed binop")
        a, b = self.visit(node.left), self.visit(node.right)
        if a is None or b is None:
            return None
        a, b = _num(a), _num(b)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b if b != 0 else None
        if isinstance(node.op, ast.FloorDiv):
            return a // b if b != 0 else None
        if isinstance(node.op, ast.Mod):
            return a % b if b != 0 else None
        raise ExprError("disallowed binop")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comp in zip(node.ops, node.comparators):
            if not isinstance(op, ALLOWED_CMP):
                raise ExprError("disallowed compare")
            right = self.visit(comp)
            if left is None or right is None:
                return None
            ok = {
                ast.Eq: left == right,
                ast.NotEq: left != right,
                ast.Lt: left < right,
                ast.LtE: left <= right,
                ast.Gt: left > right,
                ast.GtE: left >= right,
            }[type(op)]
            if not ok:
                return False
            left = right
        return True

    def visit_IfExp(self, node):
        cond = self.visit(node.test)
        if cond is None:
            return None
        return self.visit(node.body if cond else node.orelse)

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
            raise ExprError("disallowed call")
        if node.keywords:
            raise ExprError("no keyword args")
        args = [self.visit(a) for a in node.args]
        if any(a is None for a in args):
            return None
        return ALLOWED_FUNCS[node.func.id](*(_num(a) for a in args))


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return v
    raise ExprError(f"expected number, got {v!r}")


def eval_expr(expr, env):
    tree = ast.parse(expr, mode="eval")
    return SafeEval(env).visit(tree)


def parse_value(raw):
    low = raw.lower()
    if low in ("true", "yes", "up", "on"):
        return True
    if low in ("false", "no", "down", "off"):
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def load_scm(path):
    data = yaml.safe_load(Path(path).read_text())
    nodes = {n["id"]: n for n in data.get("nodes") or []}
    edges = [e for e in (data.get("edges") or []) if e.get("type") != "refines"]
    return data, nodes, edges


def parents_of(edges):
    p = defaultdict(list)
    for e in edges:
        p[e["to"]].append(e["from"])
    return p


def children_of(edges):
    c = defaultdict(list)
    for e in edges:
        c[e["from"]].append(e["to"])
    return c


def topo(nodes, edges):
    incoming = {nid: 0 for nid in nodes}
    ch = children_of(edges)
    for e in edges:
        if e["to"] in incoming:
            incoming[e["to"]] += 1
    q = deque([n for n, k in incoming.items() if k == 0])
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in ch[n]:
            incoming[m] -= 1
            if incoming[m] == 0:
                q.append(m)
    if len(order) != len(nodes):
        leftover = [n for n in nodes if n not in order]
        raise ExprError(f"cycle involving {leftover}")
    return order


def blast(starts, children):
    seen = set()
    q = deque(starts)
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        for m in children[n]:
            q.append(m)
    return seen


def trace(start, edges, via):
    """BFS forward from start over edge types in `via`.

    Returns list of (depth, node, via_edge_type, parent). Structure-only:
    needs no equations.
    """
    adj = defaultdict(list)
    for e in edges:
        if e["type"] in via:
            adj[e["from"]].append((e["to"], e["type"]))
    seen = {start}
    order = [(0, start, None, None)]
    q = deque([(start, 0)])
    while q:
        n, d = q.popleft()
        for m, et in adj[n]:
            if m in seen:
                continue
            seen.add(m)
            order.append((d + 1, m, et, n))
            q.append((m, d + 1))
    return order


def fail_closure(start, edges):
    """BFS falseness forward from start over fails-if edges only.

    The edge type itself is the evidence: each traversed edge asserts the
    downstream node fails when the upstream one does. Returns nodes in
    propagation order, start first.
    """
    adj = defaultdict(list)
    for e in edges:
        if e["type"] == "fails-if":
            adj[e["from"]].append(e["to"])
    seen = {start}
    order = [start]
    q = deque([start])
    while q:
        n = q.popleft()
        for m in adj[n]:
            if m in seen:
                continue
            seen.add(m)
            order.append(m)
            q.append(m)
    return order


def covering_suites(start, nodes, edges):
    """Test suites covering start or anything downstream of it.

    Forward reachability (all causal edges) gives the affected set; any
    test-suite node with a covers edge into that set should run.
    Returns (affected_sorted, [(suite, target), ...]).
    """
    affected = blast([start], children_of(edges))
    hits = []
    for e in edges:
        if (e["type"] == "covers" and e["to"] in affected
                and e["from"] in nodes
                and nodes[e["from"]].get("type") == "test-suite"):
            hits.append((e["from"], e["to"]))
    return sorted(affected), sorted(hits)


def evaluate(nodes, edges, interventions):
    order = topo(nodes, edges)
    par = parents_of(edges)
    values = {}
    unknown = set()
    for nid in order:
        node = nodes[nid]
        if nid in interventions:
            values[nid] = interventions[nid]
            continue
        model = node.get("model") or None
        if model and model.get("expr"):
            env = {}
            missing = False
            for p in par[nid]:
                pv = values.get(p, node_default(nodes[p]) if p in nodes else None)
                if pv is None and p not in interventions:
                    if p in unknown or node_default(nodes.get(p, {})) is None:
                        missing = True
                env[nid_to_var(p)] = pv
            if missing or any(v is None for v in env.values()):
                values[nid] = None
                unknown.add(nid)
                continue
            try:
                values[nid] = eval_expr(model["expr"], env)
            except ExprError as exc:
                raise ExprError(f"{nid}: {exc}") from exc
        else:
            d = node_default(node)
            values[nid] = d
            if d is None and par[nid]:
                unknown.add(nid)
    return values, unknown


def node_default(node):
    if not node:
        return None
    d = node.get("default", None)
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scm")
    ap.add_argument("--set", action="append", default=[], metavar="NODE=VALUE")
    ap.add_argument("--trace", default=None, metavar="NODE",
                    help="print invocation trace forward from NODE, then exit")
    ap.add_argument("--via", default="calls,data-flow",
                    help="comma-separated edge types for --trace")
    ap.add_argument("--fail", default=None, metavar="NODE[=VALUE]",
                    help="propagate failure forward along fails-if edges, then exit "
                         "(failure value defaults to false; use =true for "
                         "nodes whose unhealthy state is true, e.g. requirements-remain)")
    ap.add_argument("--tests", default=None, metavar="NODE",
                    help="list test suites covering NODE or its downstream, then exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="show all nodes (default prints only changed/unknown rows)")
    args = ap.parse_args()

    data, nodes, edges = load_scm(args.scm)

    if args.trace is not None:
        if args.trace not in nodes:
            sys.exit(f"unknown node {args.trace!r}")
        via = tuple(t.strip() for t in args.via.split(",") if t.strip())
        order = trace(args.trace, edges, via)
        if args.json:
            print(json.dumps(
                {"trace_from": args.trace, "via": list(via),
                 "reached": [{"node": n, "depth": d, "via_edge": et,
                              "from": p} for d, n, et, p in order]},
                indent=2))
            return
        print(f"Trace from {args.trace} (via {', '.join(via)}):")
        for d, n, et, p in order:
            if d == 0:
                print(f"  {n}")
            else:
                print(f"  {'  ' * d}--{et}--> {n}")
        print(f"\nReached: {len(order)} node(s)")
        return

    if args.fail is not None:
        spec = args.fail
        if "=" in spec:
            fail_node, raw = spec.split("=", 1)
            fail_value = parse_value(raw)
        else:
            fail_node, fail_value = spec, False
        if fail_node not in nodes:
            sys.exit(f"unknown node {fail_node!r}")
        order = fail_closure(fail_node, edges)
        # Cross-check: clamp only the start to its failure value and see
        # what the equations say about the structural down-set.
        try:
            sim, _ = evaluate(nodes, edges, {fail_node: fail_value})
        except ExprError as exc:
            sys.exit(f"model error: {exc}")
        agree, disagree, structural = [], [], []
        for n in order[1:]:
            model = nodes[n].get("model") or {}
            if not model.get("expr"):
                structural.append(n)
            elif sim.get(n) is False:
                agree.append(n)
            elif sim.get(n) is True:
                disagree.append(n)
            else:
                structural.append(n)
        if args.json:
            print(json.dumps(
                {"failed_from": fail_node, "failure_value": fail_value,
                 "definitely_down": order,
                 "equation_agree": agree, "equation_disagree": disagree,
                 "structural_only": structural}, indent=2))
            return
        print(f"Failure propagation from {fail_node}={fail_value} (via fails-if):")
        for n in order:
            mark = f" [intervened {fail_value}]" if n == fail_node else " [down]"
            print(f"  {n}{mark}")
        print(f"\nDefinitely down: {', '.join(order)}")
        if agree:
            print(f"Confirmed by equations: {', '.join(agree)}")
        if structural:
            print(f"Structural only (no equation): {', '.join(structural)}")
        if disagree:
            print(f"WARNING label/model disagreement (equation says up): "
                  f"{', '.join(disagree)}")
        return

    if args.tests is not None:
        if args.tests not in nodes:
            sys.exit(f"unknown node {args.tests!r}")
        affected, hits = covering_suites(args.tests, nodes, edges)
        if args.json:
            print(json.dumps(
                {"changed": args.tests, "affected": affected,
                 "suites": [{"suite": s, "covers": t} for s, t in hits]},
                indent=2))
            return
        print(f"Affected test selection for {args.tests}:")
        print(f"  Affected ({len(affected)}): {', '.join(affected)}")
        if hits:
            print("  Run:")
            for s, t in hits:
                print(f"    {s}  (covers {t})")
        else:
            print("  No covering test suite found.")
        return

    interventions = {}
    for item in args.set:
        if "=" not in item:
            sys.exit(f"--set expects node=value, got {item!r}")
        k, v = item.split("=", 1)
        if k not in nodes:
            sys.exit(f"unknown node {k!r}")
        interventions[k] = parse_value(v)

    baseline, _ = evaluate(nodes, edges, {})
    intervened, unknown = evaluate(nodes, edges, interventions)
    ch = children_of(edges)
    radius = blast(list(interventions), ch)

    changed = []
    for nid in nodes:
        if baseline.get(nid) != intervened.get(nid):
            changed.append(nid)

    def reason_of(nid):
        r = (nodes[nid].get("unmodeled_reason") or {}) if nid in nodes else {}
        return r if isinstance(r, dict) else {}

    result = {
        "interventions": {k: interventions[k] for k in interventions},
        "baseline": baseline,
        "intervened": intervened,
        "changed": changed,
        "blast_radius": sorted(radius),
        "unknown": sorted(unknown),
        "unknown_reasons": {n: reason_of(n) for n in sorted(unknown)},
    }

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print("Interventions (do):")
    for k, v in interventions.items():
        print(f"  {k} = {v}")
    print("\nNode                 baseline          intervened")
    print("-" * 56)
    omitted = 0
    for nid in nodes:
        b, i = baseline.get(nid), intervened.get(nid)
        interesting = (b != i) or (nid in unknown)
        if not args.full and not interesting:
            omitted += 1
            continue
        mark = " *" if b != i else ""
        if nid in unknown:
            r = reason_of(nid).get("reason", "no reason recorded")
            unk = f"  [unknown:{r}]"
        else:
            unk = ""
        print(f"{nid:20} {str(b):16} {str(i):16}{mark}{unk}")
    if omitted:
        print(f"({omitted} unchanged nodes omitted — use --full)")
    print("\nChanged:", ", ".join(changed) or "(none)")
    print("Blast radius:", ", ".join(sorted(radius)) or "(none)")
    if unknown:
        print("Unknown (unmodeled / missing parents):", ", ".join(sorted(unknown)))
        for nid in sorted(unknown):
            r = reason_of(nid)
            if r.get("detail"):
                print(f"  {nid}: {r.get('reason')} — {r.get('detail')}")


if __name__ == "__main__":
    main()
