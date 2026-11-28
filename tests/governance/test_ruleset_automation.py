
"""Offline regression tests for GitHub governance automation."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REPOSITORY = "Oluadepe/agricommerce-gitops"

MOCK_GH = """#!/usr/bin/env python3

import json
import os
import sys

from pathlib import Path

args = sys.argv[1:]

if not args or args[0] != "api":
    sys.exit("Unsupported mock command")

method = args[args.index("--method") + 1]

path = next(
    (arg.split("?")[0] for arg in args
     if arg.startswith("repos/")),
    ""
)

base = "repos/Oluadepe/agricommerce-gitops/rulesets"

operations = {
    ("GET", base): "list",
    ("GET", base + "/12345"): "detail",
    ("POST", base): "create",
    ("PUT", base + "/12345"): "update",
}

operation = operations.get((method, path))

if operation is None:
    sys.exit("Unexpected mock API request")

state_file = Path(os.environ["MOCK_STATE"])
log_file = Path(os.environ["MOCK_LOG"])
failure = os.environ.get("MOCK_FAIL", "")

with log_file.open("a") as log:
    log.write(method + " " + operation + "\\n")

if failure == operation:
    print(
        "Simulated GitHub API failure",
        file=sys.stderr
    )
    sys.exit(1)

state = json.loads(state_file.read_text())

if operation == "list":
    if state is not None:
        print(12345)

elif operation == "detail":
    if state is None:
        sys.exit("Ruleset not found")

    print(json.dumps(state))

elif operation in ("create", "update"):
    if operation == "create" and state is not None:
        sys.exit("Ruleset already exists")

    if operation == "update" and state is None:
        sys.exit("Ruleset does not exist")

    payload = Path(
        args[args.index("--input") + 1]
    )

    policy = json.loads(payload.read_text())

    if failure != "ignore_write":
        state_file.write_text(json.dumps(policy))

    print(json.dumps(policy))
"""

class GovernanceTests(unittest.TestCase):

    def setUp(self):
        workspace = tempfile.TemporaryDirectory(
            prefix="governance-test-"
        )

        self.addCleanup(workspace.cleanup)

        self.workspace = Path(workspace.name)

        self.gov = self.workspace / "governance"

        shutil.copytree(
            ROOT / "governance" / "github",
            self.gov
        )

        self.policy_file = (
            self.gov / "policies" / "protect-main.json"
        )

        self.policy = json.loads(
            self.policy_file.read_text()
        )

        self.state_file = self.workspace / "state.json"
        self.log_file = self.workspace / "api.log"

        self.state_file.write_text("null")
        self.log_file.write_text("")

        mock_bin = self.workspace / "bin"
        mock_bin.mkdir()

        mock_gh = mock_bin / "gh"
        mock_gh.write_text(MOCK_GH)
        mock_gh.chmod(0o755)

        self.env = os.environ.copy()

        for token in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GH_ENTERPRISE_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
        ):
            self.env.pop(token, None)

        self.env["PATH"] = (
            str(mock_bin)
            + os.pathsep
            + self.env.get("PATH", "")
        )

        self.env["MOCK_STATE"] = str(self.state_file)
        self.env["MOCK_LOG"] = str(self.log_file)
        self.env["MOCK_FAIL"] = ""

        self.assertEqual(
            shutil.which(
                "gh",
                path=self.env["PATH"]
            ),
            str(mock_gh)
        )

    def set_state(self, policy):
        self.state_file.write_text(
            json.dumps(policy)
        )

    def get_state(self):
        return json.loads(
            self.state_file.read_text()
        )

    def drifted_policy(self):
        policy = copy.deepcopy(self.policy)

        for rule in policy["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"][
                    "allowed_merge_methods"
                ].remove("rebase")

        return policy

    def run_script(self, name, *arguments):
        script = self.gov / "scripts" / name

        return subprocess.run(
            [str(script), *arguments],
            cwd=self.workspace,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15
        )

    def assert_result(self, result, code, message):
        output = result.stdout + result.stderr

        self.assertEqual(
            result.returncode,
            code,
            output
        )

        self.assertIn(message, output)

    def api_calls(self):
        return self.log_file.read_text().splitlines()

    def write_calls(self):
        return [
            call for call in self.api_calls()
            if call.startswith(("POST ", "PUT "))
        ]

    # ----------------------------------------------
    # Validator tests
    # ----------------------------------------------

    def test_validator_compliant(self):
        self.set_state(self.policy)

        result = self.run_script(
            "validate-ruleset.sh",
            REPOSITORY
        )

        self.assert_result(result, 0, "compliant")
        self.assertEqual(self.write_calls(), [])

    def test_validator_missing_ruleset(self):
        result = self.run_script(
            "validate-ruleset.sh",
            REPOSITORY
        )

        self.assert_result(
            result, 1, "does not exist"
        )

        self.assertEqual(self.write_calls(), [])

    def test_validator_detects_drift(self):
        self.set_state(self.drifted_policy())

        result = self.run_script(
            "validate-ruleset.sh",
            REPOSITORY
        )

        self.assert_result(
            result, 1, "drift detected"
        )

        self.assertEqual(self.write_calls(), [])

    # ----------------------------------------------
    # Planning tests
    # ----------------------------------------------

    def test_default_plan_creates_nothing(self):
        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY
        )

        self.assert_result(result, 0, "CREATE")
        self.assertIn("--plan", result.stdout)

        self.assertIsNone(self.get_state())
        self.assertEqual(self.write_calls(), [])

    def test_plan_detects_update(self):
        drift = self.drifted_policy()
        self.set_state(drift)

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--plan"
        )

        self.assert_result(result, 0, "UPDATE")

        self.assertEqual(self.get_state(), drift)
        self.assertEqual(self.write_calls(), [])

    def test_apply_blocks_existing_drift(self):
        drift = self.drifted_policy()
        self.set_state(drift)

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--apply"
        )

        self.assert_result(result, 1, "[BLOCKED]")

        self.assertEqual(self.get_state(), drift)
        self.assertEqual(self.write_calls(), [])

    # ----------------------------------------------
    # Creation and reconciliation tests
    # ----------------------------------------------

    def test_create_and_repeat(self):
        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--apply"
        )

        self.assert_result(
            result, 0, "policy verified"
        )

        self.assertEqual(
            self.get_state(), self.policy
        )

        self.assertEqual(
            self.write_calls(), ["POST create"]
        )

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--apply"
        )

        self.assert_result(result, 0, "NOOP")

        self.assertEqual(
            self.write_calls(), ["POST create"]
        )

    def test_reconcile_and_repeat(self):
        self.set_state(self.drifted_policy())

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--reconcile"
        )

        self.assert_result(
            result, 0, "policy verified"
        )

        self.assertEqual(
            self.get_state(), self.policy
        )

        self.assertEqual(
            self.write_calls(), ["PUT update"]
        )

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--reconcile"
        )

        self.assert_result(result, 0, "NOOP")

        self.assertEqual(
            self.write_calls(), ["PUT update"]
        )

    # ----------------------------------------------
    # Operational failure injection
    # ----------------------------------------------

    def test_discovery_api_failure(self):
        self.env["MOCK_FAIL"] = "list"

        for script, arguments in (
            ("validate-ruleset.sh", ()),
            ("apply-ruleset.sh", ("--apply",)),
        ):
            with self.subTest(script=script):
                result = self.run_script(
                    script,
                    REPOSITORY,
                    *arguments
                )

                self.assert_result(
                    result, 2, "[ERROR]"
                )

        self.assertEqual(self.write_calls(), [])
        self.assertIsNone(self.get_state())

    def test_detail_api_failure(self):
        self.set_state(self.policy)
        self.env["MOCK_FAIL"] = "detail"

        for script, arguments in (
            ("validate-ruleset.sh", ()),
            ("apply-ruleset.sh", ("--reconcile",)),
        ):
            with self.subTest(script=script):
                result = self.run_script(
                    script,
                    REPOSITORY,
                    *arguments
                )

                self.assert_result(
                    result, 2, "[ERROR]"
                )

        self.assertEqual(self.write_calls(), [])

    def test_write_api_failure(self):
        for operation, mode, state in (
            ("create", "--apply", None),
            (
                "update",
                "--reconcile",
                self.drifted_policy()
            ),
        ):
            with self.subTest(operation=operation):
                self.set_state(state)
                self.log_file.write_text("")
                self.env["MOCK_FAIL"] = operation

                result = self.run_script(
                    "apply-ruleset.sh",
                    REPOSITORY,
                    mode
                )

                self.assert_result(
                    result, 2, "request failed"
                )

                self.assertEqual(
                    self.get_state(), state
                )

                self.assertEqual(
                    len(self.write_calls()), 1
                )

    def test_post_change_verification_failure(self):
        self.env["MOCK_FAIL"] = "ignore_write"

        result = self.run_script(
            "apply-ruleset.sh",
            REPOSITORY,
            "--apply"
        )

        self.assert_result(
            result, 2,
            "Post-change verification failed"
        )

        self.assertIsNone(self.get_state())

        self.assertEqual(
            self.write_calls(), ["POST create"]
        )

    # ----------------------------------------------
    # Input and write-scope protection
    # ----------------------------------------------

    def test_invalid_inputs_and_scope(self):
        cases = (
            ("validate-ruleset.sh", ()),
            (
                "validate-ruleset.sh",
                ("invalid-repository",)
            ),
            (
                "apply-ruleset.sh",
                (REPOSITORY, "--destroy")
            ),
            (
                "apply-ruleset.sh",
                ("Oluadepe/other-project", "--apply")
            ),
        )

        for script, arguments in cases:
            with self.subTest(
                script=script,
                arguments=arguments
            ):
                result = self.run_script(
                    script, *arguments
                )

                self.assertEqual(
                    result.returncode, 2
                )

        self.assertEqual(self.api_calls(), [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
