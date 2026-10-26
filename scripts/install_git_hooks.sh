#!/usr/bin/env bash
# Install local git hooks:
#   - pre-commit: gitleaks secret scan (INFRA-003)
#   - commit-msg: strip AI agent attribution trailers (Cursor / Claude / Codex)
# Requires gitleaks on PATH for the secret scan (https://github.com/gitleaks/gitleaks).
# Idempotent.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"

pre="$hooks/pre-commit"
cat > "$pre" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install_git_hooks.sh
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "pre-commit: gitleaks not installed; skipping secret scan (CI still scans)." >&2
  exit 0
fi
cfg="$(git rev-parse --show-toplevel)/.gitleaks.toml"
if [ -f "$cfg" ]; then
  exec gitleaks git --pre-commit --staged --no-banner --redact --config "$cfg"
fi
exec gitleaks git --pre-commit --staged --no-banner --redact  # branches without the config
HOOK
chmod +x "$pre"
echo "installed $pre"

msg="$hooks/commit-msg"
# Prefer the tracked strip script so updates apply after pull.
strip="$root/scripts/strip_ai_commit_attribution.sh"
chmod +x "$strip"
cat > "$msg" <<HOOK
#!/usr/bin/env bash
# Installed by scripts/install_git_hooks.sh — strips AI agent attribution trailers.
exec "$strip" "\$@"
HOOK
chmod +x "$msg"
echo "installed $msg"
