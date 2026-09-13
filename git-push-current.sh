#!/bin/bash
#
# Push the current branch, but only after the pre-flight scan passes.
# See git-guard.sh for why the check runs first even on a private repo.

set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./git-guard.sh

guard_check || exit 1

BRANCH=$(git branch --show-current)
echo
echo "Pushing ${BRANCH} → origin…"
git push origin "$BRANCH"
