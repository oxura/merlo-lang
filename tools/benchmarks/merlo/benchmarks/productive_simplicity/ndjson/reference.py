"""Readable reference for the frozen NDJSON reporting algorithm."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

UINT64_MAX = (1 << 64) - 1


class ApplicationError(ValueError):
    """A typed application failure, distinct from an invalid record."""


@dataclass(frozen=True)
class Options:
    level: str | None = None
    service: str | None = None
    contains: str | None = None
    minimum_duration_ms: int | None = None


def checked_add(left: int, right: int, name: str) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX - right:
        raise ApplicationError(name)
    return left + right


def required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ApplicationError(f"InvalidField {name}")
    return value


def optional_uint64(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UINT64_MAX:
        raise ApplicationError(f"InvalidField {name}")
    return value


def text_lines(path: str | Path):
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


def analyze(path: str | Path, options: Options = Options()) -> str:
    minimum = options.minimum_duration_ms
    if minimum is not None and not 0 <= minimum <= UINT64_MAX:
        raise ApplicationError("InvalidMinimumDuration")

    total = valid = invalid = matching = 0
    duration_sum = duration_count = duration_max = 0
    by_level: dict[str, int] = {}
    by_service: dict[str, int] = {}

    for line in text_lines(path):
        total += 1
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ApplicationError("InvalidEvent")
            required_text(value.get("timestamp"), "timestamp")
            level = required_text(value.get("level"), "level")
            service = required_text(value.get("service"), "service")
            message = required_text(value.get("message"), "message")
            duration = optional_uint64(value.get("duration_ms"), "duration_ms")
        except (json.JSONDecodeError, ApplicationError):
            invalid += 1
            continue

        valid += 1
        if options.level is not None and level != options.level:
            continue
        if options.service is not None and service != options.service:
            continue
        if options.contains is not None and options.contains not in message:
            continue
        if minimum is not None and (duration is None or duration < minimum):
            continue

        matching += 1
        by_level[level] = checked_add(by_level.get(level, 0), 1, "CountOverflow")
        by_service[service] = checked_add(by_service.get(service, 0), 1, "CountOverflow")
        if duration is not None:
            duration_sum = checked_add(duration_sum, duration, "DurationOverflow")
            duration_count += 1
            duration_max = max(duration_max, duration)

    average = duration_sum // duration_count if duration_count else 0
    report = [
        f"total={total}",
        f"valid={valid}",
        f"invalid={invalid}",
        f"matching={matching}",
        f"duration_sum_ms={duration_sum}",
        f"duration_average_ms={average}",
        f"duration_max_ms={duration_max}",
    ]
    report.extend(f"level {name}={count}" for name, count in by_level.items())
    report.extend(f"service {name}={count}" for name, count in by_service.items())
    return "\n".join(report) + "\n"


def command_line(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--level")
    parser.add_argument("--service")
    parser.add_argument("--contains")
    parser.add_argument("--minimum-duration", type=int)
    parsed = parser.parse_args(arguments)
    try:
        sys.stdout.write(analyze(parsed.path, Options(parsed.level, parsed.service, parsed.contains, parsed.minimum_duration)))
    except ApplicationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(command_line(sys.argv[1:]))
