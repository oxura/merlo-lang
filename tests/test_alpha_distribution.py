from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

STDLIB_PATHS = (
    "stdlib/std/core.mlo",
    "stdlib/std/option.mlo",
    "stdlib/std/result.mlo",
    "stdlib/std/text.mlo",
    "stdlib/std/bytes.mlo",
    "stdlib/std/collections.mlo",
    "stdlib/std/io.mlo",
    "stdlib/std/fs.mlo",
    "stdlib/std/cli.mlo",
    "stdlib/std/time.mlo",
    "stdlib/std/random.mlo",
    "stdlib/std/json.mlo",
    "stdlib/std/net.mlo",
    "stdlib/std/http.mlo",
    "src/merlo/stdlib/json.mlo",
    "src/merlo/stdlib/csv.mlo",
)

DOC_PATHS = tuple(f"docs/{name}.md" for name in (
    "README", "architecture", "project-history", "installation", "tour",
    "types", "ownership", "errors", "effects", "capabilities", "resources",
    "modules", "projects", "packages", "ffi", "semantic-world",
    "alpha-protocol", "ai-protocol", "tooling", "lsp", "examples",
    "limitations",
))
SPEC_PATHS = tuple(f"spec/{name}.md" for name in (
    "README", "language", "ownership", "effects", "packages", "ffi",
    "semantic-world", "alpha-protocol",
))
RESEARCH_PATHS = tuple(f"research/{name}.md" for name in (
    "README", "semantic-representations", "ownership-without-lifetime-syntax",
    "concise-canonical-equivalence", "effects-and-capabilities",
    "performance-evidence",
))
RFC_PATHS = ("rfcs/README.md",)
EXAMPLE_PATHS = (
    "examples/README.md",
    "examples/automation/merlo.toml", "examples/automation/merlo.lock",
    "examples/automation/src/main.mlo", "examples/automation/src/report.mlo",
    "examples/automation/tests/automation.mlo",
    "examples/packages/merlo.toml", "examples/packages/merlo.lock",
    "examples/packages/src/main.mlo", "examples/packages/src/greeting.mlo",
    "examples/packages/tests/packages.mlo",
    "examples/packages/vendor/greeting/merlo.toml",
    "examples/packages/vendor/greeting/merlo.lock",
    "examples/packages/vendor/greeting/src/main.mlo",
    "examples/network/merlo.toml", "examples/network/merlo.lock",
    "examples/network/src/main.mlo", "examples/network/tests/network.mlo",
    "examples/ndjson/merlo.toml", "examples/ndjson/merlo.lock",
    "examples/ndjson/src/main.mlo", "examples/ndjson/src/report.mlo",
    "examples/ndjson/tests/ndjson.mlo",
    "examples/json-cli/merlo.toml", "examples/json-cli/merlo.lock",
    "examples/json-cli/src/main.mlo", "examples/json-cli/tests/json_cli.mlo",
    "examples/grep/merlo.toml", "examples/grep/merlo.lock",
    "examples/grep/src/main.mlo", "examples/grep/src/search.mlo",
    "examples/grep/tests/grep.mlo",
    "examples/csv/merlo.toml", "examples/csv/merlo.lock",
    "examples/csv/src/main.mlo", "examples/csv/src/sales.mlo",
    "examples/csv/tests/csv.mlo",
    "examples/ffi/merlo.toml", "examples/ffi/merlo.lock",
    "examples/ffi/src/main.mlo", "examples/ffi/tests/ffi.mlo",
    "examples/capacity-ledger/merlo.toml", "examples/capacity-ledger/merlo.lock",
    "examples/capacity-ledger/src/main.mlo", "examples/capacity-ledger/src/decode.mlo",
    "examples/capacity-ledger/src/ledger.mlo",
    "examples/capacity-ledger/tests/capacity_ledger.mlo",
)


def _human_surface_paths() -> tuple[Path, ...]:
    roots = (
        ROOT / "examples",
        ROOT / "stdlib",
    )
    paths = {
        path
        for root in roots
        for path in root.rglob("*.mlo")
    }
    paths.update(
        path
        for path in (ROOT / "src" / "merlo" / "stdlib").glob("*.mlo")
    )
    paths.update(
        path
        for path in (ROOT / "src" / "merlo" / "programs").rglob("*.mlo")
        if "app" in path.parts
    )
    return tuple(sorted(paths))


def _config() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _project_paths(config: dict[str, object]) -> set[str]:
    setuptools = config["tool"]["setuptools"]  # type: ignore[index]
    data_files = setuptools["data-files"]  # type: ignore[index]
    return {
        str(path)
        for paths in data_files.values()  # type: ignore[union-attr]
        for path in paths
    }

def test_alpha_package_metadata_and_console_entrypoint() -> None:
    config = _config()
    project = config["project"]
    assert project["name"] == "merlo"
    assert project["version"] == "0.1.0a2"
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert project["scripts"]["merlo"] == "merlo.cli:main"
    assert config["tool"]["merlo"]["release"] == "0.1.0-alpha.2"


def test_package_data_declares_stdlib_editor_docs_specs_and_examples() -> None:
    config = _config()
    package_data = config["tool"]["setuptools"]["package-data"]
    assert "*.mlo" in package_data["merlo"][0]  # type: ignore[index]
    paths = _project_paths(config)
    assert "editors/vscode/syntaxes/merlo.tmLanguage.json" in paths
    assert set(DOC_PATHS) <= paths
    assert set(SPEC_PATHS) <= paths
    assert set(EXAMPLE_PATHS + RESEARCH_PATHS + RFC_PATHS) <= paths
    assert all((ROOT / path).is_file() for path in STDLIB_PATHS)
    assert (ROOT / "editors/vscode/syntaxes/merlo.tmLanguage.json").is_file()

def test_packaged_stdlib_matches_canonical_sources() -> None:
    config = _config()
    patterns = config["tool"]["setuptools"]["package-data"]["merlo"]  # type: ignore[index]
    assert "stdlib/std/*.mlo" in patterns
    for relative in STDLIB_PATHS[:14]:
        name = Path(relative).name
        canonical = ROOT / "stdlib" / "std" / name
        packaged = ROOT / "src" / "merlo" / "stdlib" / "std" / name
        assert packaged.read_bytes() == canonical.read_bytes(), name


def test_every_public_distribution_path_exists() -> None:
    required = STDLIB_PATHS + DOC_PATHS + SPEC_PATHS + RESEARCH_PATHS + RFC_PATHS + EXAMPLE_PATHS
    required += ("editors/vscode/package.json", "editors/vscode/syntaxes/merlo.tmLanguage.json")
    missing = [path for path in required if not (ROOT / path).is_file()]
    assert not missing, f"missing distribution paths: {missing}"


def test_shipped_human_sources_use_and_parse_as_surface_0_2() -> None:
    from merlo.surface_parser import parse_surface

    canonical_only = re.compile(
        r"^\s*(?:export\s+)?(?:fn|task|record)\b"
        r"|^\s*(?:let|var|uses)\b",
        flags=re.MULTILINE,
    )
    sources = _human_surface_paths()
    assert len(sources) == 45
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert canonical_only.search(source) is None, path
        parsed = parse_surface(
            source,
            path=path.relative_to(ROOT).as_posix(),
        )
        assert parsed.declarations, path

    grammar = json.loads(
        (ROOT / "editors/vscode/syntaxes/merlo.tmLanguage.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(grammar, sort_keys=True)
    for spelling in (
        "entity.name.function.merlo",
        "entity.name.type.record.merlo",
        "print",
        "or",
        r"\?",
    ):
        assert spelling in serialized


def test_dual_license_and_alpha_limitations_are_explicit() -> None:
    config = _config()
    assert config["project"]["license"] == "MIT OR Apache-2.0"
    mit = (ROOT / "LICENSE-MIT").read_text(encoding="utf-8")
    apache = (ROOT / "LICENSE-APACHE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Mansurshakh Japarov" in mit
    assert "Apache License" in apache
    assert "Version 2.0, January 2004" in apache
    public_text = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("README.md", "docs/limitations.md", "ROADMAP.md")
    ).casefold()
    for statement in (
        "linux x86-64", "c11 clang/gcc", "synchronous", "no cycle collector",
        "capturing closures", "async", "registry", "macros", "traits", "self-hosting",
        "one semantic core", "no future facets",
    ):
        assert statement in public_text


def test_readme_commands_are_real_production_parser_commands() -> None:
    from merlo.cli import build_parser

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = re.findall(r"^merlo (.+)$", readme, flags=re.MULTILINE)
    assert commands
    parser = build_parser()
    for command in commands:
        if command.startswith("--"):
            continue
        parsed = parser.parse_args(command.split())
        assert parsed.command != "historical"


def clean_wheel_hook(outdir: Path = ROOT / "dist") -> tuple[str, ...]:
    """Return the documented clean-wheel smoke command without running it."""
    return (sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir))


def venv_hook(venv: Path, wheel: Path) -> tuple[str, ...]:
    """Return the documented venv smoke command without running installation."""
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return (str(python), "-m", "pip", "install", "--no-deps", str(wheel))


def test_clean_wheel_and_venv_hooks_are_available_without_install_side_effects(tmp_path: Path) -> None:
    wheel = tmp_path / "merlo.whl"
    clean = clean_wheel_hook(tmp_path / "dist")
    install = venv_hook(tmp_path / "venv", wheel)
    assert clean[1:5] == ("-m", "build", "--wheel", "--outdir")
    assert install[1:5] == ("-m", "pip", "install", "--no-deps")
    assert "--no-deps" in install
    assert not wheel.exists()
