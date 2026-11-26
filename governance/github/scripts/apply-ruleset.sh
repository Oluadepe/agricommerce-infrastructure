
#!/usr/bin/env bash

# GitHub repository governance reconciliation.
#
# Modes:
#   --plan       Preview changes without modifying GitHub.
#   --apply      Create missing ruleset; block existing drift.
#   --reconcile  Create or update ruleset.
#
# Exit codes:
#   0 - Successful plan, no change, or verified application.
#   1 - Existing drift requires explicit reconciliation.
#   2 - Input or operational error.

set -euo pipefail

# --------------------------------------------------
# Helper functions
# --------------------------------------------------

info() {
    printf '[INFO] %s\n' "$*"
}

fail() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 2
}

usage() {
    printf 'Usage: %s OWNER/REPOSITORY [--plan|--apply|--reconcile]\n' "$0"
}

# --------------------------------------------------
# Validate arguments
# --------------------------------------------------

if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage >&2
    exit 2
fi

REPOSITORY="$1"
MODE="${2:---plan}"

if [[ ! "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    fail "Invalid repository format."
fi

case "$MODE" in
    --plan|--apply|--reconcile)
        ;;
    *)
        fail "Unknown mode: $MODE"
        ;;
esac

# --------------------------------------------------
# Restrict write operations
# --------------------------------------------------

if [[ "$MODE" != --plan ]]; then
    case "$REPOSITORY" in
        Oluadepe/agricommerce-platform|\
        Oluadepe/agricommerce-app|\
        Oluadepe/agricommerce-infrastructure|\
        Oluadepe/agricommerce-gitops)
            ;;
        *)
            fail "Write operations are restricted to AgriCommerce repositories."
            ;;
    esac
fi

# --------------------------------------------------
# Validate dependencies
# --------------------------------------------------

for dependency in gh python3 diff mktemp dirname rm; do
    command -v "$dependency" >/dev/null 2>&1 \
        || fail "Missing dependency: $dependency"
done

# --------------------------------------------------
# Locate policy and initialize workspace
# --------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
    || fail "Cannot locate script."

POLICY_FILE="$SCRIPT_DIR/../policies/protect-main.json"

WORK_DIR="$(mktemp -d)" \
    || fail "Cannot create temporary directory."

trap 'rm -rf -- "$WORK_DIR"' EXIT

DESIRED="$WORK_DIR/desired.json"
ACTUAL="$WORK_DIR/actual.json"
RAW_ACTUAL="$WORK_DIR/actual-raw.json"

API_PATH="repos/$REPOSITORY/rulesets"

# --------------------------------------------------
# Normalize and validate policy structure
# --------------------------------------------------

normalize_policy() {
    python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as file:
    policy = json.load(file)

expected = {
    "name": str,
    "target": str,
    "enforcement": str,
    "bypass_actors": list,
    "conditions": dict,
    "rules": list,
}

if not isinstance(policy, dict) or set(policy) != set(expected):
    raise SystemExit("Unexpected policy fields")

if any(not isinstance(policy[key], kind) for key, kind in expected.items()):
    raise SystemExit("Missing or invalid policy values")

print(json.dumps(policy, indent=4, sort_keys=True))
PY
}

normalize_policy "$POLICY_FILE" > "$DESIRED" \
    || fail "Cannot validate desired policy."

# --------------------------------------------------
# Discover and compare actual state
# --------------------------------------------------

inspect_state() {
    RULESET_ID="$(
        gh api --hostname github.com --method GET --paginate \
            "${API_PATH}?includes_parents=false&per_page=100" \
            --jq '.[] | select(.name == "protect-main") | .id'
    )" || fail "Ruleset discovery failed. No changes attempted by this step."

    if [[ -z "$RULESET_ID" ]]; then
        ACTION=CREATE
        return 0
    fi

    if [[ ! "$RULESET_ID" =~ ^[0-9]+$ ]]; then
        fail "Expected one numeric ruleset ID."
    fi

    gh api --hostname github.com --method GET \
        "$API_PATH/$RULESET_ID" \
        --jq '{name, target, enforcement, bypass_actors, conditions, rules}' \
        > "$RAW_ACTUAL" \
        || fail "Cannot retrieve ruleset."

    normalize_policy "$RAW_ACTUAL" > "$ACTUAL" \
        || fail "Cannot validate actual policy."

    if diff -u \
        --label desired-policy \
        --label actual-policy \
        "$DESIRED" "$ACTUAL"; then

        ACTION=NOOP
    else
        local result=$?

        [[ "$result" -eq 1 ]] \
            || fail "Policy comparison failed."

        ACTION=UPDATE
    fi
}

# --------------------------------------------------
# Generate execution plan
# --------------------------------------------------

info "Repository: $REPOSITORY"
info "Mode: $MODE"

inspect_state

info "Required action: $ACTION"

# --------------------------------------------------
# Handle compliant configuration
# --------------------------------------------------

if [[ "$ACTION" == NOOP ]]; then
    echo '[PASS] Already compliant. No changes required.'
    exit 0
fi

# --------------------------------------------------
# Handle read-only plan
# --------------------------------------------------

if [[ "$MODE" == --plan ]]; then
    echo '[PLAN] No changes applied.'
    exit 0
fi

# --------------------------------------------------
# Require explicit reconciliation for updates
# --------------------------------------------------

if [[ "$ACTION" == UPDATE && "$MODE" != --reconcile ]]; then
    echo '[BLOCKED] Existing policy differs. Review plan before using --reconcile.' >&2
    exit 1
fi

# --------------------------------------------------
# Execute authorized changes
# --------------------------------------------------

if [[ "$ACTION" == CREATE ]]; then
    info 'Creating ruleset.'

    gh api --hostname github.com --method POST \
        "$API_PATH" \
        --input "$DESIRED" >/dev/null \
        || fail 'Create request failed. Inspect remote state before retrying.'
else
    info "Updating ruleset: $RULESET_ID"

    gh api --hostname github.com --method PUT \
        "$API_PATH/$RULESET_ID" \
        --input "$DESIRED" >/dev/null \
        || fail 'Update request failed. Inspect remote state before retrying.'
fi

# --------------------------------------------------
# Verify final remote state
# --------------------------------------------------

info 'Verifying resulting remote state.'

inspect_state

[[ "$ACTION" == NOOP ]] \
    || fail 'Post-change verification failed.'

echo '[PASS] Desired governance policy verified.'
