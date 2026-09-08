# Reproducible validation

Use Python 3.11 or newer in a clean checkout. Install the declared development dependency and run the single entrypoint:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/validate.py
```

`requirements-dev.txt` pins `pytest==8.4.2`. The CI workflow uses the same
entrypoint and checks that the checkout is exactly `$GITHUB_SHA` before running
it. Official GitHub Actions are pinned to full commit SHAs.

The command runs every test under `tests/` and `skills/`, then runs the portfolio scanner against the repository's nested `skills/*` scope at the current Git `HEAD`. It prints discovered, passed, failed, error, skipped, and xfailed counts, followed by skill, finding, blocker, compliance-status, and scope-digest counts. A missing `pytest`, missing scanner, missing evidence output, or incomplete audit is an error; no missing check is treated as a pass.

The scanner requires a clean selected scope. Run it after committing the revision under test, or use `python scripts/validate.py --skip-portfolio` only when you are iterating on unrelated uncommitted changes. The skip is explicit and reported.

Portfolio reports are written to a temporary directory and removed after the command. To retain the complete run, pass an explicit evidence directory:

```bash
python scripts/validate.py --evidence-dir /tmp/agents-validation
```

The directory receives raw `pytest-output.txt`, scanner output, and
`portfolio-audit.json`/`portfolio-audit.md`. Validation errors are recorded in
`validation-error.txt`; test and scanner failures still retain their output.
Existing files are never overwritten: a repeated name receives a numeric
suffix such as `pytest-output.1.txt`.

The repository contains deterministic unit and contract tests. Prompt eval JSON files document behavior scenarios and assertions, but this baseline does not fabricate model outputs or claim a model benchmark from metadata alone. The active workflow replaced the Saihai and flat-root contracts that were asserted by selected legacy compatibility cases. `skills/conftest.py` marks exactly those individual cases skipped with an explicit reason and successor path; `-rs` output is printed by the validator, while passing cases in the same modules remain active. Current behavior is covered by `skills/CURRENT-WORKFLOW.md` plus the delivery, runner, installation, and task tests under `tests/`. Publication hygiene tests remain active: safe example-key fixtures use narrow negations, while the tracked `skills/kanary` symlink is checked as a local-state boundary and is never traversed as public source.
