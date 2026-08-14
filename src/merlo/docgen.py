from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from merlo.semantic_world import SemanticWorld


@dataclass(frozen=True)
class Documentation:
    project: str
    digest: str
    markdown: str
    modules: int
    public_symbols: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "world_digest": self.digest,
            "modules": self.modules,
            "public_symbols": self.public_symbols,
            "markdown": self.markdown,
        }


def generate_documentation(world: SemanticWorld) -> Documentation:
    """Render public module interfaces directly from an exact semantic world."""

    modules = sorted(world.data.get("modules", ()), key=lambda item: item["name"])
    symbols = {
        item["symbol_id"]: item
        for item in world.data.get("symbols", ())
        if item.get("public", item.get("exported", False))
    }
    lines = [f"# {Path(world.root).name}", "", f"World: `{world.digest}`", ""]
    for module in modules:
        lines.extend((f"## {module['name']}", ""))
        module_symbols = [symbols[item] for item in module.get("symbols", ()) if item in symbols]
        if not module_symbols:
            lines.extend(("No public symbols.", ""))
            continue
        for symbol in sorted(module_symbols, key=lambda item: (item["name"], item["symbol_id"])):
            lines.append(f"### {symbol['name']}")
            lines.append("")
            lines.append(f"- Kind: `{symbol['kind']}`")
            lines.append(f"- Signature: `{symbol['signature']}`")
            lines.append(f"- Symbol ID: `{symbol['symbol_id']}`")
            if symbol.get("effects"):
                lines.append(f"- Effects: {', '.join(sorted(symbol['effects']))}")
            if symbol.get("capabilities"):
                lines.append(f"- Capabilities: {', '.join(sorted(symbol['capabilities']))}")
            lines.append("")
    markdown = "\n".join(lines).rstrip() + "\n"
    return Documentation(
        project=str(world.root),
        digest=world.digest,
        markdown=markdown,
        modules=len(modules),
        public_symbols=len(symbols),
    )


def write_documentation(world: SemanticWorld, destination: str | Path) -> Documentation:
    documentation = generate_documentation(world)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(documentation.markdown, encoding="utf-8")
    return documentation


# Short aliases are intentionally thin and keep one implementation.
def render_docs(world: SemanticWorld) -> str:
    return generate_documentation(world).markdown


def generate_docs(world: SemanticWorld) -> Documentation:
    return generate_documentation(world)


__all__ = ["Documentation", "generate_docs", "generate_documentation", "render_docs", "write_documentation"]
