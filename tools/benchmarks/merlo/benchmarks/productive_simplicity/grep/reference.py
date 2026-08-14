"""Readable reference for the frozen grep-style text search algorithm."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator


class ApplicationError(ValueError):
    """A typed application failure."""


def text_lines(path: str | Path) -> Iterator[str]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as source:
            for raw in source:
                if raw.endswith("\n"):
                    raw = raw[:-1]
                    if raw.endswith("\r"):
                        raw = raw[:-1]
                elif raw.endswith("\r"):
                    raw = raw[:-1]
                yield raw
    except UnicodeDecodeError as exc:
        raise ApplicationError("InvalidUtf8") from exc
    except OSError as exc:
        raise ApplicationError(f"ReadError {exc}") from exc


def ascii_lower(value: str) -> str:
    return "".join(chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value)


def search(path: str | Path, contains: str, ignore_case: bool = False, count_only: bool = False) -> str:
    needle = ascii_lower(contains) if ignore_case else contains
    matches: list[tuple[int, str]] = []
    total = 0
    for total, line in enumerate(text_lines(path), 1):
        haystack = ascii_lower(line) if ignore_case else line
        if needle in haystack:
            matches.append((total, line))
    if count_only:
        return f"{len(matches)}\n"
    return "".join(f"{line_number}:{line}\n" for line_number, line in matches)


def command_line(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--contains", required=True)
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--count", action="store_true")
    parsed = parser.parse_args(arguments)
    try:
        sys.stdout.write(search(parsed.path, parsed.contains, parsed.ignore_case, parsed.count))
    except ApplicationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(command_line(sys.argv[1:]))
