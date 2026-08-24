import json

import pytest

from math_research_agent.research.certification import (
    CERTIFICATION_AUDIT_CONTRACT,
    ReplayCertificationRunner,
)
from math_research_agent.research.project import ProjectError


def test_certification_prompt_pins_audit_enums_and_rejects_synonyms():
    assert '"PASS", "FAIL", or "INCONCLUSIVE"' in CERTIFICATION_AUDIT_CONTRACT
    assert '"OK" or "ERROR"' in CERTIFICATION_AUDIT_CONTRACT
    assert '"COMPLETED" or "CERTIFIED"' in CERTIFICATION_AUDIT_CONTRACT


def test_certification_requires_exactly_two_workers(tmp_path):
    with pytest.raises(ProjectError, match="exactly two"):
        ReplayCertificationRunner(
            spec_path=tmp_path / "missing-spec.json",
            repair_root=tmp_path / "repair",
            source_root=tmp_path / "source",
            config_path=tmp_path / "models.toml",
            output_dir=tmp_path / "output",
            semantic_registry_path=tmp_path / "semantics.json",
            worker_count=3,
        )


@pytest.mark.parametrize("protected_name", ["repair", "source"])
def test_certification_output_cannot_be_inside_read_only_root(tmp_path, protected_name):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({}), encoding="utf-8")
    repair_root = tmp_path / "repair"
    source_root = tmp_path / "source"
    protected_root = repair_root if protected_name == "repair" else source_root

    with pytest.raises(ProjectError, match="output must be outside"):
        ReplayCertificationRunner(
            spec_path=spec_path,
            repair_root=repair_root,
            source_root=source_root,
            config_path=tmp_path / "models.toml",
            output_dir=protected_root / "certification-output",
            semantic_registry_path=tmp_path / "semantics.json",
            worker_count=2,
        )
