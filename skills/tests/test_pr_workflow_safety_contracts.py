from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40


def read_skill(name: str) -> str:
    return (ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def read_pr_contract() -> str:
    return read_skill("pr") + "\n" + (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")


def read_remaining_contract() -> str:
    return read_skill("gh-deliver-remaining-issues") + "\n" + (ROOT / "gh-deliver-remaining-issues" / "references" / "execution-contract.md").read_text(encoding="utf-8")


def read_pr_cli_block() -> str:
    contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
    section = contract.split("<!-- publication-cli-bash-start -->", 1)[1].split("<!-- publication-cli-bash-end -->", 1)[0]
    return section.split("```bash", 1)[1].split("```", 1)[0].strip()


def read_expected_assignees_filter() -> str:
    match = re.search(
        r'expected_assignees_json="\$\(printf \'%s\' "\$publication_manifest_buffer" \| jq -ce \'(.*?)\'\)"',
        read_pr_cli_block(),
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("expected_assignees jq filter not found")
    return match.group(1)


def read_current_user_default_block() -> str:
    contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
    section = contract.split("### Current-user default materialization", 1)[1].split("### Exact-set reconciliation", 1)[0]
    return section.split("```bash", 1)[1].split("```", 1)[0].strip()


def read_publication_intake_filter() -> str:
    contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
    section = contract.split("<!-- publication-intake-jq-start -->", 1)[1].split("<!-- publication-intake-jq-end -->", 1)[0]
    return section.split("~~~jq", 1)[1].split("~~~", 1)[0].strip()


def read_publication_integrity_js() -> str:
    contract = (ROOT / "gh-deliver-remaining-issues" / "references" / "execution-contract.md").read_text(
        encoding="utf-8"
    )
    section = contract.split("<!-- publication-integrity-js-start -->", 1)[1].split(
        "<!-- publication-integrity-js-end -->", 1
    )[0]
    return section.split("```javascript", 1)[1].split("```", 1)[0].strip()


def read_publication_transport_block() -> str:
    cli = read_pr_cli_block()
    return cli.split("# publication-isolated-transport-start", 1)[1].split(
        "# publication-isolated-transport-end", 1
    )[0].strip()


def run_publication_integrity(operation: str, payload: str | dict[str, object]) -> subprocess.CompletedProcess[str]:
    serialized = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return subprocess.run(
        ["node", "-e", read_publication_integrity_js(), operation],
        input=serialized,
        text=True,
        capture_output=True,
        check=False,
    )


def normalized_text_sha256(text: str) -> str:
    return hashlib.sha256(text.rstrip("\n").encode("utf-8")).hexdigest()


def publication_contract_identity(contract_root: Path = ROOT / "pr") -> dict[str, str]:
    contract = (contract_root / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
    section = contract.split("<!-- publication-intake-jq-start -->", 1)[1].split("<!-- publication-intake-jq-end -->", 1)[0]
    jq_filter = section.split("~~~jq", 1)[1].split("~~~", 1)[0].strip()
    return {
        "contract_path": "pr/references/publication-safety-contract.md",
        "normalization": "utf8_text_without_trailing_lf",
        "contract_sha256": normalized_text_sha256(contract),
        "filter_sha256": normalized_text_sha256(jq_filter),
    }


def valid_publication_intake_manifest() -> dict[str, object]:
    source = {"authority": "caller", "evidence": ["task-context:TSK-1@sha256:abc"]}
    return {
        "manifest_version": "1",
        "publication_intake_schema_version": "2",
        "publication_lineage_id": "9" * 64,
        "publication_manifest_generation": 1,
        "supersedes_manifest_sha256": None,
        "publication_pr_number": None,
        "publication_target": {
            "repository": "Saber5656/skills",
            "remote": "origin",
            "base_ref": "refs/heads/main",
            "head_ref": "refs/heads/codex/skills-pr-merge-safety",
            "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA,
        },
        "publication_intake_contract": publication_contract_identity(),
        "expected_assignees": ["Saber5656", "release-owner"],
        "expected_assignees_source": source,
        "required_checks": [
            {
                "name": "Analyze (python)",
                "mechanism": "check_run",
                "producer": {"app_id": 15368, "evidence": ["ruleset:protect-main"]},
                "current_head_required": True,
            }
        ],
        "required_check_inventory_source": {
            "repository": "Saber5656/skills",
            "base_ref": "refs/heads/main",
            "policy_sources": ["ruleset:protect-main"],
            "evidence": ["github-ruleset-snapshot:sha256:def"],
        },
        "external_reviewers": {
            "policy_source": source,
            "coderabbit": {
                "required": True,
                "command": "@coderabbitai review",
                "current_head_required": True,
                "attempt_policy": "at_most_once_per_pr_initial_review",
                "initial_review_key": "repository_pr_initial",
                "fallback_policy": "quota_only_existing_chatgpt",
                "alternate_reviewer": {
                    "provider": "chatgpt",
                    "route": "existing_configured_review",
                    "trigger_mode": "existing_review_route_only",
                    "quota_only": True,
                    "current_head_required": True,
                    "policy_source": source,
                },
                "policy_source": source,
            },
            "codex": {
                "required": True,
                "trigger_mode": "repository_automatic_only",
                "manual_trigger_forbidden": True,
                "current_head_required": True,
                "policy_source": source,
            },
        },
        "publication_mutations": {
            "ready_pr": {
                "creation_allowed": True,
                "title_digest": "sha256:" + "4" * 64,
                "body_digest": "sha256:" + "5" * 64,
            },
            "pr_labels": ["enhancement"],
            "issue_labels": {"issue_number": 41, "labels": ["enhancement"]},
            "requested_reviewers": [],
            "review_threads": [],
        },
    }


class PrPublicationSafetyTest(unittest.TestCase):
    def test_exact_assignee_postcondition_is_fail_closed(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "expected_assignees",
            "observed_assignees",
            "exact set",
            "publication_incomplete",
            "assignee_set_mismatch",
        ]:
            self.assertIn(phrase, text)

    def test_assignee_commands_reconcile_the_trusted_set(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "expected_assignees_json",
            "set_exact_assignees",
            "expected_logins:$expected",
            "assignees-set-exact",
            "github_observe:assignees",
            "fresh_assignees_result_json",
        ]:
            self.assertIn(phrase, text)
        self.assertNotIn("--add-assignee", text)
        self.assertNotIn("--remove-assignee", text)
        self.assertNotIn('--assignee "$me"', text)
        self.assertNotIn('--add-assignee "$me"', text)

    def test_assignee_examples_and_evals_do_not_restore_unconditional_current_user(self) -> None:
        skill = read_skill("pr")
        self.assertNotIn("- Assign current GitHub user.", skill)
        self.assertNotIn("- Apply assignee, remote-head verification", skill)
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))
        first = next(item for item in evals["evals"] if item["id"] == 1)
        self.assertIn("expected_assignees", first["expected_output"])
        self.assertNotIn("assigns the current user", first["expected_output"].lower())
        self.assertFalse(any("current GitHub user" in item for item in first["expectations"]))

    def test_expected_assignee_manifest_filter_fails_closed_before_mutation(self) -> None:
        skill = read_skill("pr")
        jq_filter = read_expected_assignees_filter()
        invalid_payloads = [
            "{",
            json.dumps({}),
            json.dumps({"expected_assignees": None}),
            json.dumps({"expected_assignees": "Saber5656"}),
            json.dumps({"expected_assignees": []}),
            json.dumps({"expected_assignees": [1]}),
            json.dumps({"expected_assignees": [""]}),
            json.dumps({"expected_assignees": ["-invalid"]}),
            json.dumps({"expected_assignees": ["invalid-"]}),
            json.dumps({"expected_assignees": ["invalid_name"]}),
            json.dumps({"expected_assignees": ["a--b"]}),
            json.dumps({"expected_assignees": ["a---b"]}),
            json.dumps({"expected_assignees": ["a" * 40]}),
            json.dumps({"expected_assignees": ["Saber5656\n"]}),
            json.dumps({"expected_assignees": ["Saber5656\r"]}),
            json.dumps({"expected_assignees": ["Saber5656\t"]}),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                result = subprocess.run(
                    ["jq", "-ce", jq_filter],
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode)
        valid = subprocess.run(
            ["jq", "-ce", jq_filter],
            input=json.dumps({"expected_assignees": ["release-owner", "a-b", "a" * 39, "Saber5656", "Saber5656"]}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        self.assertEqual(["Saber5656", "a-b", "a" * 39, "release-owner"], json.loads(valid.stdout))
        cli_block = read_pr_cli_block()
        gate_position = cli_block.index('if ! expected_assignees_json="$(printf')
        for mutation in [
            'publication_runtime_run "$transport_operation_tag" transport_remote_oid',
            'publication_runtime_run "$transport_operation_tag" transport_push_oid',
            "publication_runtime_run ready-pr-create create_ready_pr",
            "publication_runtime_run assignees-set-exact set_exact_assignees",
        ]:
            self.assertLess(gate_position, cli_block.index(mutation))
        self.assertIn("expected_assignees_invalid", skill)

    def test_canonical_preflight_is_the_exclusive_push_owner(self) -> None:
        skill = read_skill("pr")
        cli_block = read_pr_cli_block()
        self.assertNotIn("git push -u origin <branch>", skill)
        self.assertIn("canonical publication preflight exclusively owns every publication push", skill)
        self.assertIn("Do not run a standalone `git push`", skill)
        self.assertNotIn("git push", cli_block)
        self.assertNotIn("git ls-remote", cli_block)
        self.assertEqual(1, cli_block.count("publication_transport_push_oid()"))
        self.assertIn("publication_runtime_run \"$transport_operation_tag\" transport_push_oid", cli_block)
        self.assertIn(
            'publication_transport_push_oid "$expected_head_sha" "$head_ref"',
            cli_block,
        )
        self.assertIn('reconcile_operation "$reconciliation_parameters_json"', cli_block)

    def test_invalid_full_manifest_invokes_no_git_or_gh_command(self) -> None:
        cli_block = read_pr_cli_block()
        valid = valid_publication_intake_manifest()

        def changed(*path_and_value: object) -> dict[str, object]:
            payload = json.loads(json.dumps(valid))
            *path, value = path_and_value
            target = payload
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            return payload

        invalid_payloads = [
            changed("publication_intake_schema_version", "1"),
            changed("publication_manifest_generation", 2),
            changed("expected_assignees_source", "evidence", ["  "]),
            changed("required_checks", 0, "producer", {"creator_node_id": "node", "evidence": ["x"]}),
            changed("required_check_inventory_source", "repository", "different/repository"),
            changed("external_reviewers", "coderabbit", "policy_source", {}),
        ]
        invalid_serializations = [json.dumps(payload) for payload in invalid_payloads]
        invalid_serializations.append(json.dumps(valid) + "\n" + json.dumps(valid))
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            calls_path = temporary_root / "mutation-calls.log"
            git_path = bin_directory / "git"
            git_path.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALLS_FILE"\n'
                'case "$1" in\n'
                '  symbolic-ref) printf "%s\\n" "${STUB_BRANCH}"; exit 0 ;;\n'
                '  rev-parse) printf "%s\\n" "${STUB_LOCAL_HEAD}"; exit 0 ;;\n'
                '  remote) printf "%s\\n" "${STUB_REMOTE_URL}"; exit 0 ;;\n'
                '  config) exit 1 ;;\n'
                '  fetch) exit 97 ;;\n'
                '  *) exit 97 ;;\n'
                'esac\n',
                encoding="utf-8",
            )
            git_path.chmod(0o755)
            gh_path = bin_directory / "gh"
            gh_path.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALLS_FILE"\n'
                'emit_exact_pr() {\n'
                '  printf \'[{"number":41,"url":"https://github.com/Saber5656/skills/pull/41",'
                '"isDraft":false,"baseRefName":"main","headRefName":"%s","baseRefOid":"%s",'
                '"headRefOid":"%s","headRepositoryOwner":{"login":"Saber5656"}}]\\n\' '
                '"$STUB_BRANCH" "$STUB_FETCHED_BASE_SHA" "$STUB_LOCAL_HEAD"\n'
                '}\n'
                'if [ "$1" = "pr" ] && [ "$2" = "list" ]; then\n'
                '  if [ "${STUB_PR_MODE:-none}" = "existing" ] \\\n'
                '    || { [ -f "$PR_CREATED_FILE" ] \\\n'
                '      && { [ "${STUB_PR_MODE:-none}" = "create" ] \\\n'
                '        || [ "${STUB_PR_MODE:-none}" = "uncertain" ]; }; }; then\n'
                '    emit_exact_pr\n'
                '  else\n'
                '    printf "[]\\n"\n'
                '  fi\n'
                '  exit 0\n'
                'fi\n'
                'if [ "$1" = "pr" ] && [ "$2" = "create" ]; then\n'
                '  touch "$PR_CREATED_FILE"\n'
                '  [ "${STUB_PR_MODE:-none}" = "create" ] && exit 0\n'
                '  [ "${STUB_PR_MODE:-none}" = "uncertain" ] && exit 88\n'
                '  exit 97\n'
                'fi\n'
                'exit 97\n',
                encoding="utf-8",
            )
            gh_path.chmod(0o755)
            upstream_set_path = temporary_root / "upstream-set"
            pr_created_path = temporary_root / "pr-created"
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
            environment["CALLS_FILE"] = str(calls_path)
            environment["pr_skill_root"] = str(ROOT / "pr")
            environment["STUB_BRANCH"] = "codex/skills-pr-merge-safety"
            environment["STUB_LOCAL_HEAD"] = HEAD_SHA
            environment["STUB_REMOTE_URL"] = "git@github.com:Saber5656/skills.git"
            body_path = temporary_root / "pr-body.md"
            body_path.write_text("Closes #41\n", encoding="utf-8")
            script = (
                'publication_manifest="$1"\n'
                'expected_publication_manifest_sha256="$2"\n'
                'title="[issue #41] Harden PR publication safety"\n'
                f'body_file="{body_path}"\n'
                + cli_block
            )
            manifest_path = temporary_root / "publication-manifest.json"
            for payload in invalid_serializations:
                with self.subTest(payload=payload):
                    calls_path.unlink(missing_ok=True)
                    manifest_path.write_text(payload, encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script, "publication-preflight", str(manifest_path), normalized_text_sha256(payload)],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=environment,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("publication_incomplete: publication_intake_invalid", result.stderr)
                    self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

            calls_path.unlink(missing_ok=True)
            valid_serialization = json.dumps(valid)
            manifest_path.write_text(valid_serialization, encoding="utf-8")
            unavailable_environment = environment.copy()
            unavailable_environment["pr_skill_root"] = "relative/pr"
            unavailable = subprocess.run(
                ["bash", "-c", script, "publication-preflight", str(manifest_path), normalized_text_sha256(valid_serialization)],
                text=True,
                capture_output=True,
                check=False,
                env=unavailable_environment,
            )
            self.assertNotEqual(0, unavailable.returncode)
            self.assertIn("publication_incomplete: publication_intake_contract_unavailable", unavailable.stderr)
            self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

            calls_path.unlink(missing_ok=True)
            digest_mismatch = subprocess.run(
                ["bash", "-c", script, "publication-preflight", str(manifest_path), "0" * 64],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(0, digest_mismatch.returncode)
            self.assertIn("publication_incomplete: publication_intake_identity_mismatch", digest_mismatch.stderr)
            self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

            stale_root = temporary_root / "stale-pr"
            stale_reference = stale_root / "references"
            stale_reference.mkdir(parents=True)
            current_contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
            (stale_reference / "publication-safety-contract.md").write_text(
                current_contract + "\n<!-- producer-consumer-drift -->\n", encoding="utf-8"
            )
            stale_environment = environment.copy()
            stale_environment["pr_skill_root"] = str(stale_root)
            stale_contract = subprocess.run(
                ["bash", "-c", script, "publication-preflight", str(manifest_path), normalized_text_sha256(valid_serialization)],
                text=True,
                capture_output=True,
                check=False,
                env=stale_environment,
            )
            self.assertNotEqual(0, stale_contract.returncode)
            self.assertIn("publication_incomplete: publication_intake_identity_mismatch", stale_contract.stderr)
            self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

            calls_path.unlink(missing_ok=True)
            valid_attempt = subprocess.run(
                ["bash", "-c", script, "publication-preflight", str(manifest_path), normalized_text_sha256(valid_serialization)],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(0, valid_attempt.returncode)
            self.assertIn("publication_incomplete: publication_gateway_client_untrusted", valid_attempt.stderr)
            self.assertFalse(calls_path.exists())

        self.assertIn("set -euo pipefail", cli_block)
        full_gate_position = cli_block.index("jq -cse \"$publication_intake_filter\"")
        target_position = cli_block.index(".publication_target.repository")
        digest_position = cli_block.index("observed_publication_manifest_sha256")
        for mutation in [
            'publication_runtime_run "$transport_operation_tag" transport_remote_oid',
            'publication_runtime_run "$transport_operation_tag" transport_push_oid',
            "publication_runtime_run ready-pr-create create_ready_pr",
            "publication_runtime_run assignees-set-exact set_exact_assignees",
        ]:
            self.assertLess(full_gate_position, cli_block.index(mutation))
            self.assertLess(target_position, cli_block.index(mutation))
            self.assertLess(digest_position, cli_block.index(mutation))

    def test_extracted_filter_is_not_executed_before_contract_digest_verification(self) -> None:
        cli_block = read_pr_cli_block()
        filter_execution = cli_block.index('jq -cse "$publication_intake_filter"')
        filter_identity_gate = cli_block.index(
            '[ "$observed_filter_sha256" != "$expected_filter_sha256" ]'
        )
        self.assertLess(filter_identity_gate, filter_execution)

        valid = valid_publication_intake_manifest()
        serialization = json.dumps(valid)
        current_contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(
            encoding="utf-8"
        )
        current_filter = read_publication_intake_filter()
        malicious_filter = f'(env.SECURITY_SENTINEL | debug), (\n{current_filter}\n)'
        malicious_contract = current_contract.replace(
            f"~~~jq\n{current_filter}\n~~~",
            f"~~~jq\n{malicious_filter}\n~~~",
            1,
        )
        self.assertNotEqual(current_contract, malicious_contract)

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            malicious_root = temporary_root / "malicious-pr"
            references = malicious_root / "references"
            references.mkdir(parents=True)
            (references / "publication-safety-contract.md").write_text(
                malicious_contract,
                encoding="utf-8",
            )
            manifest_path = temporary_root / "publication-manifest.json"
            manifest_path.write_text(serialization, encoding="utf-8")
            body_path = temporary_root / "body.md"
            body_path.write_text("Closes #41\n", encoding="utf-8")
            calls_path = temporary_root / "calls.log"
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            for command in ("git", "gh"):
                command_path = bin_directory / command
                command_path.write_text(
                    '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALLS_FILE"\nexit 97\n',
                    encoding="utf-8",
                )
                command_path.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_directory}:{environment['PATH']}",
                    "CALLS_FILE": str(calls_path),
                    "pr_skill_root": str(malicious_root),
                    "SECURITY_SENTINEL": "filter-executed-before-identity-gate",
                }
            )
            script = (
                'publication_manifest="$1"\n'
                'expected_publication_manifest_sha256="$2"\n'
                'title="[issue #41] Harden PR publication safety"\n'
                f'body_file="{body_path}"\n'
                + cli_block
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    script,
                    "publication-preflight",
                    str(manifest_path),
                    normalized_text_sha256(serialization),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("publication_incomplete: publication_intake_identity_mismatch", result.stderr)
            self.assertNotIn("filter-executed-before-identity-gate", result.stderr)
            self.assertFalse(calls_path.exists())

    def test_assignee_argument_materialization_failure_stops_before_edit(self) -> None:
        cli_block = read_pr_cli_block()
        assignee_start = cli_block.index("assignee_parameters_json=")
        assignee_block = cli_block[assignee_start:]
        self.assertIn("expected_logins:$expected", assignee_block)
        self.assertIn(
            "publication_stop assignee_argument_materialization_failed",
            assignee_block,
        )
        self.assertIn(
            "publication_runtime_run assignees-set-exact set_exact_assignees",
            assignee_block,
        )
        self.assertNotIn("gh pr edit", assignee_block)
        self.assertNotIn("--add-assignee", assignee_block)
        self.assertNotIn("--remove-assignee", assignee_block)

    def test_pr_identity_move_stops_before_assignee_edit(self) -> None:
        cli_block = read_pr_cli_block()
        before = cli_block.index(
            "assert_publication_pr_identity pr-identity-before-assignees"
        )
        mutation = cli_block.index(
            "publication_runtime_run assignees-set-exact set_exact_assignees"
        )
        after = cli_block.index(
            "assert_publication_pr_identity pr-identity-after-assignees"
        )
        fresh = cli_block.index(
            "publication_runtime_run assignees-observe-post github_observe"
        )
        self.assertLess(before, mutation)
        self.assertLess(mutation, after)
        self.assertLess(after, fresh)
        self.assertIn("same-lock identity recheck", read_pr_contract())
        self.assertNotIn("gh pr edit", cli_block)

    def test_active_lineage_and_conditional_gateway_are_pre_mutation_gates(self) -> None:
        cli_block = read_pr_cli_block()
        lineage_gate = cli_block.index(
            "assert_active_publication_lineage lineage-read-before-remote"
        )
        transport = cli_block.index(
            'publication_transport_remote_oid "$base_ref" remote-base-initial'
        )
        create = cli_block.index(
            "publication_runtime_run ready-pr-create create_ready_pr"
        )
        self.assertLess(lineage_gate, transport)
        self.assertLess(lineage_gate, create)
        self.assertLess(
            cli_block.index(
                "publication_runtime_run lineage-activate lineage_activate"
            ),
            lineage_gate,
        )
        self.assertNotIn("gh pr create", cli_block)
        self.assertNotIn("gh pr edit", cli_block)
        self.assertNotIn("publication_github_mutate_if_identity", cli_block)
        self.assertIn(
            ".manifest_digest == $manifest",
            cli_block,
        )
        self.assertIn(
            ".manifest_generation == $generation",
            cli_block,
        )
        self.assertIn("publication_gateway_health_unavailable", cli_block)

    def test_bound_pr_number_is_adopted_when_the_same_frozen_generation_restarts(self) -> None:
        cli_block = read_pr_cli_block()
        self.assertIn(
            "then ($observed.publication_pr_number == null",
            cli_block,
        )
        self.assertIn(
            "expected_pr_number=\"$observed_active_pr_number\"",
            cli_block,
        )
        self.assertIn(
            "publication_runtime_run lineage-bind-pr lineage_bind_pr",
            cli_block,
        )
        self.assertIn(
            "assert_active_publication_lineage lineage-read-after-bind",
            cli_block,
        )
        self.assertIn(
            "frozen generation starts with a null PR",
            read_pr_contract(),
        )

    def test_create_claim_reclaims_only_expired_reserved_before_creating(self) -> None:
        contract = read_pr_contract()
        cli_block = read_pr_cli_block()
        for phrase in [
            "claim_reserve",
            "caller never supplies or calculates a key",
            "All fallible remote and PR observations occur before reservation",
            "`delivery_unknown` is reconciliation-only",
            "CodeRabbit and review-reply claims are never reclaimable",
        ]:
            self.assertIn(phrase, contract)
        final_remote_precheck = cli_block.index(
            'latest_head_sha="$(publication_transport_remote_oid "$head_ref" remote-head-pre-create)"'
        )
        final_pr_precheck = cli_block.index(
            "load_publication_pr_resolution pr-observe-pre-create",
            final_remote_precheck,
        )
        reserve = cli_block.index(
            "publication_runtime_run ready-claim-reserve claim_reserve",
            final_pr_precheck,
        )
        create = cli_block.index(
            "publication_runtime_run ready-pr-create create_ready_pr",
            reserve,
        )
        self.assertLess(final_remote_precheck, final_pr_precheck)
        self.assertLess(final_pr_precheck, reserve)
        self.assertLess(reserve, create)
        between_reserve_and_create = cli_block[reserve:create]
        self.assertNotIn("publication_transport_remote_oid", between_reserve_and_create)
        self.assertNotIn("github_observe", between_reserve_and_create)
        self.assertIn("claim_key:$claim_key", between_reserve_and_create)
        self.assertIn("claim_token:$claim_token", between_reserve_and_create)
        self.assertIn(
            "publication_runtime_run ready-pr-create-reconcile reconcile_operation",
            cli_block,
        )
        self.assertNotIn("ready-pr-v1:", cli_block)

    def test_all_github_write_operations_require_the_conditional_gateway(self) -> None:
        contract = read_pr_contract()
        operations = [
            "create_ready_pr",
            "set_exact_assignees",
            "set_pr_labels",
            "set_issue_labels",
            "set_reviewers",
            "create_coderabbit_comment",
            "reply_review_thread",
            "resolve_review_thread",
            "append_publication_outcome",
        ]
        for operation in operations:
            self.assertIn(operation, contract)
        self.assertIn("same-lock identity recheck", contract)
        self.assertIn("fall back to plain", contract)
        self.assertNotIn("publication_github_mutate_if_identity", read_pr_cli_block())

        expected = {"head_sha": HEAD_SHA, "manifest": "a" * 64}
        writes: list[str] = []

        def mutate_if_identity(operation: str, current: dict[str, str]) -> bool:
            if current != expected:
                return False
            writes.append(operation)
            return True

        for operation in operations:
            with self.subTest(operation=operation):
                writes.clear()
                moved = {"head_sha": "3" * 40, "manifest": expected["manifest"]}
                self.assertFalse(mutate_if_identity(operation, moved))
                self.assertEqual([], writes)
    def test_configuration_isolated_transport_ignores_global_and_injected_rewrites(self) -> None:
        transport_block = read_publication_transport_block()
        for phrase in [
            'publication_runtime_run "$transport_operation_tag" transport_remote_oid',
            'publication_runtime_run "$transport_operation_tag" transport_push_oid',
            "reconcile_operation",
            "expected_remote_oid",
        ]:
            self.assertIn(phrase, transport_block)
        for forbidden in [
            "publication_isolated_git",
            "git ls-remote",
            "git push",
            "GIT_CONFIG_COUNT",
            "GIT_SSH_COMMAND",
            "transport_url",
        ]:
            self.assertNotIn(forbidden, transport_block)

        cli_block = read_pr_cli_block()
        for phrase in [
            "env -i PATH=/usr/bin:/bin HOME=/var/empty",
            "publication_gateway_client.py",
            "publication_gateway_client_untrusted",
            "publication_gateway_contract_untrusted",
        ]:
            self.assertIn(phrase, cli_block)
    def test_invalid_assignee_manifest_invokes_no_git_or_gh_command(self) -> None:
        cli_block = read_pr_cli_block()
        invalid_payloads = [
            "{",
            json.dumps({}),
            json.dumps({"expected_assignees": None}),
            json.dumps({"expected_assignees": "Saber5656"}),
            json.dumps({"expected_assignees": []}),
            json.dumps({"expected_assignees": [1]}),
            json.dumps({"expected_assignees": [""]}),
            json.dumps({"expected_assignees": ["-invalid"]}),
            json.dumps({"expected_assignees": ["invalid-"]}),
            json.dumps({"expected_assignees": ["invalid_name"]}),
            json.dumps({"expected_assignees": ["a--b"]}),
            json.dumps({"expected_assignees": ["a---b"]}),
            json.dumps({"expected_assignees": ["a" * 40]}),
            json.dumps({"expected_assignees": ["Saber5656\n"]}),
            json.dumps({"expected_assignees": ["Saber5656\r"]}),
            json.dumps({"expected_assignees": ["Saber5656\t"]}),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            calls_path = temporary_root / "mutation-calls.log"
            stub = '#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALLS_FILE"\nexit 97\n'
            for command in ("git", "gh"):
                command_path = bin_directory / command
                command_path.write_text(stub, encoding="utf-8")
                command_path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
            environment["CALLS_FILE"] = str(calls_path)
            script = 'publication_manifest="$1"\n' + cli_block
            manifest_path = temporary_root / "publication-manifest.json"
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    calls_path.unlink(missing_ok=True)
                    manifest_path.write_text(payload, encoding="utf-8")
                    result = subprocess.run(
                        ["bash", "-c", script, "assignee-preflight", str(manifest_path)],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=environment,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("publication_incomplete: expected_assignees_invalid", result.stderr)
                    self.assertFalse(calls_path.exists(), calls_path.read_text(encoding="utf-8") if calls_path.exists() else "")

    def test_explicit_current_user_default_materializes_authenticated_login(self) -> None:
        block = read_current_user_default_block()
        contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
        self.assertNotIn('expected_assignees=["$me"]', contract)
        self.assertIn("Never persist a symbolic `$me`", contract)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bin_directory = temporary_root / "bin"
            bin_directory.mkdir()
            calls_path = temporary_root / "gh-calls.log"
            gh_path = bin_directory / "gh"
            gh_path.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CALLS_FILE"\n'
                'if [ "${STUB_GH_FAIL:-0}" = "1" ]; then exit 41; fi\n'
                'if [ "$#" -eq 4 ] && [ "$1" = "api" ] && [ "$2" = "user" ] && [ "$3" = "--jq" ] && [ "$4" = ".login" ]; then\n'
                '  printf "%s\\n" "Saber5656"\n  exit 0\nfi\nexit 97\n',
                encoding="utf-8",
            )
            gh_path.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_directory}:{environment['PATH']}"
            environment["CALLS_FILE"] = str(calls_path)
            script = 'set -euo pipefail\n' + block + '\nprintf "RESULT=%s\\n" "$current_user_expected_assignees_json"\n'
            result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, env=environment)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["api user --jq .login"], calls_path.read_text(encoding="utf-8").splitlines())
            concrete = json.loads(next(line.removeprefix("RESULT=") for line in result.stdout.splitlines() if line.startswith("RESULT=")))
            self.assertEqual(["Saber5656"], concrete)
            validated = subprocess.run(
                ["jq", "-ce", read_expected_assignees_filter()],
                input=json.dumps({"expected_assignees": concrete}),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, validated.returncode, validated.stderr)
            calls_path.unlink()
            failing_environment = environment.copy()
            failing_environment["STUB_GH_FAIL"] = "1"
            failed = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, env=failing_environment)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("publication_incomplete: current_user_identity_unreadable", failed.stderr)

    def test_assignee_reconciliation_rereads_and_compares_exact_set(self) -> None:
        contract = (ROOT / "pr" / "references" / "publication-safety-contract.md").read_text(encoding="utf-8")
        for phrase in [
            "assignee_state_unreadable",
            "assignee_edit_failed",
            "fresh_assignees_result_json",
            ".observed_identity.assignees == $expected",
            "assignee_set_mismatch",
            "github_observe:assignees",
        ]:
            self.assertIn(phrase, contract)

    def test_coderabbit_trigger_is_configured_current_head_and_typed(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "external_reviewers.coderabbit.required",
            "@coderabbitai review",
            "at-most-once",
            "expected_head_sha",
            "coderabbit_policy_missing",
            "coderabbit_permission_blocked",
            "coderabbit_rate_limited",
            "coderabbit_delivery_failed",
            "coderabbit_claim_unavailable",
            "claim_reserve",
            "never supplies or calculates a key",
            "delivery_unknown",
            "reviewer_policy_version",
            "create_coderabbit_comment",
            "identical public comment cannot reconcile",
            "Never substitute whichever head is newly observed",
        ]:
            self.assertIn(phrase, text)
        claim_rows = [line for line in text.splitlines() if line.startswith("| `") and " | " in line]
        mutation_column = {row.split("|")[1].strip(): row.split("|")[2].strip() for row in claim_rows}
        self.assertEqual("no", mutation_column["`unclaimed`"])
        self.assertIn("no from persisted state", mutation_column["`reserved`"])
        self.assertIn("no from persisted or reloaded state", mutation_column["`posting` / `delivery_unknown`"])
        self.assertEqual("no", mutation_column["`delivered` / `acknowledged` / `rate_limited` / terminal"])
        skill = read_skill("pr")
        for mutation in ["set_pr_labels", "set_issue_labels"]:
            self.assertIn(mutation, skill)
        self.assertIn("Saihai publication runtime", skill)

    def test_coderabbit_posting_crash_is_reconciliation_only(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "persisted `posting`",
            "never authorizes another reserve or comment attempt",
            "identical public comment cannot reconcile",
            "CodeRabbit and review-reply claims are never reclaimable",
            "never issue another comment mutation",
            "coderabbit_trigger_state_unknown",
        ]:
            self.assertIn(phrase, text)
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))
        crash = next(item for item in evals["evals"] if item["id"] == 32)
        self.assertIn("pre-existing posting state as reconciliation-only", crash["expected_output"])
        self.assertIn("no second comment mutation", crash["expected_output"])

    def test_coderabbit_is_initial_only_and_quota_fallback_is_narrow(self) -> None:
        text = read_pr_contract()
        normalized = " ".join(text.split())
        for phrase in [
            "at_most_once_per_pr_initial_review",
            "initial_review_key",
            "one logical initial CodeRabbit intake per PR",
            "repair push does not retrigger",
            "quota-only",
            "authenticated quota receipt",
            "existing ChatGPT review route",
            "alternate_review_result_missing",
            "alternate_review_result_invalid",
            "No second CodeRabbit trigger",
            "HTTP 429",
        ]:
            self.assertIn(phrase, normalized)
        self.assertNotIn("attempt_policy == \"at_most_once_per_head\"", text)
        manifest = valid_publication_intake_manifest()
        self.assertEqual(
            "at_most_once_per_pr_initial_review",
            manifest["external_reviewers"]["coderabbit"]["attempt_policy"],  # type: ignore[index]
        )
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))["evals"]
        for eval_id, marker in {
            50: "Only an authenticated CodeRabbit quota receipt",
            51: "Does not select the alternate reviewer",
            52: "Does not request a second ChatGPT review",
        }.items():
            case = next(item for item in evals if item["id"] == eval_id)
            self.assertIn(marker, case["expectations"])

    def test_related_repo_disables_automatic_coderabbit_reviews(self) -> None:
        config = (ROOT / ".coderabbit.yaml").read_text(encoding="utf-8")
        self.assertIn("auto_review:", config)
        self.assertIn("enabled: false", config)
        self.assertIn("auto_incremental_review: false", config)

    def test_pr_conflict_repair_is_causal_bounded_and_does_not_authorize_merge(self) -> None:
        pr_text = read_skill("pr")
        contract = read_pr_contract()
        for phrase in [
            "causal merge receipt",
            "merged PR number and merge SHA",
            "same task owner",
            "same authorized branch/worktree",
            "unrelated dirty",
            "history-preserving",
            "no force push",
            "conflict_repair",
            "base/head change invalidates",
            "fresh focused and full validation",
            "conflict repair does not authorize merge",
        ]:
            self.assertIn(phrase, f"{pr_text}\n{contract}")
        merge_text = read_skill("merge")
        self.assertIn("must not push, force, reset hard,", merge_text)
        self.assertIn("or auto-resolve conflicts; on conflict it aborts and reports", merge_text)
        evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))["evals"]
        for eval_id, marker in {
            53: "Allows repair only with a fresh authenticated causal merge receipt",
            54: "Stops on stale causal evidence or unrelated dirty state",
            55: "Invalidates old validation and review evidence after the base/head change",
            56: "Does not reset the shared repair budget or force-push",
        }.items():
            case = next(item for item in evals if item["id"] == eval_id)
            self.assertIn(marker, case["expectations"])

    def test_required_check_producer_and_untrusted_payload_are_bound(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "trusted producer",
            "app_id",
            "`required_workflow`",
            "repository_id/path/ref/sha",
            "required_check_inventory_unknown",
            "incomplete pagination",
            "required_check_producer_mismatch",
            "untrusted data",
            "Never execute",
            "authenticated structured GitHub metadata",
        ]:
            self.assertIn(phrase, text)

    def test_review_absence_and_provenance_states_are_distinct(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "review_count_zero",
            "review_threads_absent",
            "unresolved_thread_count_zero",
            "review_timeout",
            "review_provenance_missing",
            "effective_model",
            "reviewer_role",
        ]:
            self.assertIn(phrase, text)
        self.assertIn("never post a manual Codex review-trigger command", text)

    def test_required_checks_do_not_use_mergeable_as_readiness(self) -> None:
        text = read_pr_contract()
        for phrase in [
            "required_check_inventory_unknown",
            "checks_pending",
            "checks_failed",
            "mergeStateStatus",
            "policy merge readiness",
        ]:
            self.assertIn(phrase, text)

    def test_ready_pr_transport_has_one_owner_and_push_is_negative_boundary(self) -> None:
        push_skill = read_skill("push")
        remaining_skill = read_skill("gh-deliver-remaining-issues")
        pr_skill = read_skill("pr")
        for phrase in [
            "PR Publication Negative Boundary",
            "pr_publication_transport_owned_by_pr",
            "pr_canonical_publication_preflight",
            "全Git network operationを実行しない",
        ]:
            self.assertIn(phrase, push_skill)
        self.assertIn("Do not hand a ready-PR publication to `push`", remaining_skill)
        self.assertIn("canonical preflight exclusively owns", remaining_skill)
        self.assertIn("Use only for an explicitly non-PR push", pr_skill)


class ReviewFixSafetyTest(unittest.TestCase):
    def test_current_head_and_provenance_are_required(self) -> None:
        text = read_skill("pr-review-fix-policy")
        for phrase in [
            "current_head_sha",
            "review_head_sha",
            "old_head_review_invalid",
            "review_provenance_missing",
            "effective_model",
            "reviewer_role",
        ]:
            self.assertIn(phrase, text)

    def test_timeout_and_absence_are_not_collapsed(self) -> None:
        text = read_skill("pr-review-fix-policy")
        for phrase in [
            "review_count_zero",
            "review_threads_absent",
            "unresolved_thread_count_zero",
            "review_timeout",
            "not a pass",
        ]:
            self.assertIn(phrase, text)

    def test_quota_fallback_and_conflict_repair_keep_review_policy_boundaries(self) -> None:
        text = read_skill("pr-review-fix-policy")
        for phrase in [
            "quota-only CodeRabbit",
            "authenticated quota receipt",
            "existing ChatGPT review route",
            "do not request a second ChatGPT review",
            "timeout, permission error, generic API error, silence, or skip",
            "causal merge receipt",
            "same authorized branch/worktree",
            "unrelated dirty state",
            "base/head change invalidates",
            "fresh focused and full validation",
            "does not authorize merge",
        ]:
            self.assertIn(phrase, text)
        evals = json.loads(
            (ROOT / "pr-review-fix-policy" / "evals" / "evals.json").read_text(encoding="utf-8")
        )["evals"]
        for eval_id, marker in {
            33: "Accepts fallback only for an authenticated quota-only receipt",
            34: "Keeps timeout, generic error, and silence as blockers",
            35: "Reuses an existing valid ChatGPT result without duplication",
            36: "Allows only same-task causal conflict repair",
            37: "Stops on stale or unrelated conflict evidence",
            38: "Requires fresh identity-bound validation after repair",
        }.items():
            case = next(item for item in evals if item["id"] == eval_id)
            self.assertIn(marker, case["expectations"])


class RemainingIssuesSafetyTest(unittest.TestCase):
    def test_required_frontmatter_is_present(self) -> None:
        text = read_skill("gh-deliver-remaining-issues")
        for field in [
            "user-invocable:",
            "allowed-tools:",
            "category:",
            "created:",
            "status:",
            "purpose:",
            "argument-hint:",
        ]:
            self.assertIn(field, text)
        allowed_line = next(line for line in text.splitlines() if line.startswith("allowed-tools:"))
        observed_tools = {item.strip() for item in allowed_line.split(":", 1)[1].split(",")}
        self.assertEqual({"Read", "Grep", "Glob", "Bash", "Agent"}, observed_tools)

    def test_publication_manifest_intake_is_versioned_and_executable(self) -> None:
        valid = valid_publication_intake_manifest()
        jq_filter = read_publication_intake_filter()

        accepted = subprocess.run(
            ["jq", "-cse", jq_filter],
            input=json.dumps(valid),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)

        def changed(*path_and_value: object) -> dict[str, object]:
            payload = json.loads(json.dumps(valid))
            *path, value = path_and_value
            target = payload
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            return payload

        def producer_case(mechanism: str, producer: dict[str, object]) -> dict[str, object]:
            payload = json.loads(json.dumps(valid))
            payload["required_checks"][0]["mechanism"] = mechanism  # type: ignore[index]
            payload["required_checks"][0]["producer"] = producer  # type: ignore[index]
            return payload

        invalid_payloads = [
            {},
            changed("publication_intake_schema_version", "1"),
            changed("publication_lineage_id", "9" * 63),
            changed("publication_lineage_id", "G" * 64),
            changed("publication_manifest_generation", 0),
            changed("publication_manifest_generation", 1.5),
            changed("supersedes_manifest_sha256", "a" * 64),
            changed("publication_intake_contract", "filter_sha256", "g" * 64),
            changed("publication_target", "repository", " "),
            changed("publication_target", "repository", "Saber5656/skills\n"),
            changed("publication_target", "remote", "-origin"),
            changed("publication_target", "base_ref", "main"),
            changed("publication_target", "base_ref", "refs/heads/main\n"),
            changed("publication_target", "head_ref", "refs/heads/topic..escape"),
            changed("publication_target", "head_ref", "refs/heads/topic\r"),
            changed("publication_target", "head_ref", "refs/heads/main"),
            changed("publication_target", "base_sha", "1" * 39),
            changed("publication_target", "base_sha", "A" * 40),
            changed("publication_target", "head_sha", "2" * 41),
            changed("publication_target", "head_sha", "2" * 39 + "\n"),
            changed("expected_assignees", []),
            changed("expected_assignees", ["Saber5656\n"]),
            changed("expected_assignees", ["Saber5656\r"]),
            changed("expected_assignees", ["Saber5656\t"]),
            changed("expected_assignees", ["release-owner", "Saber5656"]),
            changed("expected_assignees_source", "evidence", []),
            changed("expected_assignees_source", "evidence", ["  "]),
            changed("required_checks", 0, "producer", {}),
            changed("required_checks", 0, "producer", {"creator_node_id": "node", "evidence": ["x"]}),
            changed("required_checks", 0, "producer", {"app_id": -1, "evidence": ["x"]}),
            changed("required_checks", 0, "producer", {"app_id": 1.5, "evidence": ["x"]}),
            producer_case("required_workflow", {"app_id": 15368, "evidence": ["x"]}),
            producer_case("commit_status", {"workflow_id": 311277167, "evidence": ["x"]}),
            producer_case("check_run", {"creator_node_id": "node", "evidence": ["x"]}),
            producer_case("required_workflow", {"workflow_id": 311277167, "workflow_ref": " ", "evidence": ["x"]}),
            producer_case(
                "required_workflow",
                {
                    "workflow_id": 311277167,
                    "workflow_ref": ".github/workflows/codeql.yml@971265c1750d289374ed11bade7cc696c730bfbb\n",
                    "evidence": ["x"],
                },
            ),
            producer_case("commit_status", {"creator_node_id": "  ", "evidence": ["x"]}),
            producer_case("commit_status", {"creator_node_id": "node\t", "evidence": ["x"]}),
            producer_case("commit_status", {"app_id": 15368, "creator_node_id": "node", "evidence": ["x"]}),
            producer_case("commit_status", {"app_id": -1, "creator_node_id": "node", "evidence": ["x"]}),
            producer_case("commit_status", {"app_id": 1.5, "creator_node_id": "node", "evidence": ["x"]}),
            producer_case("commit_status", {"app_id": 15368, "creator_node_id": "  ", "evidence": ["x"]}),
            changed("required_checks", 0, "producer", {"app_id": 15368, "evidence": ["  "]}),
            changed("required_check_inventory_source", "repository", "different/repo"),
            changed("required_check_inventory_source", "base_ref", "refs/heads/different"),
            changed("required_check_inventory_source", "evidence", []),
            changed("external_reviewers", "coderabbit", "policy_source", {}),
            changed("external_reviewers", "codex", "manual_trigger_forbidden", False),
            changed("publication_mutations", None),
            changed("publication_mutations", "ready_pr", "title_digest", "4" * 64),
            changed("publication_mutations", "ready_pr", "creation_allowed", "true"),
            changed("publication_mutations", "pr_labels", ["z-label", "a-label"]),
            changed("publication_mutations", "pr_labels", ["bad\nlabel"]),
            changed("publication_mutations", "requested_reviewers", ["reviewer", "reviewer"]),
            changed(
                "publication_mutations",
                "review_threads",
                [
                    {
                        "thread_id": "thread-1",
                        "reply_body_digest": None,
                        "resolve_allowed": False,
                        "approval_evidence": ["task-context:TSK-1"],
                    }
                ],
            ),
            changed(
                "publication_mutations",
                "review_threads",
                [
                    {
                        "thread_id": "thread-1",
                        "reply_body_digest": "sha256:" + "6" * 64,
                        "resolve_allowed": False,
                        "approval_evidence": ["task-context:TSK-1"],
                    },
                    {
                        "thread_id": "thread-1",
                        "reply_body_digest": None,
                        "resolve_allowed": True,
                        "approval_evidence": ["task-context:TSK-1"],
                    },
                ],
            ),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                rejected = subprocess.run(
                    ["jq", "-cse", jq_filter],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(0, rejected.returncode)

        workflow = json.loads(json.dumps(valid))
        workflow["required_checks"][0]["mechanism"] = "required_workflow"  # type: ignore[index]
        workflow["required_checks"][0]["producer"] = {  # type: ignore[index]
            "workflow_id": 311277167,
            "workflow_ref": ".github/workflows/codeql.yml@971265c1750d289374ed11bade7cc696c730bfbb",
            "evidence": ["required-workflow:311277167"],
        }
        rejected_workflow = subprocess.run(
            ["jq", "-cse", jq_filter], input=json.dumps(workflow), text=True, capture_output=True, check=False
        )
        self.assertNotEqual(0, rejected_workflow.returncode)

        status = json.loads(json.dumps(valid))
        status["required_checks"][0]["mechanism"] = "commit_status"  # type: ignore[index]
        status["required_checks"][0]["producer"] = {  # type: ignore[index]
            "creator_node_id": "MDQ6VXNlcjE=",
            "evidence": ["authenticated-status-creator"],
        }
        accepted_status = subprocess.run(
            ["jq", "-cse", jq_filter], input=json.dumps(status), text=True, capture_output=True, check=False
        )
        self.assertEqual(0, accepted_status.returncode, accepted_status.stderr)

        app_status = json.loads(json.dumps(valid))
        app_status["required_checks"][0]["mechanism"] = "commit_status"  # type: ignore[index]
        app_status["required_checks"][0]["producer"] = {  # type: ignore[index]
            "app_id": 15368,
            "evidence": ["authenticated-status-app"],
        }
        accepted_app_status = subprocess.run(
            ["jq", "-cse", jq_filter], input=json.dumps(app_status), text=True, capture_output=True, check=False
        )
        self.assertEqual(0, accepted_app_status.returncode, accepted_app_status.stderr)

        successor = json.loads(json.dumps(valid))
        successor["publication_manifest_generation"] = 2
        successor["supersedes_manifest_sha256"] = "a" * 64
        successor["publication_pr_number"] = 41
        accepted_successor = subprocess.run(
            ["jq", "-cse", jq_filter], input=json.dumps(successor), text=True, capture_output=True, check=False
        )
        self.assertEqual(0, accepted_successor.returncode, accepted_successor.stderr)

        pre_pr_successor = json.loads(json.dumps(successor))
        pre_pr_successor["publication_pr_number"] = None
        accepted_pre_pr_successor = subprocess.run(
            ["jq", "-cse", jq_filter],
            input=json.dumps(pre_pr_successor),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, accepted_pre_pr_successor.returncode, accepted_pre_pr_successor.stderr)

        duplicate_input = subprocess.run(
            ["jq", "-cse", jq_filter],
            input=json.dumps(valid) + "\n" + json.dumps(valid),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, duplicate_input.returncode)

        all_contracts = read_remaining_contract() + "\n" + read_pr_contract()
        self.assertEqual(1, all_contracts.count("<!-- publication-intake-jq-start -->"))
        self.assertEqual(1, all_contracts.count("<!-- publication-intake-jq-end -->"))

        for phrase in [
            "publication_intake_schema_version",
            "expected_assignees_source",
            "required_check_inventory_source",
            "publication_intake_contract",
            "expected_publication_manifest_sha256",
            "publication_intake_identity_mismatch",
            "publication_intake_invalid",
            "zero publication mutation",
            "unchanged",
            "versioned issue manifest",
        ]:
            self.assertIn(phrase, all_contracts)

    def test_consumer_and_test_extract_identical_filter_bytes_without_fences(self) -> None:
        contract_path = ROOT / "pr" / "references" / "publication-safety-contract.md"
        extracted = subprocess.run(
            [
                "awk",
                "-v",
                "start_marker=<!-- publication-intake-jq-start -->",
                "-v",
                "end_marker=<!-- publication-intake-jq-end -->",
                "$0 == start_marker { capture=1; next } "
                "$0 == end_marker { capture=0 } "
                "capture && $0 !~ /^~~~(jq)?$/ { print }",
                str(contract_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, extracted.returncode, extracted.stderr)
        consumer_bytes = extracted.stdout.rstrip("\n")
        self.assertEqual(read_publication_intake_filter(), consumer_bytes)
        self.assertNotIn("~~~", consumer_bytes)
        self.assertEqual(
            normalized_text_sha256(read_publication_intake_filter()),
            hashlib.sha256(consumer_bytes.encode("utf-8")).hexdigest(),
        )

    def test_finalization_separates_prepublication_checks_from_pr_only_outcomes(self) -> None:
        text = read_remaining_contract()
        for phrase in [
            "prepublication_checks",
            "all_prepublication_checks_passed",
            "postpublication_gate_inventory_frozen",
            "publication_outcome_record",
            "required_checks_current_head",
            "configured_reviews_current_head",
            "exact_assignees",
            "A PR-only check or external review that cannot exist before PR creation is not a finalization prerequisite",
        ]:
            self.assertIn(phrase, text)
        finalization_definition = text.split("`finalization.status: finalized` means", 1)[1].split(
            "Before setting `finalization.status: finalized`", 1
        )[0]
        self.assertIn("immutable publication intake", finalization_definition)
        self.assertIn("post-publication", finalization_definition)

    def test_manifest_supersession_and_outcome_delta_are_versioned(self) -> None:
        contract = read_remaining_contract()
        pr_contract = read_pr_contract()
        normalized_contract = " ".join(contract.split())
        normalized_pr_contract = " ".join(pr_contract.split())
        for phrase in [
            "publication_intake_schema_version: \"2\"",
            "publication_lineage_id",
            "publication_manifest_generation",
            "supersedes_manifest_sha256",
            "Exactly one Manifest",
            "active for that ID",
            "M1 -> M2",
            "prevents every M1 outcome",
            "from satisfying completion",
            "publication_outcome_delta",
            "delta_id",
            "event_id",
            "event_index",
            "append_sequence",
            "idempotent no-op",
            "publication_outcome_contradiction",
            "exact promotion predicate",
        ]:
            self.assertIn(phrase, normalized_contract)
        for phrase in [
            "publication_manifest_supersession_required",
            "expected_pr_number",
            "Publication outcome delta",
            "It alone verifies the active lineage",
            "active Manifest digest and generation",
        ]:
            self.assertIn(phrase, normalized_pr_contract)

        pr_evals = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))
        supersession = next(item for item in pr_evals["evals"] if item["id"] == 45)
        self.assertIn("only active generation", supersession["expected_output"])
        remaining_evals = json.loads(
            (ROOT / "gh-deliver-remaining-issues" / "evals" / "evals.json").read_text(encoding="utf-8")
        )
        contradiction = next(item for item in remaining_evals["evals"] if item["id"] == 33)
        self.assertIn("blocks contradictory", contradiction["expected_output"])

    def test_rfc8785_reference_and_review_carrier_cover_complete_provenance(self) -> None:
        text = read_remaining_contract()
        for phrase in [
            "Canonical review evidence carrier",
            "effective_model",
            "request_id",
            "session_id",
            "reviewed_head_sha",
            "artifact_digest",
            "result_integrity",
            "review_provenance_incomplete",
            "RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8",
            "Public-safe review summary projection",
            "duplicate decoded member names",
        ]:
            self.assertIn(phrase, text)

        vector = run_publication_integrity("canonicalize", '{"z":-0,"n":1e-7,"a":"x"}')
        self.assertEqual(0, vector.returncode, vector.stderr)
        self.assertEqual('{"a":"x","n":1e-7,"z":0}', vector.stdout)
        self.assertNotEqual(
            vector.stdout,
            json.dumps({"z": -0.0, "n": 1e-7, "a": "x"}, sort_keys=True, separators=(",", ":")),
        )
        duplicate = run_publication_integrity("canonicalize", '{"a":1,"\\u0061":2}')
        self.assertNotEqual(0, duplicate.returncode)
        self.assertIn("json_duplicate_key", duplicate.stderr)
        lone_surrogate = run_publication_integrity("canonicalize", '{"x":"\\ud800"}')
        self.assertNotEqual(0, lone_surrogate.returncode)
        self.assertIn("jcs_lone_surrogate", lone_surrogate.stderr)
        prototype_key = run_publication_integrity(
            "canonicalize", '{"__proto__":{"polluted":true},"a":1}'
        )
        self.assertEqual(0, prototype_key.returncode, prototype_key.stderr)
        self.assertEqual('{"__proto__":{"polluted":true},"a":1}', prototype_key.stdout)

        carrier = {
            "review_evidence_version": "1",
            "review_id": "review-1",
            "request_id": "request-1",
            "session_id": "session-1",
            "reviewer_role": "tech-security",
            "provider": "openai",
            "effective_model": "gpt-5.6-sol",
            "dispatch_facade": "saihai@abc123",
            "review_target": {
                "repository": "Saber5656/skills",
                "base_sha": BASE_SHA,
                "reviewed_head_sha": HEAD_SHA,
                "target_kind": "intended_tree",
                "target_identity": "sha256:" + "3" * 64,
                "snapshot_digest": "sha256:" + "4" * 64,
                "artifact_digest": "sha256:" + "5" * 64,
            },
            "terminal_status": "success",
            "terminal_result": {
                "schema_version": "1",
                "status": "success",
                "verdict": "approved",
                "findings": [],
            },
            "result_integrity": {
                "algorithm": "sha256",
                "payload": "review_evidence_without_result_integrity.digest",
                "canonicalization": "RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8",
                "digest": "0" * 64,
            },
        }
        digest_result = run_publication_integrity("carrier-digest", carrier)
        self.assertEqual(0, digest_result.returncode, digest_result.stderr)
        expected_digest = digest_result.stdout
        self.assertEqual("9cf4a33725c26429b90d5f1b2b71ad5fa5346e400ea0cf8735930ad473659810", expected_digest)
        carrier["result_integrity"]["digest"] = expected_digest
        verified = run_publication_integrity("verify-carrier", carrier)
        self.assertEqual(0, verified.returncode, verified.stderr)
        self.assertEqual(expected_digest, json.loads(verified.stdout)["digest"])

        mutations = [
            (("review_id",), "review-2"),
            (("request_id",), "request-2"),
            (("session_id",), "session-2"),
            (("reviewer_role",), "tech-qa"),
            (("provider",), "different-provider"),
            (("effective_model",), "different-model"),
            (("dispatch_facade",), "saihai@def456"),
            (("review_target", "repository"), "Saber5656/other"),
            (("review_target", "base_sha"), "6" * 40),
            (("review_target", "reviewed_head_sha"), "7" * 40),
            (("review_target", "target_kind"), "commit"),
            (("review_target", "target_identity"), "sha256:" + "6" * 64),
            (("review_target", "snapshot_digest"), "sha256:" + "7" * 64),
            (("review_target", "artifact_digest"), "sha256:" + "8" * 64),
            (("terminal_result", "verdict"), "findings"),
            (("terminal_result", "findings"), [{"id": "R1"}]),
        ]
        for path, replacement_value in mutations:
            with self.subTest(path=path):
                changed = json.loads(json.dumps(carrier))
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement_value
                changed["result_integrity"]["digest"] = "0" * 64
                changed_digest = run_publication_integrity("carrier-digest", changed)
                self.assertEqual(0, changed_digest.returncode, changed_digest.stderr)
                self.assertNotEqual(expected_digest, changed_digest.stdout)

        terminal_change = json.loads(json.dumps(carrier))
        terminal_change["terminal_status"] = "findings"
        terminal_change["terminal_result"]["status"] = "findings"
        terminal_change["result_integrity"]["digest"] = "0" * 64
        changed_terminal_digest = run_publication_integrity("carrier-digest", terminal_change)
        self.assertEqual(0, changed_terminal_digest.returncode, changed_terminal_digest.stderr)
        self.assertNotEqual(expected_digest, changed_terminal_digest.stdout)

        invalid_carriers = []
        for field, replacement_value in [
            ("algorithm", "sha512"),
            ("payload", "terminal_result"),
            ("canonicalization", "python-sort-keys"),
        ]:
            changed = json.loads(json.dumps(carrier))
            changed["result_integrity"][field] = replacement_value
            invalid_carriers.append(changed)
        mismatched_status = json.loads(json.dumps(carrier))
        mismatched_status["terminal_status"] = "findings"
        invalid_carriers.append(mismatched_status)
        wrong_version = json.loads(json.dumps(carrier))
        wrong_version["review_evidence_version"] = "2"
        invalid_carriers.append(wrong_version)
        wrong_result_schema = json.loads(json.dumps(carrier))
        wrong_result_schema["terminal_result"]["schema_version"] = "2"
        invalid_carriers.append(wrong_result_schema)
        wrong_target_kind = json.loads(json.dumps(carrier))
        wrong_target_kind["review_target"]["target_kind"] = "working_tree"
        invalid_carriers.append(wrong_target_kind)
        unknown = json.loads(json.dumps(carrier))
        unknown["opaque"] = "not-allowlisted"
        invalid_carriers.append(unknown)
        for invalid in invalid_carriers:
            rejected = run_publication_integrity("verify-carrier", invalid)
            self.assertNotEqual(0, rejected.returncode)

    def test_public_review_projection_rejects_unknown_private_and_secret_fields(self) -> None:
        projection = {
            "public_review_summary_version": "1",
            "reviewer_role": "tech-security",
            "reviewed_head_sha": HEAD_SHA,
            "verdict": "approved",
            "finding_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0},
            "validation_summary": ["python tests: pass"],
            "limitations": [],
        }
        accepted = run_publication_integrity("validate-projection", projection)
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        invalid_projections = []
        for value in [
            "/Users/example/private/review.json",
            "C:\\Users\\example\\private\\review.json",
            "/var/folders/private/review.json",
            "Agents-Vault/01-Projects/task.md",
            "request_id=request-123",
            "token sk-abcdefghijklmnop",
        ]:
            changed = json.loads(json.dumps(projection))
            changed["limitations"] = [value]
            invalid_projections.append(changed)
        for field in ("raw_carrier", "session_id", "dispatch_facade", "review_id"):
            changed = json.loads(json.dumps(projection))
            changed[field] = "private"
            invalid_projections.append(changed)
        for invalid in invalid_projections:
            rejected = run_publication_integrity("validate-projection", invalid)
            self.assertNotEqual(0, rejected.returncode)

    def test_active_lineage_transition_is_owned_only_by_saihai_runtime(self) -> None:
        contract = read_remaining_contract()
        normalized_contract = " ".join(contract.split())
        integrity = run_publication_integrity("canonicalize", {})
        self.assertEqual(0, integrity.returncode, integrity.stderr)
        self.assertNotIn("advance-lineage", read_publication_integrity_js())
        self.assertNotIn("bind-lineage-pr", read_publication_integrity_js())
        self.assertNotEqual(
            0,
            run_publication_integrity("advance-lineage", {}).returncode,
        )
        for phrase in [
            "signed Saihai `lineage_activate`",
            "separately identified `lineage_read`",
            "`lineage_bind_pr` with the canonical digest of an attested create/reconciliation result",
            "no coordinator file, shell adapter, or caller database is an authorization source",
        ]:
            self.assertIn(phrase, normalized_contract)

    def test_compact_outcome_identity_and_types_match_saihai_v1(self) -> None:
        contract_text = " ".join(read_remaining_contract().split())
        pr_contract = " ".join(read_pr_contract().split())
        for phrase in [
            "`pr_created_or_reused`, `assignee_postcondition`, `required_check_observation`, or `review_observation`",
            '"identity_version": "1"',
            '"publication_lineage_id": "<active 64-hex lineage id>"',
            '"publication_manifest_sha256": "<active 64-hex manifest digest>"',
            "Every value comes from the separately attested active `lineage_read` result",
            "An unbound PR, unknown event type, or runtime rejection of the active identity digest is a blocker",
        ]:
            self.assertIn(phrase, contract_text)
        for phrase in [
            "The compact runtime row uses only",
            "Every value comes from the separately attested active Saihai lineage with a non-null PR binding",
            "unknown type, caller-selected digest, unbound PR, stale Manifest generation",
        ]:
            self.assertIn(phrase, pr_contract)

    def test_outcome_reducer_enforces_identity_ids_replay_and_promotion(self) -> None:
        lineage_id = "9" * 64
        manifest_digest = "a" * 64

        def jcs_digest(value: dict[str, object]) -> str:
            canonical = run_publication_integrity("canonicalize", value)
            self.assertEqual(0, canonical.returncode, canonical.stderr)
            return hashlib.sha256(canonical.stdout.encode("utf-8")).hexdigest()

        def event(index: int, postcondition: str, result: str = "success") -> dict[str, object]:
            event_type = {
                "pr_identity": "pr_created_or_reused",
                "exact_assignees": "assignee_postcondition",
                "required_checks_current_head": "required_check_observation",
                "configured_reviews_current_head": "review_observation",
                "unresolved_threads_current_head": "review_observation",
            }[postcondition]
            payload: dict[str, object] = {
                "event_index": index,
                "event": event_type,
                "observed_at": "2026-08-26T00:00:00Z",
                "pr_number": 41,
                "base_sha": BASE_SHA,
                "head_sha": HEAD_SHA,
                "postcondition": postcondition,
                "result": result,
                "evidence_digests": ["sha256:" + str(index + 1) * 64],
            }
            payload["event_id"] = "sha256:" + jcs_digest(payload)
            return payload

        postconditions = [
            "pr_identity",
            "exact_assignees",
            "required_checks_current_head",
            "configured_reviews_current_head",
            "unresolved_threads_current_head",
        ]
        events = [event(index, postcondition) for index, postcondition in enumerate(postconditions)]
        delta: dict[str, object] = {
            "delta_version": "1",
            "canonicalization": "RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8",
            "publication_lineage_id": lineage_id,
            "publication_manifest_sha256": manifest_digest,
            "publication_manifest_generation": 1,
            "repository": "Saber5656/skills",
            "base_ref": "refs/heads/main",
            "base_sha": BASE_SHA,
            "head_ref": "refs/heads/codex/skills-pr-merge-safety",
            "head_sha": HEAD_SHA,
            "pr_number": 41,
            "events": events,
        }
        delta["delta_id"] = "sha256:" + jcs_digest(delta)
        active = {
            "publication_lineage_id": lineage_id,
            "active_publication_manifest_sha256": manifest_digest,
            "publication_manifest_generation": 1,
            "repository": "Saber5656/skills",
            "base_ref": "refs/heads/main",
            "head_ref": "refs/heads/codex/skills-pr-merge-safety",
            "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA,
            "publication_pr_number": 41,
        }
        record = {
            "outcome_version": "1",
            "publication_lineage_id": lineage_id,
            "active_publication_manifest_sha256": manifest_digest,
            "publication_manifest_generation": 1,
            "next_append_sequence": 0,
            "accepted_deltas": [],
            "events": [],
            "contradictions": [],
        }
        reduced = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": active,
                "record": record,
                "delta": delta,
                "expected_next_append_sequence": 0,
                "live_identity_verified": True,
            },
        )
        self.assertEqual(0, reduced.returncode, reduced.stderr)
        result = json.loads(reduced.stdout)
        self.assertEqual("appended", result["status"])
        self.assertEqual("pr_created", result["promotion_status"])
        self.assertEqual(5, result["record"]["next_append_sequence"])

        normal_delta: dict[str, object] = {
            **{key: value for key, value in delta.items() if key not in {"delta_id", "events"}},
            "events": events[:3],
        }
        normal_delta["delta_id"] = "sha256:" + jcs_digest(normal_delta)
        normal_reduced = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": active,
                "record": record,
                "delta": normal_delta,
                "expected_next_append_sequence": 0,
                "live_identity_verified": True,
                "review_required": False,
            },
        )
        self.assertEqual(0, normal_reduced.returncode, normal_reduced.stderr)
        self.assertEqual("pr_created", json.loads(normal_reduced.stdout)["promotion_status"])

        replay = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": active,
                "record": result["record"],
                "delta": delta,
                "expected_next_append_sequence": 5,
                "live_identity_verified": True,
            },
        )
        self.assertEqual(0, replay.returncode, replay.stderr)
        self.assertEqual("idempotent_no_op", json.loads(replay.stdout)["status"])

        stale_active = json.loads(json.dumps(active))
        stale_active["active_publication_manifest_sha256"] = "b" * 64
        stale_active["publication_manifest_generation"] = 2
        stale = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": stale_active,
                "record": result["record"],
                "delta": delta,
                "expected_next_append_sequence": 5,
                "live_identity_verified": True,
            },
        )
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("publication_manifest_inactive", stale.stderr)

        conflicting_event = event(0, "exact_assignees", "failed")
        conflicting_delta: dict[str, object] = {
            **{key: value for key, value in delta.items() if key not in {"delta_id", "events"}},
            "events": [conflicting_event],
        }
        conflicting_delta["delta_id"] = "sha256:" + jcs_digest(conflicting_delta)
        conflict = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": active,
                "record": result["record"],
                "delta": conflicting_delta,
                "expected_next_append_sequence": 5,
                "live_identity_verified": True,
            },
        )
        self.assertEqual(0, conflict.returncode, conflict.stderr)
        conflict_result = json.loads(conflict.stdout)
        self.assertEqual("publication_outcome_contradiction", conflict_result["status"])
        self.assertEqual("pr_created_review_pending", conflict_result["promotion_status"])

        unknown_delta = json.loads(json.dumps(delta))
        unknown_delta["raw_evidence"] = "private"
        rejected_unknown = run_publication_integrity(
            "reduce-outcome",
            {
                "active_lineage": active,
                "record": record,
                "delta": unknown_delta,
                "expected_next_append_sequence": 0,
                "live_identity_verified": True,
            },
        )
        self.assertNotEqual(0, rejected_unknown.returncode)
    def test_pr_handoff_and_stop_boundary(self) -> None:
        text = read_skill("gh-deliver-remaining-issues")
        for phrase in [
            "expected_assignees",
            "external_reviewers",
            "external_reviewers.coderabbit.required",
            "pr_created_ci_pending",
            "policy_merge_ready",
            "After required CI and all applicable policy gates pass, hand the PR to `pr-merge-gate` for autonomous merge",
            "normal-risk PR can proceed without a reviewer response",
            "Do not start a new platform-bot review for the fix",
            "public-safe review summary projection",
            "complete review carriers",
            "next Manifest generation",
        ]:
            self.assertIn(phrase, text)


class MergeContextSafetyTest(unittest.TestCase):
    def test_mixed_context_performs_no_local_or_pr_mutation(self) -> None:
        text = read_skill("merge")
        for phrase in [
            "`mixed_context`としてfail closed",
            "local側を候補化せず",
            "fetch、commit、stash、`git merge`を含む全mutationを実行しない",
            "別のtask/processを要求し直すのは",
            "required_handoff: typed_saihai_merge_envelope",
        ]:
            self.assertIn(phrase, text)
        evals = json.loads((ROOT / "merge" / "evals" / "evals.json").read_text(encoding="utf-8"))
        mixed = next(item for item in evals["evals"] if item["id"] == 10)
        self.assertIn("performs no fetch, commit, stash, local git merge, or PR handoff", mixed["expected_output"])
        self.assertNotIn("Preserves local merge behavior", mixed["expectations"])


class EvalInventoryTest(unittest.TestCase):
    def test_changed_skill_eval_ids_are_unique(self) -> None:
        minimums = {
            "pr": 56,
            "pr-review-fix-policy": 38,
            "merge": 10,
            "gh-deliver-remaining-issues": 39,
            "pr-merge-gate": 19,
            "push": 17,
        }
        for name, minimum in minimums.items():
            with self.subTest(skill=name):
                data = json.loads((ROOT / name / "evals" / "evals.json").read_text(encoding="utf-8"))
                ids = [item["id"] for item in data["evals"]]
                self.assertGreaterEqual(len(ids), minimum)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(data["skill_name"], name)


if __name__ == "__main__":
    unittest.main()
