import json

from math_research_agent.research.file_ingestion import ProjectFileIngestor
from math_research_agent.research.project import ProjectStore


def test_unstructured_file_is_archived_and_materialized(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Imported project")
    source = tmp_path / "notes.md"
    source.write_text("# Earlier result\n\nA useful observation.\n", encoding="utf-8")

    ingestor = ProjectFileIngestor(project)
    first = ingestor.add(source)
    duplicate = ingestor.add(source)
    assert duplicate["duplicate"] is True
    assert first["status"] == "PENDING"
    assert (project.root / first["inbox_path"]).is_file()

    processed = ingestor.prepare_pending()
    assert processed[0]["status"] == "READY"
    working = project.root / first["work_path"]
    analysis = project.root / first["analysis_path"]
    assert working.is_file()
    assert analysis.is_file()
    assert "not an automatically proved theorem" in working.read_text(encoding="utf-8")
    assert json.loads(analysis.read_text(encoding="utf-8"))["title"] == "Earlier result"

    sources = ingestor.ready_sources()
    assert sources[0]["original_name"] == "notes.md"
    assert "A useful observation" in sources[0]["content"]


def test_pdf_extraction_failure_is_visible_and_original_is_kept(tmp_path, monkeypatch):
    project = ProjectStore.initialize(tmp_path / "project", "Imported project")
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"not a real pdf")
    record = ProjectFileIngestor(project).add(source)

    monkeypatch.setattr(
        "math_research_agent.research.file_ingestion.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1, "stderr": "bad pdf", "stdout": ""})(),
    )
    processed = ProjectFileIngestor(project).prepare_pending()
    assert processed[0]["status"] == "ERROR"
    assert "bad pdf" in processed[0]["error"]
    assert (project.root / record["inbox_path"]).is_file()
