---
name: scm-setup
description: Build, evolve, or validate a codebase SCM (structural causal model) stored in .codebase-scm/. Use when no SCM exists yet, the user asks to set up / update / extend / re-validate the model, add a granularity level, or asks schema and format questions. For day-to-day planning, review, and intervention work on an existing SCM, use the scm-usage skill instead.
---

# SCM Setup

A codebase SCM is a **coarse-grained structural causal model** of a codebase:
named entities (services, modules, data stores, feature flags, SLOs — never
individual statements or variables) connected by edges that say how an
intervention on one entity affects others. It lives in the repo at
`.codebase-scm/` so it is versioned, reviewable, and CI-checkable alongside
the code. File format template:
`${CLAUDE_PLUGIN_ROOT}/templates/scm.template.yaml`.
Shared tooling lives at `${CLAUDE_PLUGIN_ROOT}/scripts/`
(`validate.py`, `propagate.py`, `example-scm.yaml`).

## 0. Determine the codebase

1. If the agent is already working inside a repository, that is the codebase.
2. Otherwise (or if ambiguous — e.g. a monorepo, or several checkouts), ask
   the user which codebase the SCM should apply to before doing
   anything else.
3. The SCM root for that codebase is `<repo>/.codebase-scm/`. Multiple SCMs
   at different granularity levels coexist as subfolders (see sections 1–2).

## 1. Check for an existing SCM

List `<repo>/.codebase-scm/`. Each subfolder named `L1-…`, `L2-…`, `L3-…`,
`L4-…` holding an `scm.yaml` is one SCM at that granularity level.

- **If at least one SCM exists** and the task is normal engineering work
  (planning, editing, reviewing, "what breaks if…"), hand off to the
  **scm-usage** skill at the granularity that fits (default: the coarsest
  available for planning/review, the finest available for localized edits).
- **If none exists**, or the user explicitly wants to build/extend/repair a
  model: tell the user no SCM is set up (or what exists), and ask whether
  they want to build the initial SCM and at which granularity level
  (section 2). Do not start building until they confirm the level(s).

## 2. Granularity levels

More granular = more nodes, more maintenance burden. The most granular level
stops at functions — nodes must always be coarser than statements/variables.

| Level | Name | Node examples | Use for |
|---|---|---|---|
| L1 | Landscape | Services, repos, external systems, shared data stores | Deploy/migration blast radius, incident reasoning |
| L2 | Module | Packages, modules, deployable units, feature flags, key SLOs | Feature work, flag changes, perf questions |
| L3 | Unit | Files, classes, schemas, migrations, test suites | Refactors, schema changes, targeted reviews |
| L4 | Function | Public functions/methods, API endpoints, jobs, tuning constants and magic literals as `config` nodes | Precise impact analysis within a subsystem, incl. constant changes |

Multiple levels may coexist (e.g. `L1-landscape/` and `L3-unit/` side by
side). Cross-level edges are allowed only as `refines` links from a coarse
node to the finer SCM's node covering the same entity
(`{from, to, type: refines}`), never as causal edges across levels.

Node admission rule: a node must be a **deployed, state-owning, configured,
or contracted boundary** — something you can deploy, toggle, migrate,
monitor, or hold an SLO on. If in doubt, leave it out; a small honest model
beats a large stale one. At L4, `configured` explicitly includes named
tuning constants: each magic number/string that changes behavior gets its own `config`
node with `domain` + `default` set to the constant's value and `evidence`
pointing at the definition site, so constant changes show up in blast-radius
and intervention analysis like any other node.

## 3. Build mode (initial SCM) — incremental, never one-shot

Do NOT read the whole codebase and emit the SCM in a single pass. Build it
with this loop:

1. **Survey one bounded area** (one service, module, or directory). Prefer
   mechanical sources first: build files, service manifests, imports, schemas
   and migrations, feature-flag definitions, CI/test topology, observability
   dashboards. Read code only to resolve what these leave ambiguous.
2. **Draft or update** nodes and edges in `<level>/scm.yaml` for that area
   only. Every node and every edge MUST carry `evidence` (file path, command
   output, dashboard link, or `elicited:<who>` for human-supplied facts).
   Mark anything uncertain in `open_questions` instead of guessing.
3. **Validate** the file against the rules in section 5 after every update.
4. **Repeat** steps 1–3 for the next area until the coverage checklist passes:
   - every deployable unit / top-level module appears as a node (or is
     consciously excluded with a reason in `notes`);
   - every data store, queue, and external system the code touches is a node;
   - every feature flag and config surface that changes behavior is a node;
   - at L4, every behavior-changing constant is its own `config` node (or is
     consciously excluded with a reason in `notes`);
   - `open_questions` is empty or explicitly deferred with owner and date.
5. **Report** node/edge counts, deferred questions, and confidence per area.
   Stop and ask the user before inventing equations for behavior you cannot
   evidence — qualitative typed edges beat fabricated quantities. Add a
   `model:` block on a node only when the functional form is evidenced
   (see the scm-usage skill, runnable layer); otherwise leave it off.

Edge vocabulary (`type` field, pick exactly one): `calls`, `data-flow`,
`fails-if` (downstream fails when upstream fails), `slows-when`,
`configures` (flag/config changes behavior of target), `covers` (test suite
covers target), `refines` (cross-level link only). Direction convention:
`calls` = caller→callee, `data-flow` = producer→consumer,
`fails-if`/`slows-when` = upstream→downstream, `configures` = source→target,
`covers` = suite→target. Each edge SHOULD carry an
`effect` map when known (`{description, magnitude, observed_in}` e.g.
`{description: "+40ms p99 checkout", magnitude: "40ms p99",
observed_in: "chaos exp 2026-03 / incident #412"}`); omit it when unknown
rather than guessing.

Causal edges must form a DAG (the runner evaluates in topological order);
`validate.py` rejects cycles.

## 5. File format and validation

One SCM = one folder `<repo>/.codebase-scm/<L#>-<slug>/scm.yaml`, following
`${CLAUDE_PLUGIN_ROOT}/templates/scm.template.yaml` (fields: `version`, `codebase`,
`granularity`, `updated`, `nodes[]`, `edges[]`, `open_questions[]`,
`changelog[]`; node fields: `id`, `type`, `description`, `evidence`,
`owns_state`, `slo`; edge fields: `from`, `to`, `type`, `description`,
`evidence`, optional `effect`; node optional `domain`, `default`, `model`,
`unmodeled_reason` (`{reason, detail}` — required when a node has incoming
parents but no `model.expr`; see the rules).

Validation rules (check after every write):
- Node `id`s unique, lowercase-hyphenated; no statement/variable-level nodes.
- Every edge endpoint references an existing node; no causal edges across
  granularity levels (only `refines`).
- Every node and edge has non-empty `evidence`.
- `updated` is today; every change appends a `changelog` entry
  (`{date, author, change}`).
- The file parses as YAML and contains no `TODO`/placeholder content outside
  `open_questions`.
- Causal edges form a DAG (no cycles).
- If a node has `model:`: `kind` is `bool` or `numeric`; `expr` uses only
  parent node ids (hyphens → underscores in the expr), literals, `and`/`or`/
  `not`/`if`/`else`, `min`/`max`, and `+ - * /`; every identifier in `expr`
  is a parent via a non-`refines` incoming edge; `domain` matches `kind`.
- Equation coverage: every node with incoming non-`refines` parents but no
  `model.expr` carries `unmodeled_reason: {reason, detail}` where reason is
  one of `needs-parents` (detail names the missing inputs),
  `grammar-limited` (value is not a scalar bool/numeric), `unmeasured`
  (functional form known, magnitudes not measured), or `deferred` (detail
  gives owner + date). Nodes without parents need no reason. A node MUST
  NOT carry both `model.expr` and `unmodeled_reason`.

Run `` `${CLAUDE_PLUGIN_ROOT}/scripts/validate.py <scm.yaml>` `` after every
write (it checks all of the above; `--allow-stale` warns instead of failing
on `updated`, `--today YYYY-MM-DD` overrides today for tests).

Related skill: **scm-usage** — consult an existing SCM for planning, review,
and interventions, keep it in sync when code changes, and simulate with
`propagate.py`.
