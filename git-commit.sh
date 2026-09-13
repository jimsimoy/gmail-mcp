#!/bin/bash
#
# Stage everything, commit, scan, push.
# Usage: ./git-commit.sh "commit message"

set -uo pipefail
cd "$(dirname "$0")" || exit 1

if [[ $# -lt 1 ]]; then
  echo "Usage: ./git-commit.sh \"commit message\"" >&2
  exit 1
fi

COMMIT_MESSAGE="$1"

source ./git-guard.sh

# Check the message before it becomes a commit — a bad one is far more
# annoying to remove afterwards than to retype now.
if ! guard_scan "commit message" "$COMMIT_MESSAGE"; then
  echo
  echo "  Commit aborted. Nothing has been staged or committed."
  exit 1
fi

git add .
git commit -m "$COMMIT_MESSAGE" || exit 1

./git-push-current.sh
