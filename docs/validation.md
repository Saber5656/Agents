# Reproducible validation

Use Python 3.11 or newer in a clean checkout. Install the declared development dependency and run the single entrypoint:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/validate.py
```

The command runs every test under `tests/` and `skills/`, then runs the portfolio scanner against the repository's nested `skills/*` scope at the current Git `HEAD`. It prints discovered, passed, failed, error, skipped, and xfailed counts, followed by skill, finding, blocker, compliance-status, and scope-digest counts. A missing `pytest`, missing scanner, missing evidence output, or incomplete audit is an error; no missing check is treated as a pass.

The scanner requires a clean selected scope. Run it after committing the revision under test, or use `python scripts/validate.py --skip-portfolio` only when you are iterating on unrelated uncommitted changes. The skip is explicit and reported.

Portfolio reports are written to a temporary directory and removed after the command. To retain evidence, invoke the scanner directly with an evidence path outside the repository and a manifest whose `repository_root` is the Git root and whose scope includes `skills/*`.

The repository contains deterministic unit and contract tests. Prompt eval JSON files document behavior scenarios and assertions, but this baseline does not fabricate model outputs or claim a model benchmark from metadata alone. Retired control-plane expectations are represented by explicit retirement tests; they are not used as current compliance evidence.
