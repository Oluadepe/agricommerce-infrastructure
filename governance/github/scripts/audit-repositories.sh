#!/usr/bin/env bash

# Centralized governance compliance audit.
#
# Exit codes:
#   0 - All repositories compliant.
#   1 - One or more repositories non-compliant.
#   2 - One or more operational or input errors.
#
# Operational errors take precedence over
# non-compliance when calculating the final status.

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

# --------------------------------------------------
# Validate input
# --------------------------------------------------

if [[ $# -ne 0 ]]; then
    fail "Usage: $0"
fi

# --------------------------------------------------
# Locate validation script
# --------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
    || fail "Unable to locate script directory."

VALIDATOR="${SCRIPT_DIR}/validate-ruleset.sh"

if [[ ! -x "$VALIDATOR" ]]; then
    fail "Validator is missing or not executable: $VALIDATOR"
fi

# --------------------------------------------------
# Managed repository inventory
# --------------------------------------------------

REPOSITORIES=(
    "Oluadepe/agricommerce-platform"
    "Oluadepe/agricommerce-app"
    "Oluadepe/agricommerce-infrastructure"
    "Oluadepe/agricommerce-gitops"
)

# --------------------------------------------------
# Initialize audit counters
# --------------------------------------------------

TOTAL="${#REPOSITORIES[@]}"

COMPLIANT=0
NON_COMPLIANT=0
ERRORS=0

FAILED_REPOS=()
ERROR_REPOS=()

# --------------------------------------------------
# Begin governance audit
# --------------------------------------------------

printf '\n'
printf '%s\n' "========================================"
printf '%s\n' " AgriCommerce Repository Governance Audit"
printf '%s\n' "========================================"

info "Repositories to audit: $TOTAL"

# --------------------------------------------------
# Validate each repository
# --------------------------------------------------

for repository in "${REPOSITORIES[@]}"; do

    printf '\n'
    printf '%s\n' "----------------------------------------"

    info "Auditing repository: $repository"

    # Capture the validator's exit status without
    # allowing strict mode to terminate the audit.

    if "$VALIDATOR" "$repository"; then
        RESULT=0
    else
        RESULT=$?
    fi

    # Classify the validation result.

    case "$RESULT" in

        0)
            COMPLIANT=$((COMPLIANT + 1))

            printf '[PASS] %s\n' "$repository"
            ;;

        1)
            NON_COMPLIANT=$((NON_COMPLIANT + 1))

            FAILED_REPOS+=("$repository")

            printf '[NON-COMPLIANT] %s\n' "$repository"
            ;;

        2)
            ERRORS=$((ERRORS + 1))

            ERROR_REPOS+=("$repository")

            printf '[ERROR] %s\n' "$repository" >&2
            ;;

        *)
            ERRORS=$((ERRORS + 1))

            ERROR_REPOS+=("$repository")

            printf '[ERROR] Unexpected exit code %s: %s\n' \
                "$RESULT" "$repository" >&2
            ;;

    esac

done

# --------------------------------------------------
# Generate consolidated audit report
# --------------------------------------------------

printf '\n'
printf '%s\n' "========================================"
printf '%s\n' " Governance Audit Summary"
printf '%s\n' "========================================"

printf '%-24s %d\n' "Total repositories:" "$TOTAL"
printf '%-24s %d\n' "Compliant:" "$COMPLIANT"
printf '%-24s %d\n' "Non-compliant:" "$NON_COMPLIANT"
printf '%-24s %d\n' "Operational errors:" "$ERRORS"

# --------------------------------------------------
# Report non-compliant repositories
# --------------------------------------------------

if (( NON_COMPLIANT > 0 )); then

    printf '\nNon-compliant repositories:\n'

    for repository in "${FAILED_REPOS[@]}"; do
        printf '  - %s\n' "$repository"
    done

fi

# --------------------------------------------------
# Report repositories with operational errors
# --------------------------------------------------

if (( ERRORS > 0 )); then

    printf '\nRepositories with operational errors:\n'

    for repository in "${ERROR_REPOS[@]}"; do
        printf '  - %s\n' "$repository"
    done

fi

# --------------------------------------------------
# Determine final audit exit status
# --------------------------------------------------

printf '\n'

if (( ERRORS > 0 )); then

    printf '[ERROR] Audit completed with operational errors.\n' >&2

    exit 2

elif (( NON_COMPLIANT > 0 )); then

    printf '[FAIL] Governance audit detected non-compliance.\n'

    exit 1

else

    printf '[PASS] All managed repositories are compliant.\n'

    exit 0

fi
