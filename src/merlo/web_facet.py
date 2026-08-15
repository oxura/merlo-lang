"""Deterministic web facet assets for a Merlo WebAssembly artifact."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


class WebFacetError(ValueError):
    def __init__(self, message: str, code: str = "WebFacetError") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _canonical(value: Any) -> str:
    # JSON escapes also keep untrusted metadata inert if served as a script-adjacent asset.
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@dataclass(frozen=True)
class Route:
    path: str
    component: str
    methods: tuple[str, ...] = ("GET",)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "component": self.component, "methods": list(self.methods)}


@dataclass(frozen=True)
class Component:
    name: str
    properties: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "properties": dict(sorted(self.properties.items()))}


@dataclass(frozen=True)
class WebFacetManifest:
    name: str
    routes: tuple[Route, ...] = ()
    components: tuple[Component, ...] = ()
    capabilities: tuple[str, ...] = ()
    wasm_sha256: str = ""
    title: str = "Merlo application"
    schema: int = 1

    def __post_init__(self) -> None:
        if not self.name or self.schema != 1:
            raise WebFacetError("invalid facet manifest", "InvalidManifest")
        if self.wasm_sha256 and (len(self.wasm_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.wasm_sha256.lower())):
            raise WebFacetError("wasm_sha256 must be a SHA-256 digest", "InvalidManifest")
        object.__setattr__(self, "routes", tuple(sorted(self.routes, key=lambda item: (item.path, item.component))))
        object.__setattr__(self, "components", tuple(sorted(self.components, key=lambda item: item.name)))
        object.__setattr__(self, "capabilities", tuple(sorted(set(map(str, self.capabilities)))))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WebFacetManifest":
        routes = tuple(Route(str(item["path"]), str(item["component"]), tuple(map(str, item.get("methods", ("GET",))))) for item in raw.get("routes", ()) if isinstance(item, Mapping))
        components = tuple(Component(str(item["name"]), {str(k): str(v) for k, v in item.get("properties", {}).items()}) for item in raw.get("components", ()) if isinstance(item, Mapping))
        return cls(str(raw.get("name", "")), routes, components, tuple(map(str, raw.get("capabilities", ()))), str(raw.get("wasm_sha256", "")), str(raw.get("title", "Merlo application")), int(raw.get("schema", 1)))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "name": self.name, "title": self.title, "wasm_sha256": self.wasm_sha256, "routes": [route.to_dict() for route in self.routes], "components": [component.to_dict() for component in self.components], "capabilities": list(self.capabilities)}

    def to_json(self) -> str:
        return _canonical(self.to_dict()) + "\n"


@dataclass(frozen=True)
class WebBundle:
    files: Mapping[str, bytes]
    hashes: Mapping[str, str]
    wasm_sha256: str
    manifest_sha256: str
    capability_manifest: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.files, Mapping) or not isinstance(self.hashes, Mapping):
            raise WebFacetError("invalid bundle mappings", "InvalidBundle")
        files = dict(self.files)
        hashes = dict(self.hashes)
        if (
            any(not isinstance(name, str) or not isinstance(data, bytes) for name, data in files.items())
            or set(files) != set(hashes)
            or any(hashes[name] != _digest(data) for name, data in files.items())
        ):
            raise WebFacetError("bundle hashes do not match files", "InvalidBundle")
        object.__setattr__(self, "files", MappingProxyType(dict(sorted(files.items()))))
        object.__setattr__(self, "hashes", MappingProxyType(dict(sorted(hashes.items()))))
        object.__setattr__(self, "capability_manifest", tuple(sorted(set(self.capability_manifest))))

    @property
    def artifact_digest(self) -> str:
        return self.wasm_sha256

    @property
    def assets(self) -> Mapping[str, bytes]:
        return self.files

    def file(self, name: str) -> bytes:
        try: return self.files[name]
        except KeyError as exc: raise WebFacetError(name, "MissingAsset") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"files": dict(sorted(self.hashes.items())), "wasm_sha256": self.wasm_sha256, "manifest_sha256": self.manifest_sha256, "capabilities": list(self.capability_manifest)}


class WebBundler:
    """Bind route/component metadata to a wasm file without a UI framework."""
    def bundle(self, manifest: WebFacetManifest, artifact: Any, *, wasm_name: str = "module.wasm") -> WebBundle:
        wasm = getattr(artifact, "wasm", artifact)
        if not isinstance(wasm, bytes):
            raise WebFacetError("artifact must contain wasm bytes", "InvalidArtifact")
        digest = _digest(wasm)
        expected = manifest.wasm_sha256.lower() if manifest.wasm_sha256 else digest
        if digest != expected:
            raise WebFacetError("wasm artifact digest mismatch", "ArtifactTampered")
        if not wasm.startswith(b"\x00asm\x01\x00\x00\x00"):
            raise WebFacetError("wasm artifact has invalid magic/version", "ArtifactInvalid")
        if not isinstance(wasm_name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.wasm", wasm_name) is None:
            raise WebFacetError("wasm asset name is unsafe", "InvalidArtifact")
        # Publish the same digest-bound manifest consumed by the runtime.
        public = manifest.to_dict()
        public["wasm_sha256"] = digest
        manifest_bytes = (_canonical(public) + "\n").encode()
        manifest_digest = _digest(manifest_bytes)
        # Data is in an external JS asset and serialized as JSON, never interpolated
        # into HTML or executable source. ensure_ascii also neutralizes U+2028/U+2029.
        payload = _canonical(public).encode()
        wasm_integrity = "sha256-" + base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        js = (
            "// Merlo web facet; deterministic, framework-free runtime.\n"
            "const MERLO_FACET = " + payload.decode() + ";\n"
            "const MERLO_WASM_SHA256 = " + json.dumps(digest) + ";\n"
            "fetch(" + json.dumps(wasm_name)
            + ",{credentials:\"same-origin\",integrity:" + json.dumps(wasm_integrity) + "})"
            ".then(r=>{if(!r.ok)throw new Error(`WASM fetch failed: ${r.status}`);return r.arrayBuffer()})"
            ".then(bytes=>WebAssembly.instantiate(bytes,{}));\n"
        ).encode()
        js_digest = _digest(js)
        js_integrity = "sha256-" + base64.b64encode(bytes.fromhex(js_digest)).decode("ascii")
        csp = "default-src 'none'; base-uri 'none'; object-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; style-src 'self'"
        safe_title = html.escape(manifest.title, quote=True)
        html_bytes = (
            "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"" + html.escape(csp, quote=True)
            + "\"><title>" + safe_title + "</title></head><body><div id=\"app\"></div>"
            "<script src=\"app.js\" integrity=\"" + js_integrity
            + "\" crossorigin=\"anonymous\"></script></body></html>\n"
        ).encode()
        files = {wasm_name: wasm, "manifest.json": manifest_bytes, "app.js": js, "index.html": html_bytes}
        return WebBundle(files, {name: _digest(data) for name, data in files.items()}, digest, manifest_digest, manifest.capabilities)

    build = bundle


bundle_web = WebBundler().bundle
__all__ = ["Route", "Component", "WebFacetManifest", "WebBundle", "WebBundler", "WebFacetError", "bundle_web"]
