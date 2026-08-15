"""Typed surface elaboration building blocks.

The public coordinator remains :mod:`merlo.surface_elaborator`; this package
owns reusable constraint, call-binding, diagnostic, and state rules.
"""

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.elaboration.model import InferenceDecision, SurfaceElaboration

__all__ = [
    "InferenceDecision",
    "SurfaceElaboration",
    "SurfaceElaborationError",
]
