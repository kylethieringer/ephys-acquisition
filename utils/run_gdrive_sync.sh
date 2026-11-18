#!/usr/bin/env bash
# Wrapper for the experiment-checklist launcher.
#
# Runs sync_to_gdrive.sh in the current terminal, then holds the window open
# so the final result / any errors stay readable. Launched by
# experiment_checklist.py with Git Bash in a new console window; kept as a separate
# script so the launcher only needs a single bash invocation.
#
# Any arguments are forwarded to sync_to_gdrive.sh (e.g. --dry-run).

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$here/sync_to_gdrive.sh" "$@"
code=$?
echo
read -r -p "Press Enter to close..."
exit "$code"
