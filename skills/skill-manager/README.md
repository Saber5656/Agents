# skill-manager

Read-only portfolio meta-QA for skills: inventory, deterministic contract checks, semantic duplicate/responsibility/trigger proposals, evidence freshness, and lifecycle handoffs.

It never deletes, merges, renames, rewrites, or retires a skill. Approved existing-skill changes go to `skill-updater`; approved new capability goes to `skill-creator`.

```bash
python3 scripts/scan_skill_portfolio.py \
  --manifest /path/to/audit-manifest.json \
  --json /outside/audited/repo/portfolio-audit.json \
  --markdown /outside/audited/repo/portfolio-audit.md
```

The manifest pins a clean full Git revision, bounded include/exclude scope, provenance profiles, optional previous report, and `report_only | new_blockers` fail policy. Set `repository_root` to the Git repository root and use repository-relative scope patterns. For this repository's nested skill tree, use `"include": ["skills/*"]`; invoking the scanner with `skills/` as an unrelated path or with `agents-sdk` as a root-relative Git path loses the tracked path binding. Evidence outputs inside the audited repository are rejected.

The scanner ignores unrelated untracked paths outside the explicit scope. A selected directory or nested file that is a symlink is rejected, including a symlink that resolves outside the repository. Dirty selected files, a deleted selected skill, or a revision mismatch produce JSON and Markdown with `audit.status: audit_incomplete` and exit code `2`; they are never reported as a successful clean audit.

The report's deterministic hard gate is not a general compliance verdict. Unknown provenance and wording-based read/write signals remain review candidates and set `summary.compliance_status` to `unverified`.
