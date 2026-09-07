import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_decision_manifest.py"
SPEC = importlib.util.spec_from_file_location("manifest_validator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_manifest():
    digest = "a" * 64
    proposal = {
        "bundle_id": "p1", "repository": "owner/repo", "base_sha": "b" * 40,
        "evidence_binding": "bound_repository", "generated_at": "2026-07-16T00:00:00Z",
        "source_manifest_digest": digest,
        "artifacts": [{"artifact_id": "design", "media_type": "text/markdown", "resolver": "repository_relative_file", "locator": "proposals/v1.md", "exact_bytes_sha256": digest}],
    }
    proposal["bundle_digest"] = MODULE.canonical_bundle_digest(proposal)
    return {
        "schema_version": "1", "manifest_id": "m1", "mode": "approved-apply",
        "repository": {"root": "/repo", "owner_repo": "owner/repo", "base_sha": "b" * 40, "observed_at": "2026-07-16T00:00:00Z", "issue_snapshot_digest": digest},
        "proposal": proposal,
        "approval": {
            "owner": "user", "approved_at": "2026-07-16T00:00:00Z", "source": {"kind": "explicit_user_instruction", "locator": "turn-1"},
            "approved_decision_ids": ["d1"], "approved_mutation_ids": ["m1"],
        },
        "decisions": [{"id": "d1", "category": "v1_scope", "statement": "ship x", "rationale": "goal", "source": "turn-1", "approved": True}],
        "constraints": {
            "exact_paths": ["docs/v1.md"], "excluded_paths": [], "github_actions_allowed": [],
            "no_product_code": True, "no_commit_push_pr_merge_release": True,
        },
        "mutations": [{
            "id": "m1", "action": "create_doc", "exact_target": "docs/v1.md",
            "allowed": True, "decision_ids": ["d1"], "expected_before": {"state": "absent", "digest": None},
            "proposed_after_digest": digest, "payload_artifact_id": "design",
        }],
    }


class DecisionManifestTests(unittest.TestCase):
    def test_accepts_exact_approved_scope(self):
        self.assertEqual([], MODULE.validate(valid_manifest()))

    def test_rejects_unapproved_mutation(self):
        data = valid_manifest()
        data["approval"]["approved_mutation_ids"] = []
        self.assertIn("approval mutation IDs must exactly match mutation objects", MODULE.validate(data))

    def test_rejects_wildcard_target(self):
        data = valid_manifest()
        data["mutations"][0]["exact_target"] = "docs/*"
        self.assertTrue(any("exact_target must be exact" in error for error in MODULE.validate(data)))

    def test_requires_distinct_issue_authorization(self):
        data = valid_manifest()
        data["mutations"][0].update(action="create_issue", exact_target="owner/repo#new")
        self.assertTrue(any("distinct authorization" in error for error in MODULE.validate(data)))

    def test_requires_schema_fields_not_only_digests(self):
        data = valid_manifest()
        del data["approval"]["approved_at"]
        self.assertIn("approval.approved_at is required", MODULE.validate(data))

    def test_rejects_artifact_change_without_new_bundle_digest(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = "c" * 64
        self.assertIn("proposal.bundle_digest does not match canonical preimage", MODULE.validate(data))

    def test_rejects_doc_update_without_present_digest(self):
        data = valid_manifest()
        data["mutations"][0]["action"] = "update_doc"
        self.assertIn("update_doc requires a present digest precondition", MODULE.validate(data))

    def test_accepts_doc_update_with_present_digest(self):
        data = valid_manifest()
        data["mutations"][0]["action"] = "update_doc"
        data["mutations"][0]["expected_before"] = {"state": "present", "digest": "c" * 64}
        self.assertEqual([], MODULE.validate(data))

    def test_rejects_changed_locator_payload(self):
        data = valid_manifest()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "proposals" / "v1.md"
            target.parent.mkdir()
            target.write_bytes(b"approved")
            data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = hashlib.sha256(b"approved").hexdigest()
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            self.assertEqual([], MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))
            target.write_bytes(b"changed after approval")
            self.assertIn("proposal.artifacts[0] payload digest mismatch", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))

    def test_rejects_artifact_locator_escape(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["locator"] = "../outside.md"
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("proposal.artifacts[0] locator escapes repository.root", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))

    def test_rejects_absolute_artifact_locator(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["locator"] = "/tmp/outside.md"
        self.assertIn("proposal.artifacts[0] locator must be repository-relative", MODULE.validate_artifact_payloads(data["proposal"], Path("/repo")))

    def test_rejects_symlink_escape_and_missing_payload(self):
        data = valid_manifest()
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            link = Path(temp) / "proposals"
            link.symlink_to(Path(outside), target_is_directory=True)
            data["proposal"]["artifacts"][0]["locator"] = "proposals/v1.md"
            self.assertIn("proposal.artifacts[0] locator escapes repository.root", MODULE.validate_artifact_payloads(data["proposal"], Path(temp)))
            link.unlink()
            self.assertTrue(any("payload is unreadable" in item for item in MODULE.validate_artifact_payloads(data["proposal"], Path(temp))))

    def test_rejects_unsupported_resolver_and_unexpected_property(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["resolver"] = "url"
        self.assertIn("proposal.artifacts[0].resolver is unsupported", MODULE.validate(data))
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["extra"] = "not canonical"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.artifacts[0] has unexpected or missing properties", MODULE.validate(data))

    def test_target_state_gate_is_idempotent_or_fail_closed(self):
        mutation = valid_manifest()["mutations"][0]
        self.assertEqual("apply", MODULE.classify_target_state(mutation, "absent", None))
        self.assertEqual("idempotent_skip", MODULE.classify_target_state(mutation, "present", "a" * 64))
        self.assertEqual("stale_target", MODULE.classify_target_state(mutation, "present", "c" * 64))

    def test_cli_returns_structured_error_for_malformed_artifacts(self):
        data = valid_manifest()
        data["proposal"]["artifacts"] = None
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(json.dumps(data))
            completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["valid"])
        self.assertIn("proposal.artifacts must be a non-empty array", payload["errors"])
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_returns_structured_error_for_malformed_artifact_id(self):
        data = valid_manifest()
        data["proposal"]["artifacts"][0]["artifact_id"] = {}
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(json.dumps(data))
            completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["valid"])
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_returns_structured_errors_for_malformed_enum_values(self):
        mutations = [
            lambda data: data["approval"].update(owner={}),
            lambda data: data["approval"]["source"].update(kind={}),
            lambda data: data["decisions"][0].update(category={}),
            lambda data: data["mutations"][0].update(action={}),
            lambda data: data["mutations"][0]["expected_before"].update(state={}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = valid_manifest()
                mutate(data)
                with tempfile.TemporaryDirectory() as temp:
                    manifest = Path(temp) / "manifest.json"
                    manifest.write_text(json.dumps(data))
                    completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
                self.assertEqual(1, completed.returncode)
                self.assertFalse(json.loads(completed.stdout)["valid"])
                self.assertNotIn("Traceback", completed.stderr)

    def test_cli_returns_structured_errors_for_unsafe_path_values(self):
        mutations = [
            lambda data: data["repository"].update(root=1),
            lambda data: data["repository"].update(root="/tmp/abc\x00x"),
            lambda data: data["proposal"]["artifacts"][0].update(locator="proposals/x\x00.md"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = valid_manifest()
                mutate(data)
                with tempfile.TemporaryDirectory() as temp:
                    manifest = Path(temp) / "manifest.json"
                    manifest.write_text(json.dumps(data))
                    completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
                self.assertEqual(1, completed.returncode)
                self.assertFalse(json.loads(completed.stdout)["valid"])
                self.assertNotIn("Traceback", completed.stderr)

    def test_rejects_proposal_bound_to_another_base(self):
        data = valid_manifest()
        data["proposal"]["base_sha"] = "c" * 40
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.base_sha must match repository.base_sha", MODULE.validate(data))

    def test_rejects_proposal_bound_to_another_repository(self):
        data = valid_manifest()
        data["proposal"]["repository"] = "other/repo"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertIn("proposal.repository must match the apply repository identity", MODULE.validate(data))

    def test_accepts_explicit_local_repository_identity(self):
        data = valid_manifest()
        data["repository"]["owner_repo"] = None
        data["proposal"]["repository"] = "local:/repo"
        data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
        self.assertEqual([], MODULE.validate(data))

    def test_unbound_proposal_is_noncanonical_and_not_applicable(self):
        contract = (Path(__file__).parents[1] / "SKILL.md").read_text()
        self.assertIn("evidence_binding: unbound_concept", contract)
        self.assertIn("require a new bound proposal/audit before approved-apply", contract)

    def test_audit_and_apply_still_require_immutable_repository(self):
        contract = (Path(__file__).parents[1] / "SKILL.md").read_text()
        self.assertIn("audit/apply lacks repository, explicit mode, immutable base", contract)

    def test_malformed_approval_id_arrays_return_errors(self):
        for field, value in (("approved_decision_ids", None), ("approved_mutation_ids", 1)):
            with self.subTest(field=field):
                data = valid_manifest()
                data["approval"][field] = value
                errors = MODULE.validate(data)
                self.assertTrue(any(field.split("approved_")[1].replace("_ids", "") in error for error in errors))

    def test_rejects_issue_target_in_another_repository_and_missing_observation(self):
        data = valid_manifest()
        data["mutations"][0].update(action="create_issue", exact_target="other/repo#new")
        data["constraints"]["github_actions_allowed"] = ["create_issue"]
        data["repository"]["observed_at"] = None
        errors = MODULE.validate(data)
        self.assertTrue(any("timezone-aware repository.observed_at" in error for error in errors))
        self.assertTrue(any("target must belong to repository.owner_repo" in error for error in errors))

    def test_rejects_unbound_doc_payload(self):
        data = valid_manifest()
        data["mutations"][0]["payload_artifact_id"] = "missing"
        self.assertTrue(any("doc payload is not bound" in error for error in MODULE.validate(data)))

    def test_rejects_doc_target_outside_repository(self):
        for target in ("../outside.md", "/tmp/outside.md"):
            with self.subTest(target=target):
                data = valid_manifest()
                data["mutations"][0]["exact_target"] = target
                data["constraints"]["exact_paths"] = [target]
                self.assertTrue(any("escapes repository.root" in error for error in MODULE.validate(data)))

    def test_rejects_non_string_mutation_decision_ids_without_crashing(self):
        data = valid_manifest()
        data["mutations"][0]["decision_ids"] = [{}]
        errors = MODULE.validate(data)
        self.assertIn("mutations[0].decision_ids must be a string array", errors)

    def test_runtime_rejects_changed_head_and_stale_doc_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "seed.txt").write_text("seed")
            subprocess.run(["git", "add", "seed.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            data = valid_manifest()
            data["repository"]["root"] = str(root)
            data["repository"]["base_sha"] = "b" * 40
            data["proposal"]["base_sha"] = "b" * 40
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            target = root / "docs" / "v1.md"
            target.parent.mkdir()
            target.write_text("changed")
            errors = MODULE.validate_runtime_state(data)
            self.assertIn("repository HEAD does not match repository.base_sha", errors)
            self.assertTrue(any("target precondition is stale" in error for error in errors))

    def test_runtime_accepts_current_head_and_idempotent_doc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            target = root / "docs" / "v1.md"
            target.parent.mkdir()
            target.write_bytes(b"approved")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            data = valid_manifest()
            digest = hashlib.sha256(b"approved").hexdigest()
            data["repository"].update(root=str(root), base_sha=head)
            data["proposal"].update(repository="owner/repo", base_sha=head)
            data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = digest
            data["mutations"][0]["proposed_after_digest"] = digest
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            self.assertEqual([], MODULE.validate_runtime_state(data))

    def test_cli_accepts_pending_doc_create_with_distinct_payload_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            payload = root / "proposals" / "v1.md"
            payload.parent.mkdir()
            payload.write_bytes(b"approved")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            data = valid_manifest()
            digest = hashlib.sha256(b"approved").hexdigest()
            data["repository"].update(root=str(root), base_sha=head)
            data["proposal"].update(base_sha=head)
            data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = digest
            data["mutations"][0]["proposed_after_digest"] = digest
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(data))
            completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])

    def test_cli_accepts_pending_doc_update_with_distinct_payload_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            payload = root / "proposals" / "v1.md"
            target = root / "docs" / "v1.md"
            payload.parent.mkdir()
            target.parent.mkdir()
            payload.write_bytes(b"after")
            target.write_bytes(b"before")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            data = valid_manifest()
            after_digest = hashlib.sha256(b"after").hexdigest()
            before_digest = hashlib.sha256(b"before").hexdigest()
            data["repository"].update(root=str(root), base_sha=head)
            data["proposal"].update(base_sha=head)
            data["proposal"]["artifacts"][0]["exact_bytes_sha256"] = after_digest
            data["mutations"][0].update(
                action="update_doc", expected_before={"state": "present", "digest": before_digest},
                proposed_after_digest=after_digest,
            )
            data["proposal"]["bundle_digest"] = MODULE.canonical_bundle_digest(data["proposal"])
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(data))
            completed = subprocess.run([sys.executable, str(MODULE_PATH), str(manifest)], capture_output=True, text=True)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
