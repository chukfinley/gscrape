#!/usr/bin/env bash
# Install gscrape globally: the `gscrape` CLI on PATH and the Claude Code skill
# registered for every project/agent. Idempotent — safe to re-run after a pull.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
SKILL_DIR="${HOME}/.claude/skills/gscrape"

if ! command -v uv >/dev/null 2>&1; then
  echo "gscrape: uv not found — install it first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "→ installing the CLI (editable, so a git pull updates it)"
# --editable keeps the tool pointed at this checkout instead of copying it.
uv tool install --editable --force "${REPO}"

echo "→ registering the Claude Code skill: ${SKILL_DIR}"
mkdir -p "${SKILL_DIR}"
ln -sf "${REPO}/skill/SKILL.md" "${SKILL_DIR}/SKILL.md"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) : ;;
  *) echo "  ! ${BIN_DIR} is not on your PATH — add: export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

echo "✓ done. Try:  gscrape yt search fitness --limit 5"
