
#!/usr/bin/env bash

# Validate a repository's protect-main ruleset against
# the version-controlled governance policy.
#
# Exit codes:
#   0 - Compliant
#   1 - Configuration drift or missing ruleset
#   2 - Operational or input error

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
    echo "Usage: $0 OWNER/REPOSITORY"
}

# --------------------------------------------------
# Validate input
# --------------------------------------------------

if [[ $# -ne 1 ]]; then
    usage
    exit 2
fi

REPOSITORY="$1"
RULESET_NAME="protect-main"

if [[ ! "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    fail "Repository must use OWNER/REPOSITORY format."
fi

# --------------------------------------------------
# Validate dependencies
# --------------------------------------------------

for dependency in gh python3 diff mktemp; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        fail "Required command not found: $dependency"
    fi
done

# --------------------------------------------------
# Locate desired policy
# --------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
    || fail "Unable to locate script directory."

POLICY_FILE="${SCRIPT_DIR}/../policies/protect-main.json"

if [[ ! -f "$POLICY_FILE" ]]; then
    fail "Policy file not found: $POLICY_FILE"
fi

# --------------------------------------------------
# Create temporary workspace
# --------------------------------------------------

WORK_DIR="$(mktemp -d)" \
    || fail "Unable to create temporary workspace."

trap 'rm -rf -- "$WORK_DIR"' EXIT

DESIRED="${WORK_DIR}/desired.json"
ACTUAL="${WORK_DIR}/actual.json"
RAW_ACTUAL="${WORK_DIR}/actual-raw.json"

# --------------------------------------------------
# Normalize desired configuration
# --------------------------------------------------

python3 -m json.tool --sort-keys \
    "$POLICY_FILE" > "$DESIRED" \
    || fail "Desired policy contains invalid JSON."

# --------------------------------------------------
# Discover repository ruleset
# --------------------------------------------------

info "Repository: $REPOSITORY"
info "Desired ruleset: $RULESET_NAME"
info "Discovering existing ruleset."

RULESET_ID="$(
    gh api --hostname github.com --method GET --paginate \
        "repos/${REPOSITORY}/rulesets?includes_parents=false&per_page=100" \
        --jq '.[] | select(.name == "protect-main") | .id'
)" || fail "Unable to retrieve repository rulesets."

if [[ -z "$RULESET_ID" ]]; then
    echo "[NON-COMPLIANT] Required ruleset does not exist."
    exit 1
fi

if [[ ! "$RULESET_ID" =~ ^[0-9]+$ ]]; then
    fail "Expected exactly one matching numeric ruleset ID."
fi

info "Discovered ruleset ID: $RULESET_ID"

# --------------------------------------------------
# Retrieve actual configuration
# --------------------------------------------------

info "Retrieving actual governance configuration."

gh api --hostname github.com --method GET \
    "repos/${REPOSITORY}/rulesets/${RULESET_ID}" \
    --jq '{name, target, enforcement, bypass_actors, conditions, rules}' \
    > "$RAW_ACTUAL" \
    || fail "Unable to retrieve actual ruleset configuration."

# --------------------------------------------------
# Normalize actual configuration
# --------------------------------------------------

python3 -m json.tool --sort-keys \
    "$RAW_ACTUAL" > "$ACTUAL" \
    || fail "Actual ruleset contains invalid JSON."

# --------------------------------------------------
# Compare desired and actual state
# --------------------------------------------------

info "Comparing desired and actual configurations."

if diff -u "$DESIRED" "$ACTUAL"; then
    echo "[PASS] Repository governance is compliant."
    echo "[PASS] No configuration drift detected."
    exit 0
else
    RESULT=$?

    if [[ "$RESULT" -eq 1 ]]; then
        echo "[FAIL] Governance configuration drift detected."
        exit 1
    fi

    fail "Unable to compare governance configurations."
fi
