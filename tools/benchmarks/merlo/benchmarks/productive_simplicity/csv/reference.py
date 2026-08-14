"""Readable reference for the frozen CSV aggregation algorithm."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

UINT64_MAX = (1 << 64) - 1


class ApplicationError(ValueError):
    """A typed application failure; malformed data rows are counted."""


def checked_add(left: int, right: int, name: str) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX - right:
        raise ApplicationError(name)
    return left + right


def checked_multiply(left: int, right: int, name: str) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX or right > UINT64_MAX:
        raise ApplicationError(name)
    if right and left > UINT64_MAX // right:
        raise ApplicationError(name)
    return left * right


def parse_uint64(raw: str, name: str) -> int:
    if not raw or not raw.isascii() or not raw.isdigit():
        raise ApplicationError(f"InvalidField {name}")
    value = int(raw, 10)
    if value > UINT64_MAX:
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


def one_record(line: str, delimiter: str) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=delimiter, strict=True))
    except (csv.Error, StopIteration) as exc:
        raise ApplicationError("InvalidRow") from exc


def aggregate(path: str | Path, delimiter: str = ",") -> str:
    if len(delimiter.encode("utf-8")) != 1:
        raise ApplicationError("InvalidDelimiter")
    lines = iter(text_lines(path))
    try:
        header_line = next(lines)
    except StopIteration as exc:
        raise ApplicationError("MissingHeader") from exc
    try:
        header = one_record(header_line, delimiter)
    except ApplicationError as exc:
        raise ApplicationError("InvalidHeader") from exc
    if header != ["date", "product", "region", "quantity", "unit_price_cents"]:
        raise ApplicationError("InvalidHeader")

    total = valid = invalid = quantity_total = revenue_total = 0
    by_product: dict[str, int] = {}
    by_region: dict[str, int] = {}
    for line in lines:
        total += 1
        try:
            fields = one_record(line, delimiter)
            if len(fields) != 5:
                raise ApplicationError("InvalidRow")
            date, product, region, quantity_raw, price_raw = fields
            if not date or not product or not region:
                raise ApplicationError("InvalidRow")
            quantity = parse_uint64(quantity_raw, "quantity")
            price = parse_uint64(price_raw, "unit_price_cents")
            revenue = checked_multiply(quantity, price, "RevenueOverflow")
            next_quantity = checked_add(quantity_total, quantity, "QuantityOverflow")
            next_revenue = checked_add(revenue_total, revenue, "RevenueOverflow")
        except ApplicationError as exc:
            if "Overflow" in str(exc):
                raise
            invalid += 1
            continue
        quantity_total = next_quantity
        revenue_total = next_revenue
        by_product[product] = checked_add(by_product.get(product, 0), revenue, "CountOverflow")
        by_region[region] = checked_add(by_region.get(region, 0), revenue, "CountOverflow")
        valid += 1

    report = [
        f"total={total}",
        f"valid={valid}",
        f"invalid={invalid}",
        f"quantity={quantity_total}",
        f"revenue_cents={revenue_total}",
    ]
    report.extend(f"product {name}={value}" for name, value in by_product.items())
    report.extend(f"region {name}={value}" for name, value in by_region.items())
    return "\n".join(report) + "\n"


def command_line(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--delimiter", default=",")
    parsed = parser.parse_args(arguments)
    try:
        sys.stdout.write(aggregate(parsed.path, parsed.delimiter))
    except ApplicationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(command_line(sys.argv[1:]))
