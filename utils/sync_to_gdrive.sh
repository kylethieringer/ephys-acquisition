#!/usr/bin/env bash
# One-way copy from D:/data to Google Drive via rclone.
# Never deletes on the remote (uses `rclone copy`, not `sync`).
#
# First-time setup:
#   1. Install rclone:    winget install Rclone.Rclone
#      (or download from https://rclone.org/downloads/ and add to PATH)
#   2. Configure remote:  rclone config
#        - n) New remote
#        - name: gdrive
#        - storage: drive
#        - accept defaults; complete the browser OAuth flow
#        - confirm and quit
#   3. Verify:            rclone lsd gdrive:
#
# Usage:
#   ./utils/sync_to_gdrive.sh                 # real run
#   ./utils/sync_to_gdrive.sh --dry-run       # preview only, no uploads
#   ./utils/sync_to_gdrive.sh --source <path> --dest <remote:path>

set -euo pipefail

SOURCE="D:/data"
DEST="gdrive:Kyle/data/ephys"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n) DRY_RUN=1; shift ;;
        --source)     SOURCE="$2"; shift 2 ;;
        --dest)       DEST="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0 ;;
        *)  echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if ! command -v rclone >/dev/null 2>&1; then
    echo "rclone not found on PATH. Install with 'winget install Rclone.Rclone' or see https://rclone.org/downloads/" >&2
    exit 1
fi

if [[ ! -e "$SOURCE" ]]; then
    echo "Source path does not exist: $SOURCE" >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"
log_dir="$repo_root/logs"
mkdir -p "$log_dir"

stamp="$(date +%Y%m%d_%H%M%S)"
tag=$([[ $DRY_RUN -eq 1 ]] && echo "dryrun" || echo "run")
log_path="$log_dir/sync_to_gdrive_${stamp}_${tag}.log"

rclone_args=(
    copy "$SOURCE" "$DEST"
    --progress
    --transfers 8
    --checkers 16
    --fast-list
    --drive-chunk-size 64M
    --stats 30s
    --verbose
    --log-file "$log_path"
)
[[ $DRY_RUN -eq 1 ]] && rclone_args+=(--dry-run)

echo "Source : $SOURCE"
echo "Dest   : $DEST"
echo "Log    : $log_path"
echo "Mode   : $([[ $DRY_RUN -eq 1 ]] && echo 'DRY RUN (no uploads)' || echo 'LIVE')"
echo

set +e
rclone "${rclone_args[@]}"
exit_code=$?
set -e

echo
if [[ $exit_code -eq 0 ]]; then
    echo "rclone finished successfully. Log: $log_path"
else
    echo "rclone exited with code $exit_code. See log: $log_path"
fi
exit $exit_code
