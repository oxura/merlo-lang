from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compiler import compile_project
from .project import Project, resolve_dependencies


@dataclass(frozen=True)
class TestCaseResult:
    path: str
    status: str
    command: tuple[str, ...] = ()
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class TestReport:
    project: str
    tests: tuple[TestCaseResult, ...]

    @property
    def passed(self) -> int:
        return sum(item.status == "passed" for item in self.tests)

    @property
    def failed(self) -> int:
        return sum(item.status != "passed" for item in self.tests)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "count": len(self.tests),
            "passed": self.passed,
            "failed": self.failed,
            "ok": self.failed == 0,
            "tests": [item.to_dict() for item in self.tests],
        }


def run_project_tests(project: Project | str | Path) -> TestReport:
    """Compile and execute every production Merlo test source in a project.

    Test files use the same compiler and native runtime as the application.  A
    project with no test sources is a successful empty test run.
    """

    instance = project if isinstance(project, Project) else Project.load(project)
    resolve_dependencies(instance)
    tests_root = instance.tests_dir
    results: list[TestCaseResult] = []
    if tests_root.is_dir():
        paths = sorted(path for path in tests_root.rglob("*.mlo") if path.is_file())
    else:
        paths = []
    for path in paths:
        relative = path.relative_to(instance.root).as_posix()
        try:
            compilation = compile_project(
                path,
                emit_native=True,
                require_interface_lock=False,
            )
            if compilation.native is None or compilation.native.binary_path is None:
                raise RuntimeError("native build did not produce an executable")
            command = (compilation.native.binary_path, str(path))
            completed = subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                text=True,
            )
            results.append(
                TestCaseResult(
                    path=relative,
                    status="passed" if completed.returncode == 0 else "failed",
                    command=command,
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    digest=compilation.digest,
                )
            )
        except Exception as exc:
            results.append(
                TestCaseResult(
                    path=relative,
                    status="error",
                    stderr=f"{type(exc).__name__}: {exc}",
                )
            )
    return TestReport(project=str(instance.root), tests=tuple(results))


def run_tests(project: Project | str | Path) -> TestReport:
    return run_project_tests(project)


__all__ = ["TestCaseResult", "TestReport", "run_project_tests", "run_tests"]
