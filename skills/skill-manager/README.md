# skill-manager

Read-only portfolio meta-QA for skills: inventory, deterministic contract checks, semantic duplicate/responsibility/trigger proposals, evidence freshness, and lifecycle handoffs.

It never deletes, merges, renames, rewrites, or retires a skill. Approved existing-skill changes go to `skill-updater`; approved new capability goes to `skill-creator`.

```bash
python3 scripts/scan_skill_portfolio.py \
  --manifest /path/to/audit-manifest.json \
  --json /outside/audited/repo/portfolio-audit.json \
  --markdown /outside/audited/repo/portfolio-audit.md
```

The manifest pins a clean full Git revision, bounded include/exclude scope, provenance profiles, optional previous report, and `report_only | new_blockers` fail policy. Evidence outputs inside the audited repository are rejected.
