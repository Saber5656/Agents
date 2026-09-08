import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate.py"
SPEC = importlib.util.spec_from_file_location("validate", SCRIPT)
assert SPEC and SPEC.loader
validate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate)


def test_evidence_writer_preserves_existing_artifacts(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    existing = evidence / "pytest-output.txt"
    existing.write_text("previous run\n", encoding="utf-8")

    writer = validate.EvidenceWriter(evidence)
    saved = writer.save_text("pytest-output.txt", "current run\n")

    assert saved == evidence / "pytest-output.1.txt"
    assert existing.read_text(encoding="utf-8") == "previous run\n"
    assert saved.read_text(encoding="utf-8") == "current run\n"


def test_evidence_writer_copies_reports_without_overwriting(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    source = tmp_path / "portfolio-audit.json"
    source.write_text('{"status": "complete"}\n', encoding="utf-8")
    evidence.mkdir()
    (evidence / "portfolio-audit.json").write_text("kept\n", encoding="utf-8")

    saved = validate.EvidenceWriter(evidence).save_copy("portfolio-audit.json", source)

    assert saved == evidence / "portfolio-audit.1.json"
    assert (evidence / "portfolio-audit.json").read_text(encoding="utf-8") == "kept\n"
    assert saved.read_text(encoding="utf-8") == '{"status": "complete"}\n'
