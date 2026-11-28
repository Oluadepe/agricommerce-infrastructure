
"""Offline regression tests for multi-repository governance audits."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

AUDIT_SCRIPT = (
    ROOT
    / "governance"
    / "github"
    / "scripts"
    / "audit-repositories.sh"
)

REPOSITORIES = (
    "Oluadepe/agricommerce-platform",
    "Oluadepe/agricommerce-app",
    "Oluadepe/agricommerce-infrastructure",
    "Oluadepe/agricommerce-gitops",
)

MOCK_VALIDATOR = """#!/usr/bin/env bash

set -euo pipefail

printf '[MOCK] Validating %s\\n' "$1"

case "${MOCK_SCENARIO:?Missing scenario}:$1" in

    drift:Oluadepe/agricommerce-app)
        exit 1
        ;;

    error:Oluadepe/agricommerce-infrastructure)
        exit 2
        ;;

    mixed:Oluadepe/agricommerce-app)
        exit 1
        ;;

    mixed:Oluadepe/agricommerce-infrastructure)
        exit 2
        ;;

    unexpected:Oluadepe/agricommerce-app)
        exit 99
        ;;

    *)
        exit 0
        ;;

esac
"""


class AuditAutomationTests(unittest.TestCase):

    # ----------------------------------------------
    # Isolated test environment
    # ----------------------------------------------

    def setUp(self):
        workspace = tempfile.TemporaryDirectory(
            prefix="governance-audit-test-"
        )

        self.addCleanup(workspace.cleanup)

        self.workspace = Path(workspace.name)

        self.audit = (
            self.workspace / "audit-repositories.sh"
        )

        self.validator = (
            self.workspace / "validate-ruleset.sh"
        )

        shutil.copy2(AUDIT_SCRIPT, self.audit)

        self.validator.write_text(
            MOCK_VALIDATOR,
            encoding="utf-8"
        )

        self.validator.chmod(0o755)

        self.env = os.environ.copy()

        for token in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GH_ENTERPRISE_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
        ):
            self.env.pop(token, None)

    # ----------------------------------------------
    # Reusable execution helper
    # ----------------------------------------------

    def run_audit(self, scenario, *arguments):
        environment = self.env.copy()

        environment["MOCK_SCENARIO"] = scenario

        return subprocess.run(
            [str(self.audit), *arguments],
            cwd=self.workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15
        )

    # ----------------------------------------------
    # Reusable audit assertions
    # ----------------------------------------------

    def assert_audit(
        self,
        result,
        expected_code,
        compliant,
        non_compliant,
        errors
    ):
        output = result.stdout + result.stderr

        self.assertEqual(
            result.returncode,
            expected_code,
            output
        )

        expected_counts = {
            "Total repositories:": len(REPOSITORIES),
            "Compliant:": compliant,
            "Non-compliant:": non_compliant,
            "Operational errors:": errors,
        }

        for label, count in expected_counts.items():
            with self.subTest(field=label):
                self.assertRegex(
                    result.stdout,
                    rf"(?m)^{re.escape(label)}[ \t]+{count}$"
                )

        self.assertEqual(
            compliant + non_compliant + errors,
            len(REPOSITORIES)
        )

        actual_calls = [
            line.removeprefix("[MOCK] Validating ")
            for line in result.stdout.splitlines()
            if line.startswith("[MOCK] Validating ")
        ]

        self.assertEqual(
            actual_calls,
            list(REPOSITORIES),
            "Every managed repository must be audited once."
        )

    # ----------------------------------------------
    # Audit scenarios
    # ----------------------------------------------

    def test_all_repositories_compliant(self):
        result = self.run_audit("compliant")

        self.assert_audit(result, 0, 4, 0, 0)

        self.assertIn(
            "[PASS] All managed repositories are compliant.",
            result.stdout
        )

    def test_configuration_drift(self):
        result = self.run_audit("drift")

        self.assert_audit(result, 1, 3, 1, 0)

        self.assertIn(
            "Non-compliant repositories:\n"
            "  - Oluadepe/agricommerce-app",
            result.stdout
        )

    def test_operational_error(self):
        result = self.run_audit("error")

        self.assert_audit(result, 2, 3, 0, 1)

        self.assertIn(
            "Repositories with operational errors:\n"
            "  - Oluadepe/agricommerce-infrastructure",
            result.stdout
        )

    def test_mixed_failures(self):
        result = self.run_audit("mixed")

        self.assert_audit(result, 2, 2, 1, 1)

        self.assertIn(
            "Non-compliant repositories:\n"
            "  - Oluadepe/agricommerce-app",
            result.stdout
        )

        self.assertIn(
            "Repositories with operational errors:\n"
            "  - Oluadepe/agricommerce-infrastructure",
            result.stdout
        )

    def test_unexpected_validator_exit_code(self):
        result = self.run_audit("unexpected")

        self.assert_audit(result, 2, 3, 0, 1)

        self.assertIn(
            "Unexpected exit code 99",
            result.stderr
        )

    # ----------------------------------------------
    # Input and dependency failure scenarios
    # ----------------------------------------------

    def test_invalid_arguments(self):
        result = self.run_audit(
            "compliant",
            "--invalid"
        )

        self.assertEqual(result.returncode, 2)

        self.assertIn(
            "Usage:",
            result.stderr
        )

        self.assertNotIn(
            "[MOCK] Validating",
            result.stdout
        )

    def test_missing_validator(self):
        self.validator.unlink()

        result = self.run_audit("compliant")

        self.assertEqual(result.returncode, 2)

        self.assertIn(
            "Validator is missing or not executable",
            result.stderr
        )

        self.assertNotIn(
            "[MOCK] Validating",
            result.stdout
        )

    def test_non_executable_validator(self):
        self.validator.chmod(0o644)

        result = self.run_audit("compliant")

        self.assertEqual(result.returncode, 2)

        self.assertIn(
            "Validator is missing or not executable",
            result.stderr
        )

        self.assertNotIn(
            "[MOCK] Validating",
            result.stdout
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
