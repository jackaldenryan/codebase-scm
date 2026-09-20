---
name: scm-usage
description: Consult an existing codebase SCM (structural causal model) for planning, impact analysis, diff review, and do() interventions. Use when .codebase-scm/ already exists and the user is doing normal engineering work — planning multi-step edits, asking what breaks if something changes, reviewing diffs against the architecture, or running simulations. If no SCM exists yet, or the model itself needs building, extending, or repair, use the scm-setup skill instead.
---

# SCM Usage

This skill assumes at least one SCM already exists at
`<repo>/.codebase-scm/<L#>-<slug>/scm.yaml` (one folder per granularity
level; see the scm-setup skill for levels, format, and validation). If none
exists, hand off to **scm-setup** rather than improvising a model inline.
Shared runner: `${CLAUDE_PLUGIN_ROOT}/scripts/propagate.py`
(examples: `${CLAUDE_PLUGIN_ROOT}/scripts/example-scm.yaml`).

## 4. Consult mode (SCM already exists)

When the user invokes the skill for normal work (planning, editing,
reviewing, answering "what breaks if…"):

1. Load the SCM(s) at the appropriate granularity into working context
   (default: the coarsest available for planning/review, the finest
   available for localized edits).
2. Use the model for: planning edit order (dependencies first), blast-radius
   analysis of a planned change (forward reachable set via `calls`,
   `data-flow`, `fails-if`, `slows-when`), reviewing a diff (every touched
   node → check its outgoing edges for unhandled consequences), and answering
   intervention/counterfactual questions from the graph rather than from
   pattern-matching the code text. When the user asks what happens if a node
   is set to a value, run `propagate.py --set` (runnable layer) rather than
   guessing.
3. Treat the SCM as **advisory, verify against the code**: if the graph and
   the code disagree, the code wins — then update the graph (maintenance).
4. If a query needs finer detail than the loaded level, say so and either
   load the finer SCM (if present) or propose building it (scm-setup skill,
   build mode).

Structural queries need no equations (edge labels suffice):
- invocation/production trace: `propagate.py --trace entry-node
  [--via calls,data-flow]` — what gets turned on or called; `--via` accepts
  any edge type (`fails-if` for dependency chains, `configures` from a flag,
  `covers` for suite→target hops).
- failure propagation: `propagate.py --fail failed-node` — falseness forward
  along `fails-if` only, cross-checked against equations (warns on
  label/model disagreement).
- affected-test selection: `propagate.py --tests changed-node` — suites with
  `covers` edges into the changed node or its downstream.

## 6. Maintenance on code changes

Whenever the agent's own edits (or a PR under review) imply a model change —
new service/dependency/flag/store, removed edge, changed contract or SLO —
update the affected SCM(s) in the same session: edit nodes/edges with fresh
`evidence`, bump `updated`, append `changelog`. Edits must satisfy the
scm-setup skill's file-format and validation rules — re-run
`${CLAUDE_PLUGIN_ROOT}/scripts/validate.py` after every write.
Recommend (but do not require) a CI prediction-check: encode one falsifiable
claim per PR from the model ("this PR cannot affect checkout path") and
verify it with the affected tests (`--tests` lists them); on mismatch, fix
the model first. A stale trusted model is worse than none — when in doubt,
shrink the model (move facts to `open_questions`) rather than let it rot.

## 7. Runnable layer (optional `model:` + `propagate.py`)

The graph is always a map. Nodes MAY add a `model:` so interventions can
be *simulated*, not just traced. Two-tier: qualitative edges always work;
unmodeled descendants propagate as `unknown` (manual review), never as
invented numbers. Every `unknown` names its `unmodeled_reason`, so each one
says what would resolve it.

Node extras:
- `domain`: `bool` | `numeric` | `enum:[a,b,…]` (required if `model` or `default` is set)
- `default`: baseline value used when not intervened (must sit in `domain`)
- `model`: `{kind: bool|numeric, expr: "..."}` — the structural equation
  `node = f(parents)`. Identifiers in `expr` are parent ids with `-`
  replaced by `_` (`cart-service` → `cart_service`).

`expr` grammar (whitelist; no function calls except `min`/`max`, no
attribute access, no comprehensions):
- bool: `and` `or` `not`, comparisons, `x if cond else y`
- numeric: `+ - * /`, `min()`, `max()`, parentheses, numbers
- bool parents used in numeric expr coerce: `true→1`, `false→0`

Runner (from the plugin root):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/propagate.py <scm.yaml> --set node=value [--set node=value ...]
```

(The `--set` table shows only changed/unknown rows by default; `--full`
shows every node. `--json` always dumps the complete result.)

Trace invocation/production structure without equations (what gets turned
on or called from an entry point; follows `calls` + `data-flow` by default,
override with `--via`):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/propagate.py <scm.yaml> --trace entry-node [--via calls,data-flow]
```

Failure propagation along `fails-if` only (with equation cross-check), and
affected-test selection via `covers` (changed node plus its downstream):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/propagate.py <scm.yaml> --fail failed-node[=value]
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/propagate.py <scm.yaml> --tests changed-node
```

`--fail` defaults the failure value to false; use `=true` for nodes whose
unhealthy state is true (e.g. requirements-remain).

Semantics: (1) start from `default`s; (2) apply `--set` as `do()` —
those nodes are *clamped* and their `model.expr` is skipped (Pearl
intervention); (3) evaluate remaining modeled nodes in topological
order; (4) descendants without a model become `unknown`. Output:
baseline vs intervened values, which nodes changed, blast radius
(forward reachability from intervened nodes), and `unknown` nodes
that need manual review.

Do not fabricate `model.expr` to make the runner prettier. A bool
`fails-if` chain (`checkout_up = cart_up and inventory_up`) is the
highest-value first equation; add numeric latency sums only from
measured `effect` magnitudes. Edge labels and equations are independent
layers: any non-`refines` incoming edge may serve as an equation input
regardless of its type; if the math needs an input with no edge, add the
edge (with evidence) first, never math from thin air.

## 8. Visualization ("show me the graph")

When the user asks to see the SCM/graph, generate the interactive page and
open it — do not describe the graph in prose instead:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visualize.py <scm.yaml> --open
```

This writes a self-contained `graph.html` next to the SCM (override with
`-o`) and pops it in a window: layered DAG layout, click any node or edge
to highlight it plus its neighbors with a detail card (description,
equation, evidence, effect), live `do()` overrides with baseline→changed
values, flow tracing over selectable edge types, and search. It runs offline
(single file, no CDN) and uses the same evaluation semantics as
`propagate.py`.

Related skill: **scm-setup** — build, extend, or repair the model itself
(granularity levels, incremental build loop, file format, validation).
