#!/bin/bash
#
# Pre-flight scan. Sourced by git-push-current.sh and git-commit.sh.
#
# This repo is PRIVATE, but the scan still runs before every push. A repo's
# visibility can change, a collaborator can be added, or a fork can happen —
# and this repo specifically carries the code that talks to a real Gmail
# inbox, so a leaked refresh token or client secret here is a mail account
# compromise, not a hypothetical. Never rely on "it's private" as the only
# control.
#
# Two layers:
#   1. Generic patterns below — credentials, keys, private hosts, IPs, emails.
#   2. .git-deny-patterns (gitignored, optional) — one regex per line, for names
#      that must never appear here but that would themselves be a leak if they
#      were hardcoded in a script. Create it locally; it never ships.
#
# NOTE: this scan is never truncated. A partial scan reported as an all-clear is
# how credentials reached repos' history in the first place.

set -uo pipefail

GUARD_GENERIC=(
  '(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{6,}'
  '(http_basic_auth|basic_auth|htpasswd)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']+["'"'"']'
  '["'"'"'][A-Za-z0-9]{6,}:[A-Za-z0-9]{6,}["'"'"']'   # a quoted user:pass pair
  'AIza[0-9A-Za-z_-]{20,}'                            # Google API key
  '[0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com'  # Google OAuth client ID
  'GOCSPX-[0-9A-Za-z_-]{20,}'                         # Google OAuth client secret
  '1//[0-9A-Za-z_-]{30,}'                             # Google OAuth refresh token
  'ya29\.[0-9A-Za-z_-]{20,}'                          # Google OAuth access token
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'https?://[^/[:space:]]+:[^@/[:space:]]+@'          # user:pass@host
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'    # email addresses
  '\b[a-z0-9-]+\.local\b'                             # private dev hostnames
  '\b(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})(\.(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}\b'  # IPv4
)

# Lines that legitimately contain a trigger word. Keep this list short and specific.
GUARD_ALLOW='example\.(com|local|org)|your-site|mysite\.local|user@example|placeholder|<[A-Z_]+>|@param|@return|git@github\.com:jimsimoy/|noreply@anthropic\.com|mail\.gmail\.com|127\.0\.0\.1|fake-[a-z-]+|os\.environ\.get\("GMAIL_[A-Z_]+"\)|@pytest\.|@mcp\.'

guard_scan() {
  local subject="$1" content="$2" found=0 pat line

  local hits=""
  for pat in "${GUARD_GENERIC[@]}"; do
    line=$(printf '%s\n' "$content" | grep -inE -e "$pat" 2>/dev/null | grep -vE "$GUARD_ALLOW")
    [[ -n "$line" ]] && hits+="$line"$'\n'
  done
  if [[ -n "${hits// /}" ]]; then
    printf '\n  ✖ %s — possible sensitive content:\n' "$subject"
    printf '%s' "$hits" | sort -u -t: -k1,1n | sed 's/^/      /'
    found=1
  fi

  if [[ -f .git-deny-patterns ]]; then
    local denied=""
    while IFS= read -r pat; do
      [[ -z "$pat" || "$pat" == \#* ]] && continue
      line=$(printf '%s\n' "$content" | grep -inE -e "$pat" 2>/dev/null)
      [[ -n "$line" ]] && denied+="$line"$'\n'
    done < .git-deny-patterns
    if [[ -n "${denied// /}" ]]; then
      printf '\n  ✖ %s — matches a local deny pattern:\n' "$subject"
      printf '%s' "$denied" | sort -u -t: -k1,1n | sed 's/^/      /'
      found=1
    fi
  fi

  return $found
}

guard_check() {
  local rc=0

  echo "Pre-flight scan…"

  # Everything about to be pushed: the diff against the remote, or the whole tree
  # if the branch has no upstream yet.
  local branch diff
  branch=$(git branch --show-current)
  if git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
    diff=$(git diff "origin/$branch..HEAD" 2>/dev/null)
  else
    diff=$(git grep -I --no-color -n '' HEAD 2>/dev/null)
  fi
  guard_scan "outgoing changes" "$diff" || rc=1

  # Commit messages travel too, and are easy to forget.
  local msgs
  if git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
    msgs=$(git log --format='%s%n%b' "origin/$branch..HEAD" 2>/dev/null)
  else
    msgs=$(git log --format='%s%n%b' 2>/dev/null)
  fi
  guard_scan "commit messages" "$msgs" || rc=1

  # .env and any token cache must never be tracked, whatever .gitignore says
  # today — except .env.example, which is the deliberately-blank template
  # meant to be committed (README/AGENTS.md both say `cp .env.example .env`).
  local tracked
  tracked=$(git ls-files | grep -E '^(\.env(\..+)?|token\.json)$' | grep -v '^\.env\.example$' || true)
  if [[ -n "$tracked" ]]; then
    printf '\n  ✖ credential files are tracked — these hold live Gmail access:\n'
    printf '%s\n' "$tracked" | sed 's/^/      /'
    rc=1
  fi

  if [[ $rc -ne 0 ]]; then
    cat <<'MSG'

  ─────────────────────────────────────────────────────────────────────
  Push stopped. Review each line above.

  If a hit is a false positive, add a narrow exception to GUARD_ALLOW in
  git-guard.sh. Do not disable the scan.

  If it is real: remove it, and treat the value as compromised — rotate
  it (revoke the OAuth client / refresh token at
  https://myaccount.google.com/permissions). Rewriting history later
  does not unpublish anything once it has been pushed.
  ─────────────────────────────────────────────────────────────────────
MSG
    return 1
  fi

  echo "  ✔ clean — no credentials, private hosts or denied terms found"
  return 0
}
