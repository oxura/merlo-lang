from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research.archive.historical_protocol.merlo import (
    AnalysisError,
    ChangeBlocked,
    EditCapability,
    SoftwareWorld,
    scan_python,
)
from merlo.cli import main
from merlo.project import Project, resolve_dependencies


def _workspace(root: Path, *, dynamic: bool = False, wildcard: bool = False) -> None:
    package = root / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f\"Hello, {name}\"\n"
        "\n"
        "def local_call() -> str:\n"
        "    return greet(\"local\")\n",
        encoding="utf-8",
    )
    imports = (
        "from app.service import *\n"
        if wildcard
        else (
            "from app.service import greet\n"
            "from app.service import greet as aliased_greet\n"
        )
    )
    dynamic_line = (
        "    return getattr(service, \"greet\")(\"Ada\")\n"
        if dynamic
        else (
            "    return (greet(\"Ada\") + aliased_greet(\"Bob\") "
            "+ service.greet(\"Cy\"))\n"
        )
    )
    (package / "consumer.py").write_text(
        imports
        + "import app.service as service\n"
        + "\n"
        + "def run() -> str:\n"
        + dynamic_line
        + "\n"
        + "def shadowed(greet):\n"
        + "    return greet(\"not the module symbol\")\n",
        encoding="utf-8",
    )


def _target(world: SoftwareWorld):
    return world.program.entity("app.service.greet")

def _rename_capability(target_id: str, **kwargs):
    return EditCapability.rename(
        target_id,
        allow_public_api_break=True,
        **kwargs,
    )


class TestProgramIR:
    def test_scan_builds_stable_entities_references_and_calls(self, tmp_path: Path):
        _workspace(tmp_path)

        first = scan_python(tmp_path)
        target = first.entity("app.service.greet")
        second = scan_python(tmp_path, previous=first)

        assert second.entity(target.id).fqname == "app.service.greet"
        assert second.entity(target.id).revision_hash == target.revision_hash
        assert len(second.references_to(target.id)) == 6
        assert len([edge for edge in second.calls if edge.target_id == target.id]) == 4

    def test_source_change_without_changeir_gets_review_only_identity(
        self, tmp_path: Path
    ):
        _workspace(tmp_path)
        first = scan_python(tmp_path)
        original = first.entity("app.service.greet")
        service = tmp_path / "app" / "service.py"
        service.write_text(
            service.read_text(encoding="utf-8").replace("Hello", "Welcome"),
            encoding="utf-8",
        )

        second = scan_python(tmp_path, previous=first)
        changed = second.entity("app.service.greet")

        assert changed.id != original.id
        assert changed.identity_status == "Probable"
        assert changed.revision_hash != original.revision_hash
        assert any(
            relation.old_id == original.id
            and relation.new_id == changed.id
            and relation.status == "Probable"
            for relation in second.identity_relations
        )

    def test_utf8_bom_is_scanned_without_changing_the_source(self, tmp_path: Path):
        source = tmp_path / "module.py"
        source.write_bytes(b"\xef\xbb\xbfdef greet():\n    return 'hi'\n")

        program = scan_python(tmp_path)

        assert program.entity("module.greet").name == "greet"
        assert source.read_bytes().startswith(b"\xef\xbb\xbf")
        world = SoftwareWorld.scan(tmp_path)
        target = world.program.entity("module.greet")
        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )
        world.apply(plan)
        assert source.read_bytes().startswith(b"\xef\xbb\xbf")
        assert b"def welcome" in source.read_bytes()

    def test_invalid_source_is_a_structured_scan_failure(self, tmp_path: Path):
        (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

        with pytest.raises(AnalysisError, match="broken.py:1"):
            scan_python(tmp_path)


class TestSemanticRename:
    def test_preview_covers_bound_references_but_preserves_alias_uses(
        self, tmp_path: Path
    ):
        _workspace(tmp_path)
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)
        capability = _rename_capability(target.id)

        plan = world.plan_rename(target.id, "welcome", capability)

        assert plan.ready
        assert plan.affected_files == ("app/consumer.py", "app/service.py")
        assert len(plan.edits) == 6
        assert any(
            item.details.get("stable_alias_references") == 1
            for item in plan.evidence
        )
        assert "def greet" in (tmp_path / "app" / "service.py").read_text(
            encoding="utf-8"
        )

    def test_function_local_imports_are_migrated(self, tmp_path: Path):
        _workspace(tmp_path)
        consumer = tmp_path / "app" / "consumer.py"
        consumer.write_text(
            "def run() -> str:\n"
            "    from app.service import greet\n"
            "    from app.service import greet as stable_greet\n"
            "    return greet(\"Ada\") + stable_greet(\"Bob\")\n",
            encoding="utf-8",
        )
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)

        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )
        world.apply(plan)

        migrated = consumer.read_text(encoding="utf-8")
        assert "from app.service import welcome\n" in migrated
        assert "from app.service import welcome as stable_greet" in migrated
        assert 'return welcome("Ada") + stable_greet("Bob")' in migrated

    def test_apply_is_a_semantic_transaction_and_keeps_entity_id(
        self, tmp_path: Path
    ):
        _workspace(tmp_path)
        state = tmp_path / ".meldra" / "world.json"
        world = SoftwareWorld.scan(tmp_path, state)
        target = _target(world)
        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )

        changed_files = world.apply(plan)

        assert changed_files == ("app/consumer.py", "app/service.py")
        renamed = world.program.entity(target.id)
        assert renamed.fqname == "app.service.welcome"
        assert renamed.revision_hash != target.revision_hash
        assert renamed.identity_status == "Exact"
        service = (tmp_path / "app" / "service.py").read_text(encoding="utf-8")
        consumer = (tmp_path / "app" / "consumer.py").read_text(encoding="utf-8")
        assert "def welcome" in service
        assert 'welcome("local")' in service
        assert "from app.service import welcome\n" in consumer
        assert "from app.service import welcome as aliased_greet" in consumer
        assert 'aliased_greet("Bob")' in consumer
        assert 'service.welcome("Cy")' in consumer
        assert 'def shadowed(greet)' in consumer
        assert state.exists()
        assert world.evolution_log[-1]["target_ids"] == [target.id]
        ast.parse(service)
        ast.parse(consumer)

    def test_edit_capability_blocks_files_outside_scope(self, tmp_path: Path):
        _workspace(tmp_path)
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)
        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id, allowed_files=["app/service.py"],),
        )

        assert not plan.ready
        assert {item.kind for item in plan.obligations} == {"FileScopeDenied"}
        scope_evidence = next(
            item for item in plan.evidence if item.kind == "edit_scope"
        )
        assert scope_evidence.level == "Unresolved"
        assert scope_evidence.details["passed"] is False
        with pytest.raises(ChangeBlocked):
            world.apply(plan)
        assert "def greet" in (tmp_path / "app" / "service.py").read_text(
            encoding="utf-8"
        )

    @pytest.mark.parametrize("hazard", ["dynamic", "wildcard"])
    def test_unresolved_bindings_become_blocking_obligations(
        self, tmp_path: Path, hazard: str
    ):
        _workspace(
            tmp_path,
            dynamic=hazard == "dynamic",
            wildcard=hazard == "wildcard",
        )
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)

        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )

        assert not plan.ready
        kinds = {item.kind for item in plan.obligations}
        expected = "DynamicReference" if hazard == "dynamic" else "WildcardReference"
        assert expected in kinds

    def test_string_exports_require_an_explicit_compatibility_decision(
        self, tmp_path: Path
    ):
        _workspace(tmp_path)
        service = tmp_path / "app" / "service.py"
        service.write_text(
            '__all__ = ["greet"]\n' + service.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)

        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )

        assert not plan.ready
        assert "StringReference" in {item.kind for item in plan.obligations}

    def test_source_drift_blocks_before_any_file_is_written(self, tmp_path: Path):
        _workspace(tmp_path)
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)
        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )
        consumer = tmp_path / "app" / "consumer.py"
        consumer.write_text(
            consumer.read_text(encoding="utf-8") + "# concurrent user edit\n",
            encoding="utf-8",
        )

        with pytest.raises(ChangeBlocked, match="changed after semantic scan"):
            world.apply(plan)

        assert "def greet" in (tmp_path / "app" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "# concurrent user edit" in consumer.read_text(encoding="utf-8")

    def test_name_collision_is_reported_before_materialization(self, tmp_path: Path):
        _workspace(tmp_path)
        service = tmp_path / "app" / "service.py"
        service.write_text(
            service.read_text(encoding="utf-8")
            + "\ndef welcome(name: str) -> str:\n    return name\n",
            encoding="utf-8",
        )
        world = SoftwareWorld.scan(tmp_path)
        target = _target(world)

        plan = world.plan_rename(
            target.id,
            "welcome",
            _rename_capability(target.id),
        )

        assert not plan.ready
        assert "NameCollision" in {item.kind for item in plan.obligations}


class TestMerloCLI:
    def test_check_and_apply_rename_end_to_end(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        project = Project.create(tmp_path / "app", name="app")
        source = project.source_dir / "main.mlo"
        source.write_text(
            "module main\n\n"
            "export enum AppError:\n"
            "    Failed\n\n"
            "fn helper(path: Path) -> Text:\n"
            "    return \"helper\"\n\n"
            "export task main(path: Path) -> Result[Text, AppError]:\n"
            "    uses console.write\n"
            "    let result: Text = helper(path)\n"
            "    console.write(result)\n"
            "    return Ok(result)\n",
            encoding="utf-8",
        )
        resolve_dependencies(project)

        assert main(["check", str(project.root), "--json"]) == 0
        checked = json.loads(capsys.readouterr().out)
        assert checked["ok"] is True

        result = main(
            [
                "refactor",
                "rename",
                "main.helper",
                "welcome",
                str(project.root),
                "--apply",
                "--json",
            ]
        )
        rename_output = json.loads(capsys.readouterr().out)

        assert result == 0
        assert rename_output["committed"] is True
        assert "fn welcome" in source.read_text(encoding="utf-8")
