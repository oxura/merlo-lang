"""Stage 0.4E runtime binding soundness differential benchmark."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .analyzer import scan_python
from .frontend_evaluator import ReferenceEvaluator
from .frontend_semantics import compile_frontend
from .maximal_python import analyze_maximal_python, manifest_for_sources
from .python_binder import PythonBindingReport, bind_python_sources
from .stage04e_protocol import assert_stage04e_protocol


RUNTIME_SOUNDNESS_SCHEMA_VERSION = 2
RUNTIME_SOUNDNESS_REPETITIONS = 20
RUNTIME_SOUNDNESS_CATEGORIES = (
    "module_monkey_patch",
    "instance_monkey_patch",
    "class_monkey_patch",
    "decorator_replacement",
    "decorator_wrapper",
    "property",
    "descriptor",
    "getattr",
    "getattribute",
    "metaclass_injection",
    "subclass_override",
    "open_world_dispatch",
    "singledispatch",
    "functools_partial",
    "callback_variable",
    "dependency_injection",
    "runtime_reexport",
    "import_hook",
    "mutable_module_namespace",
    "post_import_replacement",
    "callable_object",
    "proxy_object",
    "dynamic_method_registration",
)

_OBSERVER_SOURCE = r'''from __future__ import annotations
import hashlib
import json
import os
import platform
import sys

_REGISTRY = {}
_EVENTS = []


def target(label):
    def register(function):
        _REGISTRY[function.__code__] = label
        return function
    return register


def register_callable(callable_value, label):
    function = getattr(callable_value, "__func__", callable_value)
    code = getattr(function, "__code__", None)
    if code is not None:
        _REGISTRY[code] = label
    return callable_value


def _code_digest(code):
    constants = []
    for value in code.co_consts:
        if value is None or isinstance(value, (bool, int, float, str, bytes)):
            constants.append(repr(value))
        elif isinstance(value, tuple):
            constants.append(repr(value))
        else:
            constants.append(type(value).__name__)
    payload = json.dumps(
        {
            "bytecode": code.co_code.hex(),
            "constants": constants,
            "name": code.co_name,
            "qualname": code.co_qualname,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _profile(frame, event, argument):
    if event != "call":
        return
    label = _REGISTRY.get(frame.f_code)
    if label is None:
        return
    _EVENTS.append(
        {
            "target": label,
            "module": frame.f_globals.get("__name__", ""),
            "qualname": frame.f_code.co_qualname,
            "filename": os.path.basename(frame.f_code.co_filename),
            "firstlineno": frame.f_code.co_firstlineno,
            "code_sha256": _code_digest(frame.f_code),
        }
    )


def observe(callable_value, repetitions):
    observations = []
    previous = sys.getprofile()
    sys.setprofile(_profile)
    try:
        for _ in range(repetitions):
            start = len(_EVENTS)
            callable_value()
            observations.append(list(_EVENTS[start:]))
    finally:
        sys.setprofile(previous)
    return {
        "observations": observations,
        "environment": {
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "hash_seed": os.environ.get("PYTHONHASHSEED", "unset"),
            "isolated_mode": True,
        },
    }
'''


@dataclass(frozen=True)
class RuntimeSoundnessFixture:
    id: str
    category: str
    sources: tuple[tuple[str, str], ...]
    entry_path: str
    call_path: str
    call_line: int
    call_spelling: str
    expected_static_target: str
    repetitions: int = RUNTIME_SOUNDNESS_REPETITIONS
    provenance: str = "generated-stage04e-runtime-template"
    author: str = "benchmark-maintainer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "entry_path": self.entry_path,
            "call_path": self.call_path,
            "call_line": self.call_line,
            "call_spelling": self.call_spelling,
            "expected_static_target": self.expected_static_target,
            "repetitions": self.repetitions,
            "provenance": self.provenance,
            "author": self.author,
            "source_sha256": hashlib.sha256(
                _canonical(dict(self.sources)).encode("utf-8")
            ).hexdigest(),
        }


@dataclass(frozen=True)
class StaticPrediction:
    arm: str
    classification: str
    predicted_target: str | None
    predicted_symbol_id: str | None
    provenance: str
    diagnostics: tuple[str, ...] = ()

    @property
    def exact(self) -> bool:
        return self.classification == "Exact" and self.predicted_target is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "classification": self.classification,
            "predicted_target": self.predicted_target,
            "predicted_symbol_id": self.predicted_symbol_id,
            "provenance": self.provenance,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class RuntimeCallsiteObservation:
    fixture_id: str
    category: str
    prediction: StaticPrediction
    expected_static_target: str
    runtime_observations: tuple[tuple[tuple[str, Any], ...], ...]
    environment: tuple[tuple[str, Any], ...]
    provenance: str

    @property
    def root_targets(self) -> tuple[str | None, ...]:
        roots: list[str | None] = []
        for observation in self.runtime_observations:
            events = dict(observation).get("events", ())
            roots.append(dict(events[0]).get("target") if events else None)
        return tuple(roots)

    @property
    def observed_target_set(self) -> tuple[str, ...]:
        return tuple(sorted({item for item in self.root_targets if item is not None}))

    @property
    def static_target_was_observed(self) -> bool:
        return (
            self.prediction.predicted_target is not None
            and self.prediction.predicted_target in self.observed_target_set
        )

    @property
    def unexpected_runtime_targets(self) -> tuple[str, ...]:
        predicted = self.prediction.predicted_target
        return tuple(
            item for item in self.observed_target_set if item != predicted
        )

    @property
    def unsound_exact_count(self) -> int:
        if not self.prediction.exact:
            return 0
        predicted = self.prediction.predicted_target
        return sum(item != predicted for item in self.root_targets)

    @property
    def observation_count(self) -> int:
        return len(self.runtime_observations)

    @property
    def sound_exact(self) -> bool | None:
        if not self.prediction.exact:
            return None
        return self.unsound_exact_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "category": self.category,
            "prediction": self.prediction.to_dict(),
            "expected_static_target": self.expected_static_target,
            "observation_count": self.observation_count,
            "observed_target_set": list(self.observed_target_set),
            "static_target_was_observed": self.static_target_was_observed,
            "unexpected_runtime_targets": list(self.unexpected_runtime_targets),
            "unsound_exact_count": self.unsound_exact_count,
            "sound_exact": self.sound_exact,
            "runtime_observations": [
                {key: value for key, value in observation}
                for observation in self.runtime_observations
            ],
            "environment": dict(self.environment),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class RuntimeSoundnessArmReport:
    arm: str
    observations: tuple[RuntimeCallsiteObservation, ...]

    @property
    def callsite_count(self) -> int:
        return len(self.observations)

    @property
    def runtime_observation_count(self) -> int:
        return sum(item.observation_count for item in self.observations)

    @property
    def static_exact_callsites(self) -> int:
        return sum(item.prediction.exact for item in self.observations)

    @property
    def static_exact_observations(self) -> int:
        return sum(
            item.observation_count
            for item in self.observations
            if item.prediction.exact
        )

    @property
    def unsound_exact_count(self) -> int:
        return sum(item.unsound_exact_count for item in self.observations)

    @property
    def unexpected_runtime_targets(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    target
                    for item in self.observations
                    for target in item.unexpected_runtime_targets
                }
            )
        )

    @property
    def observed_only_targets(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    target
                    for item in self.observations
                    for target in item.observed_target_set
                    if target != item.prediction.predicted_target
                }
            )
        )

    def to_dict(self) -> dict[str, Any]:
        rejected = sum(
            item.prediction.classification == "RejectedByProfile"
            for item in self.observations
        )
        dynamic = sum(
            item.prediction.classification == "DynamicBoundary"
            for item in self.observations
        )
        exact_denominator = self.static_exact_observations
        return {
            "arm": self.arm,
            "callsites": self.callsite_count,
            "runtime_observations": self.runtime_observation_count,
            "static_exact_callsites": self.static_exact_callsites,
            "static_exact_rate": _ratio(
                self.static_exact_callsites, self.callsite_count
            ),
            "runtime_target_coverage": _ratio(
                exact_denominator - self.unsound_exact_count,
                exact_denominator,
            ),
            "unsound_exact_count": self.unsound_exact_count,
            "unsound_exact_denominator": exact_denominator,
            "unsound_exact_rate": _ratio(
                self.unsound_exact_count, exact_denominator
            ),
            "dynamic_boundary_rate": _ratio(
                self.callsite_count - self.static_exact_callsites,
                self.callsite_count,
            ),
            "rejected_callsites": rejected,
            "rejection_rate": _ratio(rejected, self.callsite_count),
            "explicit_dynamic_callsites": dynamic,
            "explicit_dynamic_rate": _ratio(dynamic, self.callsite_count),
            "unexpected_runtime_targets": list(self.unexpected_runtime_targets),
            "observed_only_targets": list(self.observed_only_targets),
            "observations": [item.to_dict() for item in self.observations],
        }


@dataclass(frozen=True)
class RuntimeSoundnessReport:
    fixtures: tuple[RuntimeSoundnessFixture, ...]
    current_python: RuntimeSoundnessArmReport
    maximal_python: RuntimeSoundnessArmReport
    meldra: RuntimeSoundnessArmReport
    strong_python_diagnostic: RuntimeSoundnessArmReport
    protocol_sha256: str
    evidence_level: str = "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    schema_version: int = RUNTIME_SOUNDNESS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_level": self.evidence_level,
            "protocol_sha256": self.protocol_sha256,
            "statistical_units": {
                "generated_callsites": len(self.fixtures),
                "runtime_observations": sum(
                    item.repetitions for item in self.fixtures
                ),
                "independent_programs": 0,
                "independent_authors": 0,
                "template_families": len(self.fixtures),
                "primary_external_gate_status": "UNMEASURED",
                "note": "Repeated runtime calls exercise the harness but are not independent samples.",
            },
            "categories": list(RUNTIME_SOUNDNESS_CATEGORIES),
            "fixtures": [item.to_dict() for item in self.fixtures],
            "arms": {
                self.current_python.arm: self.current_python.to_dict(),
                self.maximal_python.arm: self.maximal_python.to_dict(),
                self.meldra.arm: self.meldra.to_dict(),
            },
            "diagnostic_baselines": {
                self.strong_python_diagnostic.arm: (
                    self.strong_python_diagnostic.to_dict()
                )
            },
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "authorized_next_stage": "EXTERNAL_RUNTIME_SOUNDNESS_CORPUS",
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _source(body: str) -> str:
    return textwrap.dedent(body).strip() + "\n"


def _entry(body: str) -> str:
    return (
        "import json\n"
        "import observer\n"
        "from observer import target\n\n"
        + textwrap.dedent(body).strip()
        + "\n\n"
        + 'if __name__ == "__main__":\n'
        + (
            "    payload = observer.observe("
            f"exercise, {RUNTIME_SOUNDNESS_REPETITIONS})\n"
        )
        + "    print(json.dumps(payload, sort_keys=True))\n"
    )


def _fixture(
    category: str,
    body: str,
    spelling: str,
    target: str,
    *,
    extra_sources: Mapping[str, str] | None = None,
) -> RuntimeSoundnessFixture:
    source = _entry(body)
    lines = source.splitlines()
    marker_index = lines.index("    # STAGE04E_CALLSITE")
    call_line = marker_index + 2
    sources = {"main.py": source, **dict(extra_sources or {})}
    return RuntimeSoundnessFixture(
        id=f"runtime:{category}",
        category=category,
        sources=tuple(sorted(sources.items())),
        entry_path="main.py",
        call_path="main.py",
        call_line=call_line,
        call_spelling=spelling,
        expected_static_target=target,
    )


def generate_runtime_soundness_fixtures() -> tuple[RuntimeSoundnessFixture, ...]:
    fixtures = (
        _fixture(
            "module_monkey_patch",
            '''
            @target("main.original")
            def original():
                return 1

            @target("main.replacement")
            def replacement():
                return 2

            original = replacement

            def exercise():
                # STAGE04E_CALLSITE
                original()
            ''',
            "original",
            "main.original",
        ),
        _fixture(
            "instance_monkey_patch",
            '''
            class User:
                @target("main.User.save")
                def save(self):
                    return 1

            @target("main.replacement")
            def replacement():
                return 2

            user: User = User()
            user.save = replacement

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "class_monkey_patch",
            '''
            class User:
                @target("main.User.save")
                def save(self):
                    return 1

            @target("main.replacement")
            def replacement(self):
                return 2

            User.save = replacement
            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "decorator_replacement",
            '''
            @target("main.replacement")
            def replacement():
                return 2

            def replace(function):
                return replacement

            @replace
            @target("main.compute")
            def compute():
                return 1

            def exercise():
                # STAGE04E_CALLSITE
                compute()
            ''',
            "compute",
            "main.compute",
        ),
        _fixture(
            "decorator_wrapper",
            '''
            def wrap(function):
                @target("main.compute.wrapper")
                def wrapper():
                    return function()
                return wrapper

            @wrap
            @target("main.compute")
            def compute():
                return 1

            def exercise():
                # STAGE04E_CALLSITE
                compute()
            ''',
            "compute",
            "main.compute",
        ),
        _fixture(
            "property",
            '''
            @target("main.replacement")
            def replacement():
                return 2

            class User:
                @target("main.User.save")
                def save(self):
                    return 1

            User.save = property(lambda self: replacement)
            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "descriptor",
            '''
            @target("main.replacement")
            def replacement():
                return 2

            class Redirect:
                def __get__(self, instance, owner):
                    return replacement

            class User:
                @target("main.User.save")
                def save(self):
                    return 1

            User.save = Redirect()
            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "getattr",
            '''
            @target("main.replacement")
            def replacement():
                return 2

            class User:
                @target("main.User.save")
                def save(self):
                    return 1

                def __getattr__(self, name):
                    if name == "save":
                        return replacement
                    raise AttributeError(name)

            del User.save
            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "getattribute",
            '''
            @target("main.replacement")
            def replacement():
                return 2

            class User:
                @target("main.User.save")
                def save(self):
                    return 1

                def __getattribute__(self, name):
                    if name == "save":
                        return replacement
                    return object.__getattribute__(self, name)

            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "metaclass_injection",
            '''
            @target("main.replacement")
            def replacement(self):
                return 2

            class ReplaceMeta(type):
                def __new__(meta, name, bases, namespace):
                    namespace["save"] = replacement
                    return super().__new__(meta, name, bases, namespace)

            class User(metaclass=ReplaceMeta):
                @target("main.User.save")
                def save(self):
                    return 1

            user: User = User()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.User.save",
        ),
        _fixture(
            "subclass_override",
            '''
            class Base:
                @target("main.Base.save")
                def save(self):
                    return 1

            class Plugin(Base):
                @target("main.Plugin.save")
                def save(self):
                    return 2

            user: Base = Plugin()

            def exercise():
                # STAGE04E_CALLSITE
                user.save()
            ''',
            "save",
            "main.Base.save",
        ),
        _fixture(
            "open_world_dispatch",
            '''
            class Base:
                @target("main.Base.run")
                def run(self):
                    return 1

            class ExternalPlugin(Base):
                @target("main.ExternalPlugin.run")
                def run(self):
                    return 2

            def invoke(service: Base):
                # STAGE04E_CALLSITE
                service.run()

            def exercise():
                invoke(ExternalPlugin())
            ''',
            "run",
            "main.Base.run",
        ),
        _fixture(
            "singledispatch",
            '''
            from functools import singledispatch

            @singledispatch
            @target("main.render")
            def render(value: object):
                return 1

            @render.register(int)
            @target("main.render_int")
            def render_int(value: int):
                return 2

            def exercise():
                # STAGE04E_CALLSITE
                render(1)
            ''',
            "render",
            "main.render",
        ),
        _fixture(
            "functools_partial",
            '''
            from functools import partial

            @target("main.execute")
            def execute(value: int = 1):
                return value

            @target("main.replacement")
            def replacement(value: int = 2):
                return value

            execute = partial(replacement, 2)

            def exercise():
                # STAGE04E_CALLSITE
                execute()
            ''',
            "execute",
            "main.execute",
        ),
        _fixture(
            "callback_variable",
            '''
            @target("main.original")
            def original():
                return 1

            @target("main.replacement")
            def replacement():
                return 2

            callback = original
            callback = replacement

            def exercise():
                # STAGE04E_CALLSITE
                callback()
            ''',
            "callback",
            "main.original",
        ),
        _fixture(
            "dependency_injection",
            '''
            class Service:
                @target("main.Service.run")
                def run(self):
                    return 1

            class Replacement:
                @target("main.Replacement.run")
                def run(self):
                    return 2

            def resolve() -> Service:
                return Replacement()

            service: Service = resolve()

            def exercise():
                # STAGE04E_CALLSITE
                service.run()
            ''',
            "run",
            "main.Service.run",
        ),
        _fixture(
            "runtime_reexport",
            '''
            import api

            @target("main.replacement")
            def replacement():
                return 2

            api.target = replacement

            def exercise():
                # STAGE04E_CALLSITE
                api.target()
            ''',
            "target",
            "api.target",
            extra_sources={
                "api.py": _source(
                    '''
                    from observer import target as observe_target

                    @observe_target("api.target")
                    def target():
                        return 1
                    '''
                )
            },
        ),
        _fixture(
            "import_hook",
            '''
            import importlib.abc
            import importlib.util
            import sys
            import types

            @target("main.replacement")
            def replacement():
                return 2

            class Loader(importlib.abc.Loader):
                def create_module(self, spec):
                    return types.ModuleType(spec.name)

                def exec_module(self, module):
                    module.target = replacement

            class Finder(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path, target=None):
                    if fullname == "provider":
                        return importlib.util.spec_from_loader(fullname, Loader())
                    return None

            sys.meta_path.insert(0, Finder())
            from provider import target as provider_target

            def exercise():
                # STAGE04E_CALLSITE
                provider_target()
            ''',
            "provider_target",
            "provider.target",
            extra_sources={
                "provider.py": _source(
                    '''
                    from observer import target as observe_target

                    @observe_target("provider.target")
                    def target():
                        return 1
                    '''
                )
            },
        ),
        _fixture(
            "mutable_module_namespace",
            '''
            import namespace

            @target("main.replacement")
            def replacement():
                return 2

            vars(namespace)["target"] = replacement

            def exercise():
                # STAGE04E_CALLSITE
                namespace.target()
            ''',
            "target",
            "namespace.target",
            extra_sources={
                "namespace.py": _source(
                    '''
                    from observer import target as observe_target

                    @observe_target("namespace.target")
                    def target():
                        return 1
                    '''
                )
            },
        ),
        _fixture(
            "post_import_replacement",
            '''
            import provider
            from provider import target as imported_target

            @target("main.replacement")
            def replacement():
                return 2

            provider.target = replacement
            imported_target = provider.target

            def exercise():
                # STAGE04E_CALLSITE
                imported_target()
            ''',
            "imported_target",
            "provider.target",
            extra_sources={
                "provider.py": _source(
                    '''
                    from observer import target as observe_target

                    @observe_target("provider.target")
                    def target():
                        return 1
                    '''
                )
            },
        ),
        _fixture(
            "callable_object",
            '''
            @target("main.original")
            def original():
                return 1

            class Replacement:
                @target("main.Replacement.__call__")
                def __call__(self):
                    return 2

            callback = original
            callback = Replacement()

            def exercise():
                # STAGE04E_CALLSITE
                callback()
            ''',
            "callback",
            "main.original",
        ),
        _fixture(
            "proxy_object",
            '''
            class Service:
                @target("main.Service.run")
                def run(self):
                    return 1

            class Replacement:
                @target("main.Replacement.run")
                def run(self):
                    return 2

            class Proxy:
                def __getattribute__(self, name):
                    if name == "run":
                        return Replacement().run
                    return object.__getattribute__(self, name)

            service: Service = Proxy()

            def exercise():
                # STAGE04E_CALLSITE
                service.run()
            ''',
            "run",
            "main.Service.run",
        ),
        _fixture(
            "dynamic_method_registration",
            '''
            class Service:
                @target("main.Service.run")
                def run(self):
                    return 1

            @target("main.replacement")
            def replacement(self):
                return 2

            registry = {"run": replacement}
            for name, implementation in registry.items():
                setattr(Service, name, implementation)

            service: Service = Service()

            def exercise():
                # STAGE04E_CALLSITE
                service.run()
            ''',
            "run",
            "main.Service.run",
        ),
    )
    categories = tuple(item.category for item in fixtures)
    if categories != RUNTIME_SOUNDNESS_CATEGORIES:
        raise AssertionError("runtime soundness fixture order does not match protocol")
    return fixtures


def _strong_prediction(
    fixture: RuntimeSoundnessFixture,
    report: PythonBindingReport,
) -> StaticPrediction:
    candidates = tuple(
        item
        for item in report.references
        if item.path == fixture.call_path
        and item.line == fixture.call_line
        and item.spelling == fixture.call_spelling
    )
    candidate = next(
        (item for item in candidates if item.status == "Exact"),
        candidates[0] if candidates else None,
    )
    target = (
        report.symbol(candidate.target_symbol_id)
        if candidate is not None and candidate.target_symbol_id is not None
        else None
    )
    classification = candidate.status if candidate is not None else "Missing"
    diagnostics = tuple(
        sorted(
            {
                item.code
                for item in report.diagnostics
                if item.path == fixture.call_path
            }
        )
    )
    return StaticPrediction(
        "strong-python-binder",
        classification,
        target.locator if target is not None else None,
        target.id if target is not None else None,
        "structural-type-aware-static-analysis",
        diagnostics,
    )


def _current_prediction(
    fixture: RuntimeSoundnessFixture,
    root: Path,
) -> StaticPrediction:
    program = scan_python(root)
    candidates = tuple(
        item
        for item in program.references
        if item.file == fixture.call_path
        and item.span.start.line == fixture.call_line
        and item.expected
        in {
            fixture.call_spelling,
            fixture.expected_static_target.rsplit(".", 1)[-1],
        }
    )
    candidate = next(
        (
            item
            for item in candidates
            if item.target_id is not None and item.resolution == "Exact"
        ),
        candidates[0] if candidates else None,
    )
    target = (
        program.entity(candidate.target_id)
        if candidate is not None and candidate.target_id is not None
        else None
    )
    return StaticPrediction(
        "current-python-sidecar",
        candidate.resolution if candidate is not None else "Missing",
        target.fqname if target is not None else None,
        target.id if target is not None else None,
        candidate.provenance if candidate is not None else "NoReference",
        (),
    )


def _normalize_runtime_observations(
    value: Iterable[Iterable[Mapping[str, Any]]],
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    result = []
    for events in value:
        canonical_events = tuple(
            tuple(sorted((str(key), item) for key, item in event.items()))
            for event in events
        )
        result.append(("events", canonical_events))
    return tuple((item,) for item in result)


def _run_python_fixture(
    fixture: RuntimeSoundnessFixture,
) -> tuple[StaticPrediction, StaticPrediction, tuple[tuple[tuple[str, Any], ...], ...], tuple[tuple[str, Any], ...]]:
    sources = dict(fixture.sources)
    strong = _strong_prediction(fixture, bind_python_sources(sources))
    with tempfile.TemporaryDirectory(prefix="meldra-runtime-soundness-") as temporary:
        root = Path(temporary)
        for relative_path, source in sources.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        (root / "observer.py").write_text(_OBSERVER_SOURCE, encoding="utf-8")
        current = _current_prediction(fixture, root)
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = "0"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import runpy,sys;"
                    "sys.path.insert(0,sys.argv[1]);"
                    "runpy.run_path(sys.argv[2],run_name='__main__')"
                ),
                str(root),
                str(root / fixture.entry_path),
            ],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"runtime fixture {fixture.id} failed with {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        lines = tuple(line for line in completed.stdout.splitlines() if line.strip())
        if not lines:
            raise RuntimeError(f"runtime fixture {fixture.id} emitted no observation")
        payload = json.loads(lines[-1])
    observations = _normalize_runtime_observations(payload["observations"])
    runtime_environment = tuple(sorted(payload["environment"].items()))
    return current, strong, observations, runtime_environment


def _python_arms(
    fixtures: tuple[RuntimeSoundnessFixture, ...],
) -> tuple[RuntimeSoundnessArmReport, RuntimeSoundnessArmReport]:
    current_observations = []
    strong_observations = []
    for fixture in fixtures:
        current, strong, runtime, environment = _run_python_fixture(fixture)
        current_observations.append(
            RuntimeCallsiteObservation(
                fixture.id,
                fixture.category,
                current,
                fixture.expected_static_target,
                runtime,
                environment,
                fixture.provenance,
            )
        )
        strong_observations.append(
            RuntimeCallsiteObservation(
                fixture.id,
                fixture.category,
                strong,
                fixture.expected_static_target,
                runtime,
                environment,
                fixture.provenance,
            )
        )
    return (
        RuntimeSoundnessArmReport(
            "current-python-sidecar", tuple(current_observations)
        ),
        RuntimeSoundnessArmReport(
            "strong-python-binder", tuple(strong_observations)
        ),
    )


def _maximal_python_arm(
    fixtures: tuple[RuntimeSoundnessFixture, ...],
    strong: RuntimeSoundnessArmReport,
) -> RuntimeSoundnessArmReport:
    observations = []
    for fixture, runtime in zip(fixtures, strong.observations):
        sources = dict(fixture.sources)
        report = analyze_maximal_python(
            sources, manifest_for_sources(sources)
        )
        candidates = tuple(
            item
            for item in report.references
            if item.path == fixture.call_path
            and item.line == fixture.call_line
            and item.spelling == fixture.call_spelling
        )
        candidate = candidates[0] if candidates else None
        prediction = StaticPrediction(
            "maximal-python-profile",
            (
                candidate.profile_status
                if candidate is not None
                else "Missing"
            ),
            candidate.target_locator if candidate is not None else None,
            candidate.target_symbol_id if candidate is not None else None,
            "strict-profile-over-strong-binder",
            tuple(
                sorted(
                    {
                        item.code
                        for item in report.diagnostics
                        if item.path in {
                            fixture.call_path,
                            "<manifest>",
                        }
                    }
                )
            ),
        )
        observations.append(
            RuntimeCallsiteObservation(
                f"strict:{fixture.category}",
                fixture.category,
                prediction,
                fixture.expected_static_target,
                runtime.runtime_observations,
                runtime.environment,
                "generated-maximal-python-profile-evaluation",
            )
        )
    return RuntimeSoundnessArmReport(
        "maximal-python-profile", tuple(observations)
    )


def _meldra_arm(
    fixtures: tuple[RuntimeSoundnessFixture, ...],
) -> RuntimeSoundnessArmReport:
    sources = {
        f"sound/{fixture.category}.meldra": _source(
            f'''
            package sound.{fixture.category}
            export original, exercise
            fn original() -> Int:
                1
            fn exercise() -> Int:
                original()
            '''
        )
        for fixture in fixtures
    }
    compilation = compile_frontend(sources)
    package_interfaces = {
        package: interface
        for package, interface, _ in compilation.hir.package_revisions
    }
    observations = []
    environment = tuple(
        sorted(
            {
                "evaluator": "frozen-stage04-reference-evaluator",
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "isolated_mode": True,
            }.items()
        )
    )
    for fixture in fixtures:
        target_locator = f"sound.{fixture.category}.original"
        exercise_locator = f"sound.{fixture.category}.exercise"
        target = compilation.hir.symbol(target_locator)
        exercise = compilation.hir.symbol(exercise_locator)
        reference = next(
            item
            for item in compilation.hir.references
            if item.owner_symbol_id == exercise.symbol_id
            and item.target_symbol_id == target.symbol_id
        )
        runtime_values = []
        for _ in range(fixture.repetitions):
            result = ReferenceEvaluator(compilation).evaluate(exercise_locator)
            executed = tuple(
                compilation.hir.symbol(symbol_id)
                for symbol_id in result.executed_symbol_ids
            )
            called = next(
                item for item in executed if item.symbol_id == target.symbol_id
            )
            event = {
                "target": called.locator,
                "symbol_id": called.symbol_id,
                "revision_id": called.revision_id,
                "interface_revision_id": package_interfaces[
                    called.package_name
                ],
                "provenance": "closed-evaluator-executed-symbol-trace",
            }
            runtime_values.append(
                (("events", (tuple(sorted(event.items())),)),)
            )
        prediction = StaticPrediction(
            "meldra-closed",
            reference.status,
            target.locator,
            target.symbol_id,
            "ClosedBinder",
            (),
        )
        observations.append(
            RuntimeCallsiteObservation(
                f"meldra:{fixture.category}",
                fixture.category,
                prediction,
                target_locator,
                tuple(runtime_values),
                environment,
                "frozen-stage04-closed-semantics",
            )
        )
    return RuntimeSoundnessArmReport("meldra-closed", tuple(observations))


def run_runtime_soundness_benchmark(
    root: str | Path = ".",
) -> RuntimeSoundnessReport:
    protocol = assert_stage04e_protocol(root)
    fixtures = generate_runtime_soundness_fixtures()
    current, strong = _python_arms(fixtures)
    maximal = _maximal_python_arm(fixtures, strong)
    meldra = _meldra_arm(fixtures)
    return RuntimeSoundnessReport(
        fixtures,
        current,
        maximal,
        meldra,
        strong,
        protocol.protocol_sha256,
    )


__all__ = [
    "RUNTIME_SOUNDNESS_CATEGORIES",
    "RUNTIME_SOUNDNESS_REPETITIONS",
    "RUNTIME_SOUNDNESS_SCHEMA_VERSION",
    "RuntimeCallsiteObservation",
    "RuntimeSoundnessArmReport",
    "RuntimeSoundnessFixture",
    "RuntimeSoundnessReport",
    "StaticPrediction",
    "generate_runtime_soundness_fixtures",
    "run_runtime_soundness_benchmark",
]
