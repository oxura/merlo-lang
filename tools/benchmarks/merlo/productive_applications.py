from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from tools.benchmarks.merlo.deterministic_map import DeterministicTextUInt64Map, UINT64_MAX
from tools.benchmarks.merlo.streaming_resources import FileResourceError, FileUtf8Error, open_read
from tools.benchmarks.merlo.productive_cli import ProductiveCliError, parse_productive_cli


class ProductiveApplicationError(ValueError):
    pass


@dataclass(frozen=True)
class NdjsonOptions:
    level: str | None = None
    service: str | None = None
    contains: str | None = None
    minimum_duration_ms: int | None = None


@dataclass(frozen=True)
class NdjsonResult:
    total: int
    valid: int
    invalid: int
    matching: int
    duration_sum_ms: int
    duration_average_ms: int
    duration_max_ms: int
    by_level: tuple[tuple[str, int], ...]
    by_service: tuple[tuple[str, int], ...]
    report: str


@dataclass(frozen=True)
class CsvOptions:
    delimiter: str = ","


@dataclass(frozen=True)
class CsvResult:
    total: int
    valid: int
    invalid: int
    quantity: int
    revenue_cents: int
    revenue_by_product: tuple[tuple[str, int], ...]
    revenue_by_region: tuple[tuple[str, int], ...]
    report: str


@dataclass(frozen=True)
class GrepOptions:
    contains: str
    ignore_case: bool = False
    count_only: bool = False


@dataclass(frozen=True)
class GrepResult:
    total_lines: int
    matching_lines: int
    matches: tuple[tuple[int, str], ...]
    output: str


@dataclass(frozen=True)
class ProductiveCliRun:
    exit_code: int
    stdout: str
    stderr: str


def _checked_add(left: int, right: int, family: str) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX - right:
        raise ProductiveApplicationError(family)
    return left + right


def _checked_multiply(left: int, right: int, family: str) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX or right > UINT64_MAX:
        raise ProductiveApplicationError(family)
    if right != 0 and left > UINT64_MAX // right:
        raise ProductiveApplicationError(family)
    return left * right


def _line_text(view: object) -> str:
    try:
        text = getattr(view, "text")()
    except FileUtf8Error as exc:
        raise ProductiveApplicationError("InvalidUtf8") from exc
    if not isinstance(text, str):
        raise ProductiveApplicationError("InvalidLineView")
    return text


def _text_lines(path: str | Path):
    try:
        with open_read(path) as reader:
            while True:
                view = reader.read_line()
                if view is None:
                    return
                yield _line_text(view)
    except ProductiveApplicationError:
        raise
    except FileUtf8Error as exc:
        raise ProductiveApplicationError("InvalidUtf8") from exc
    except FileResourceError as exc:
        raise ProductiveApplicationError(f"ReadError {exc}") from exc


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ProductiveApplicationError(f"InvalidField {name}")
    return value


def _optional_uint64(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= UINT64_MAX:
        raise ProductiveApplicationError(f"InvalidField {name}")
    return value


def _increment(counts: DeterministicTextUInt64Map, key: str, amount: int = 1) -> None:
    try:
        counts.increment(key, amount)
    except OverflowError as exc:
        raise ProductiveApplicationError("CountOverflow") from exc


def analyze_ndjson(
    path: str | Path,
    options: NdjsonOptions = NdjsonOptions(),
) -> NdjsonResult:
    if options.minimum_duration_ms is not None and not 0 <= options.minimum_duration_ms <= UINT64_MAX:
        raise ProductiveApplicationError("InvalidMinimumDuration")
    total = 0
    valid = 0
    invalid = 0
    matching = 0
    duration_sum = 0
    duration_count = 0
    duration_max = 0
    levels = DeterministicTextUInt64Map()
    services = DeterministicTextUInt64Map()
    try:
        for line in _text_lines(path):
            total += 1
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ProductiveApplicationError("InvalidEvent")
                _required_text(value.get("timestamp"), "timestamp")
                level = _required_text(value.get("level"), "level")
                service = _required_text(value.get("service"), "service")
                message = _required_text(value.get("message"), "message")
                duration = _optional_uint64(value.get("duration_ms"), "duration_ms")
            except (json.JSONDecodeError, ProductiveApplicationError):
                invalid += 1
                continue
            valid += 1
            if options.level is not None and level != options.level:
                continue
            if options.service is not None and service != options.service:
                continue
            if options.contains is not None and options.contains not in message:
                continue
            if options.minimum_duration_ms is not None and (
                duration is None or duration < options.minimum_duration_ms
            ):
                continue
            matching += 1
            _increment(levels, level)
            _increment(services, service)
            if duration is not None:
                duration_sum = _checked_add(duration_sum, duration, "DurationOverflow")
                duration_count += 1
                duration_max = max(duration_max, duration)
        level_entries = levels.entries()
        service_entries = services.entries()
    finally:
        levels.close()
        services.close()
    average = duration_sum // duration_count if duration_count else 0
    lines = [
        f"total={total}",
        f"valid={valid}",
        f"invalid={invalid}",
        f"matching={matching}",
        f"duration_sum_ms={duration_sum}",
        f"duration_average_ms={average}",
        f"duration_max_ms={duration_max}",
    ]
    lines.extend(f"level {name}={count}" for name, count in level_entries)
    lines.extend(f"service {name}={count}" for name, count in service_entries)
    return NdjsonResult(
        total,
        valid,
        invalid,
        matching,
        duration_sum,
        average,
        duration_max,
        level_entries,
        service_entries,
        "\n".join(lines) + "\n",
    )


def _parse_uint64(raw: str, name: str) -> int:
    if not raw or not raw.isascii() or not raw.isdigit():
        raise ProductiveApplicationError(f"InvalidField {name}")
    value = int(raw, 10)
    if value > UINT64_MAX:
        raise ProductiveApplicationError(f"InvalidField {name}")
    return value


def aggregate_csv(
    path: str | Path,
    options: CsvOptions = CsvOptions(),
) -> CsvResult:
    if len(options.delimiter.encode("utf-8")) != 1:
        raise ProductiveApplicationError("InvalidDelimiter")
    iterator = iter(_text_lines(path))
    try:
        header_line = next(iterator)
    except StopIteration:
        return CsvResult(0, 0, 0, 0, 0, (), (), "total=0\nvalid=0\ninvalid=0\nquantity=0\nrevenue_cents=0\n")
    try:
        header = next(csv.reader([header_line], delimiter=options.delimiter, strict=True))
    except (csv.Error, StopIteration) as exc:
        raise ProductiveApplicationError("InvalidHeader") from exc
    expected_header = ["date", "product", "region", "quantity", "unit_price_cents"]
    if header != expected_header:
        raise ProductiveApplicationError("InvalidHeader")
    total = 0
    valid = 0
    invalid = 0
    quantity_total = 0
    revenue_total = 0
    products = DeterministicTextUInt64Map()
    regions = DeterministicTextUInt64Map()
    try:
        for line in iterator:
            total += 1
            try:
                row = next(csv.reader([line], delimiter=options.delimiter, strict=True))
                if len(row) != 5:
                    raise ProductiveApplicationError("InvalidRow")
                date, product, region, quantity_raw, price_raw = row
                if not date or not product or not region:
                    raise ProductiveApplicationError("InvalidRow")
                quantity = _parse_uint64(quantity_raw, "quantity")
                price = _parse_uint64(price_raw, "unit_price_cents")
                revenue = _checked_multiply(quantity, price, "RevenueOverflow")
                next_quantity = _checked_add(quantity_total, quantity, "QuantityOverflow")
                next_revenue = _checked_add(revenue_total, revenue, "RevenueOverflow")
            except (csv.Error, ProductiveApplicationError) as exc:
                if isinstance(exc, ProductiveApplicationError) and "Overflow" in str(exc):
                    raise
                invalid += 1
                continue
            quantity_total = next_quantity
            revenue_total = next_revenue
            _increment(products, product, revenue)
            _increment(regions, region, revenue)
            valid += 1
        product_entries = products.entries()
        region_entries = regions.entries()
    finally:
        products.close()
        regions.close()
    lines = [
        f"total={total}",
        f"valid={valid}",
        f"invalid={invalid}",
        f"quantity={quantity_total}",
        f"revenue_cents={revenue_total}",
    ]
    lines.extend(f"product {name}={value}" for name, value in product_entries)
    lines.extend(f"region {name}={value}" for name, value in region_entries)
    return CsvResult(
        total,
        valid,
        invalid,
        quantity_total,
        revenue_total,
        product_entries,
        region_entries,
        "\n".join(lines) + "\n",
    )


def _ascii_lower(value: str) -> str:
    return "".join(chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value)


def search_text(path: str | Path, options: GrepOptions) -> GrepResult:
    needle = _ascii_lower(options.contains) if options.ignore_case else options.contains
    matches = []
    total = 0
    for total, line in enumerate(_text_lines(path), 1):
        haystack = _ascii_lower(line) if options.ignore_case else line
        if needle in haystack:
            matches.append((total, line))
    if options.count_only:
        output = f"{len(matches)}\n"
    else:
        output = "".join(f"{line_number}:{line}\n" for line_number, line in matches)
    return GrepResult(total, len(matches), tuple(matches), output)


def _cli_error(exc: Exception, exit_code: int) -> ProductiveCliRun:
    return ProductiveCliRun(exit_code, "", f"{exc}\n")


def run_ndjson_cli(arguments: list[str]) -> ProductiveCliRun:
    try:
        parsed = parse_productive_cli(arguments)
        result = analyze_ndjson(
            parsed.path,
            NdjsonOptions(
                level=parsed.level,
                service=parsed.service,
                contains=parsed.contains,
                minimum_duration_ms=parsed.minimum_duration,
            ),
        )
    except ProductiveCliError as exc:
        return _cli_error(exc, 2)
    except ProductiveApplicationError as exc:
        return _cli_error(exc, 1)
    return ProductiveCliRun(0, result.report, "")


def run_csv_cli(arguments: list[str]) -> ProductiveCliRun:
    try:
        parsed = parse_productive_cli(arguments)
        delimiter = (
            ","
            if parsed.delimiter is None
            else bytes((parsed.delimiter,)).decode("ascii")
        )
        result = aggregate_csv(parsed.path, CsvOptions(delimiter=delimiter))
    except ProductiveCliError as exc:
        return _cli_error(exc, 2)
    except ProductiveApplicationError as exc:
        return _cli_error(exc, 1)
    return ProductiveCliRun(0, result.report, "")


def run_grep_cli(arguments: list[str]) -> ProductiveCliRun:
    try:
        parsed = parse_productive_cli(arguments)
        if parsed.contains is None:
            raise ProductiveApplicationError("MissingContains")
        result = search_text(
            parsed.path,
            GrepOptions(
                contains=parsed.contains,
                ignore_case=parsed.ignore_case,
                count_only=parsed.count,
            ),
        )
    except ProductiveCliError as exc:
        return _cli_error(exc, 2)
    except ProductiveApplicationError as exc:
        return _cli_error(exc, 1)
    return ProductiveCliRun(0, result.output, "")


__all__ = [
    "CsvOptions",
    "CsvResult",
    "GrepOptions",
    "GrepResult",
    "NdjsonOptions",
    "NdjsonResult",
    "ProductiveApplicationError",
    "ProductiveCliRun",
    "UINT64_MAX",
    "aggregate_csv",
    "analyze_ndjson",
    "search_text",
    "run_csv_cli",
    "run_grep_cli",
    "run_ndjson_cli",
]
