#!/usr/bin/env bash
# codebase-scm installer — no git clone, no pip package required.
#
#   curl -fsSL https://raw.githubusercontent.com/jackaldenryan/codebase-scm/main/install.sh | bash
#
# Options (pass after -- when piping, e.g. `| bash -s -- --dir <path>`):
#   --dir <path>   install destination (default: ~/.agents/skills/codebase-scm)
#   --ref <ref>    git ref to download: branch, tag, or commit (default: main)
#
# Layout note: the destination keeps the plugin layout (skills/, scripts/,
# templates/, .claude-plugin/, README.md, LICENSE). For Claude Code, use
#   --dir ~/.claude/skills/codebase-scm
# and it loads as a plugin (codebase-scm@skills-dir) with both skills.
set -euo pipefail

REPO="jackaldenryan/codebase-scm"
REF="main"
DEST="${HOME}/.agents/skills/codebase-scm"

usage() {
  echo "usage: install.sh [--dir <path>] [--ref <ref>]"
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DEST="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

for cmd in curl tar python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

TMPDIR_X="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_X"' EXIT INT TERM

echo "downloading ${REPO}@${REF} ..."
curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF}" -o "$TMPDIR_X/pkg.tar.gz"
tar -xzf "$TMPDIR_X/pkg.tar.gz" -C "$TMPDIR_X" --strip-components=1

for d in skills scripts templates .claude-plugin; do
  [ -d "$TMPDIR_X/$d" ] || { echo "downloaded archive missing $d (ref: $REF)" >&2; exit 1; }
done

mkdir -p "$DEST"
cp -r "$TMPDIR_X/skills" "$TMPDIR_X/scripts" "$TMPDIR_X/templates" "$TMPDIR_X/.claude-plugin" "$DEST/"
cp "$TMPDIR_X/README.md" "$TMPDIR_X/LICENSE" "$DEST/" 2>/dev/null || true

echo "installed to $DEST"

if python3 -c "import yaml" 2>/dev/null; then
  # self-check: --allow-stale since the bundled example is pinned to its own date
  if python3 "$DEST/scripts/validate.py" "$DEST/scripts/example-scm.yaml" --allow-stale 2>&1 | tail -n 1; then
    echo "self-check passed (example SCM validates)"
  fi
else
  echo "note: scripts need PyYAML at use time: python3 -m pip install pyyaml"
fi

cat <<EOF

next steps:
  - Skills live at: $DEST/skills/{scm-setup,scm-usage}/SKILL.md
  - Scripts live at: $DEST/scripts/ (propagate.py, validate.py)
  - Claude Code: reinstall with --dir ~/.claude/skills/codebase-scm to load as a plugin,
    or pass --plugin-dir <this-dir> to claude.
  - OpenCode / others: tell the agent to load skills from $DEST/skills/
EOF
