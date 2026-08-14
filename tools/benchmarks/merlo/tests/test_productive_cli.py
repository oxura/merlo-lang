from __future__ import annotations

from pathlib import PurePath

import pytest

from tools.benchmarks.merlo.productive_cli import (
    PRODUCTIVE_OPTION_SCHEMA,
    ProductiveCliError,
    ProductiveCliErrorFamily,
    ProductiveCliOptions,
    parse_productive_cli,
)


def test_productive_option_schema_has_the_checked_types() -> None:
    assert tuple((option.name, option.type_name) for option in PRODUCTIVE_OPTION_SCHEMA) == (
        ("level", "Text"),
        ("service", "Text"),
        ("contains", "Text"),
        ("column", "Text"),
        ("minimum-duration", "UInt64"),
        ("delimiter", "Byte"),
        ("ignore-case", "Bool"),
        ("count", "Bool"),
    )


def test_parse_productive_cli_accepts_every_option() -> None:
    assert parse_productive_cli(
        (
            "logs/events.csv",
            "--level",
            "warning",
            "--service",
            "checkout",
            "--contains",
            "timed out",
            "--column",
            "message",
            "--minimum-duration",
            "18446744073709551615",
            "--delimiter",
            ",",
            "--ignore-case",
            "--count",
        )
    ) == ProductiveCliOptions(
        path=PurePath("logs/events.csv"),
        level="warning",
        service="checkout",
        contains="timed out",
        column="message",
        minimum_duration=(1 << 64) - 1,
        delimiter=ord(","),
        ignore_case=True,
        count=True,
    )


def test_bool_flag_accepts_explicit_true_and_false() -> None:
    assert parse_productive_cli(("input.log", "--ignore-case", "true")).ignore_case
    assert not parse_productive_cli(
        ("input.log", "--ignore-case", "false")
    ).ignore_case
    assert parse_productive_cli(("input.log", "--count")).count


def test_options_may_precede_the_required_path() -> None:
    parsed = parse_productive_cli(
        ("--level", "info", "--ignore-case", "records.log")
    )
    assert parsed.path == PurePath("records.log")
    assert parsed.level == "info"
    assert parsed.ignore_case


@pytest.mark.parametrize(
    ("arguments", "index", "name", "family"),
    (
        (
            ("input.log", "--wat"),
            1,
            "wat",
            ProductiveCliErrorFamily.UNKNOWN,
        ),
        (
            ("input.log", "--level", "info", "--level", "debug"),
            3,
            "level",
            ProductiveCliErrorFamily.DUPLICATE,
        ),
        ((), 0, "path", ProductiveCliErrorFamily.MISSING),
        (
            ("input.log", "--service"),
            1,
            "service",
            ProductiveCliErrorFamily.MISSING,
        ),
        (
            ("input.log", "--minimum-duration", "-1"),
            2,
            "minimum-duration",
            ProductiveCliErrorFamily.MALFORMED,
        ),
        (
            ("input.log", "--delimiter", "é"),
            2,
            "delimiter",
            ProductiveCliErrorFamily.MALFORMED,
        ),
        (
            ("input.log", "--ignore-case", "sometimes"),
            2,
            "ignore-case",
            ProductiveCliErrorFamily.MALFORMED,
        ),
        (
            ("input.log", "--minimum-duration", "18446744073709551616"),
            2,
            "minimum-duration",
            ProductiveCliErrorFamily.OVERFLOW,
        ),
        (
            ("input.log", "--contains", "bad\udcfftext"),
            2,
            "contains",
            ProductiveCliErrorFamily.INVALID_TEXT,
        ),
        (
            ("first.log", "second.log"),
            1,
            "path",
            ProductiveCliErrorFamily.EXTRA_POSITIONAL,
        ),
    ),
)
def test_productive_cli_errors_are_typed(
    arguments: tuple[str, ...],
    index: int,
    name: str,
    family: ProductiveCliErrorFamily,
) -> None:
    with pytest.raises(ProductiveCliError) as raised:
        parse_productive_cli(arguments)

    assert raised.value.index == index
    assert raised.value.name == name
    assert raised.value.family is family


def test_supplied_invalid_values_never_fall_back_to_defaults() -> None:
    for arguments in (
        ("input.log", "--minimum-duration", "12ms"),
        ("input.log", "--delimiter", ""),
        ("input.log", "--delimiter", "::"),
        ("input.log", "--ignore-case", "1"),
        ("input.log", "--ignore-case", "0"),
        ("",),
        ("bad\x00path",),
    ):
        with pytest.raises(ProductiveCliError):
            parse_productive_cli(arguments)


def test_surrogate_in_path_or_option_name_is_rejected_as_invalid_text() -> None:
    for arguments, name in ((("bad\ud800path",), "path"), (("input", "--x\ud800"), "argument")):
        with pytest.raises(ProductiveCliError) as raised:
            parse_productive_cli(arguments)
        assert raised.value.family is ProductiveCliErrorFamily.INVALID_TEXT
        assert raised.value.name == name
