# codebase-scm

Give your coding agent an **index of the codebase that can compute logical consequences** — a structural causal model (SCM) of your software, versioned next to the code, in the spirit of Judea Pearl's structural causal models (*Causality*, 2000; *The Book of Why*, 2018).

## Motivation

A coding agent dropped into a repository sees files. What it lacks is a *map of what affects what*: which services die when a dependency fails, what a config flip touches, which tests guard a change, what a constant retune ripples into. Today agents answer those questions by pattern-matching over code text — fluent, confident, and frequently wrong about cross-cutting impact.

A structural causal model fixes the missing layer. In Pearl's framing, a causal model is a set of variables (nodes) plus structural equations saying how each variable responds to its parents — which is exactly what lets you simulate a `do()` intervention ("what if the bridge goes down?") instead of guessing. This plugin applies that idea to codebases at coarse granularity:

- **Nodes** are architectural entities — services, modules, data stores, flags, tuning constants, SLOs — never individual statements or variables.
- **Edges** are typed causal claims (`calls`, `data-flow`, `fails-if`, `slows-when`, `configures`, `covers`) with evidence attached to every one.
- **Equations** (`model:` blocks) are added only where the functional form is evidenced, so interventions can be *simulated*, not just traced. Everything else propagates as `unknown` — an explicit request for human review, never an invented number.

The result is a codebase index that doubles as a reasoning engine: blast-radius analysis, edit-order planning, diff review, affected-test selection, and `do()` simulations, all computed from the graph rather than hallucinated from prose. The model lives in the repo at `.codebase-scm/`, so it is versioned, reviewable, and CI-checkable alongside the code — and every node/edge carries evidence, so staleness is detectable by construction.

## What's inside

```
codebase-scm/
├── .claude-plugin/plugin.json   # plugin manifest
├── skills/
│   ├── scm-setup/SKILL.md       # build, extend, validate the model
│   └── scm-usage/SKILL.md       # consult it: plan, review, simulate, sync
├── scripts/
│   ├── propagate.py             # --set/--trace/--fail/--tests runner
│   ├── validate.py              # deterministic SCM validator
│   └── example-scm.yaml         # minimal runnable example
└── templates/scm.template.yaml  # starting template for a new SCM
```

The two skills split the job: **scm-setup** owns the model itself (granularity levels L1 landscape → L4 function, incremental build loop, file format, validation, equation-coverage rule); **scm-usage** owns working with it (consult mode, in-session maintenance when code changes, simulations).

## Requirements

- An agentic coding tool that can read files and run shell commands (see installs below).
- Python 3 + PyYAML for the scripts (`python3 -m pip install pyyaml`).

## Quickstart — any coding agent

The plugin is a standard agent-skills layout and needs no special runtime: each skill is a `SKILL.md` prompt plus plain-Python scripts.

**Claude Code** — install as a plugin:
```bash
# from a local checkout:
claude --plugin-dir ./codebase-scm
# or add it to a skills directory; it loads as codebase-scm@skills-dir
```
This gives you `/codebase-scm:scm-setup` and `/codebase-scm:scm-usage`.

**OpenCode (and similar skills-aware agents)** — point the agent at the skill files:
```bash
git clone https://github.com/jackaldenryan/codebase-scm
# then tell the agent: "load skills from ./codebase-scm/skills/ and follow them"
```
Only `SKILL.md` prompting plus `python3 scripts/*.py` is required — there is nothing Claude-specific in the tooling.

**Any other agent (or no agent)** — read `skills/scm-setup/SKILL.md` and follow it by hand; run the scripts directly:
```bash
cp templates/scm.template.yaml myrepo/.codebase-scm/L1-landscape/scm.yaml
# ... fill it in per the skill ...
python3 scripts/validate.py myrepo/.codebase-scm/L1-landscape/scm.yaml
python3 scripts/propagate.py myrepo/.codebase-scm/L1-landscape/scm.yaml --set some-service=false
```

## Five-minute tour

```bash
# 1. Validate the bundled example
python3 scripts/validate.py scripts/example-scm.yaml
# nodes: 4, edges: 4 / PASS: SCM valid

# 2. Simulate an intervention (Pearl do-operator: clamp + re-evaluate downstream)
python3 scripts/propagate.py scripts/example-scm.yaml --set inventory=false
# Changed: inventory, checkout, checkout-p99 ...

# 3. Trace what something calls, propagate a failure, select affected tests
python3 scripts/propagate.py scripts/example-scm.yaml --trace cart
python3 scripts/propagate.py scripts/example-scm.yaml --fail cart
python3 scripts/propagate.py scripts/example-scm.yaml --tests cart
```

Then build a real one: invoke **scm-setup** inside your repository, confirm a granularity level (start with L1 landscape), and follow the incremental build loop — one bounded area at a time, evidence on everything, `validate.py` after every write.

## Design rules (the short version)

- Small honest model beats large stale one. If in doubt, leave it out.
- Every node and edge carries `evidence`; uncertainty goes in `open_questions`, never in invented quantities.
- Equations only where evidenced. Unmodeled nodes propagate as `unknown`, each naming its `unmodeled_reason` (`needs-parents`, `grammar-limited`, `unmeasured`, `deferred`).
- The graph is advisory — code wins disagreements, then the graph gets updated in the same session.
- Causal edges form a DAG; cross-level links are `refines` only.

## Versioning

`version` in `.claude-plugin/plugin.json` bumps on rule or tooling changes (currently 1.1.0). SCM files you build carry their own `changelog` entries per change.

## Contributing

Issues and PRs welcome — especially new `config`-constant patterns from other stacks, runner modes for other edge types, and benchmark results from paired skill-on/skill-off evaluations. Please keep the skill free of any single codebase's vocabulary; examples should span domains or be omitted.

## License

MIT — see [LICENSE](LICENSE).

## References

- Pearl, J. *Causality: Models, Reasoning, and Inference*. Cambridge, 2000.
- Pearl, J. & Mackenzie, D. *The Book of Why*. Basic Books, 2018.
