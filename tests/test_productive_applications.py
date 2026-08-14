from __future__ import annotations

from pathlib import Path

import pytest

from merlo.productive_applications import (
    CsvOptions,
    GrepOptions,
    NdjsonOptions,
    ProductiveApplicationError,
    UINT64_MAX,
    aggregate_csv,
    analyze_ndjson,
    run_csv_cli,
    run_grep_cli,
    run_ndjson_cli,
    search_text,
)
from merlo.compiler import compile_project
from merlo.productive_cli import ProductiveCliOptions
from merlo.productive_source_projects import (
    PRODUCTIVE_PROJECTS,
    validate_productive_source_projects,
)



def test_ndjson_analyzer_filters_groups_and_reports_invalid_lines(tmp_path: Path):
    source = tmp_path / "events.ndjson"
    source.write_text(
        "\n".join(
            (
                '{"timestamp":"2026-08-13T00:00:00Z","level":"info","service":"api","message":"ready","duration_ms":10}',
                '{"timestamp":"2026-08-13T00:00:01Z","level":"error","service":"api","message":"request failed","duration_ms":30}',
                '{"timestamp":"2026-08-13T00:00:02Z","level":"error","service":"db","message":"query failed","duration_ms":50}',
                '{"timestamp":"2026-08-13T00:00:03Z","level":"error","service":"api","message":"no duration"}',
                '{"timestamp":7,"level":"error","service":"api","message":"bad"}',
                "not json",
            )
        ),
        encoding="utf-8",
    )

    result = analyze_ndjson(
        source,
        NdjsonOptions(level="error", contains="failed", minimum_duration_ms=20),
    )

    assert result.total == 6
    assert result.valid == 4
    assert result.invalid == 2
    assert result.matching == 2
    assert result.duration_sum_ms == 80
    assert result.duration_average_ms == 40
    assert result.duration_max_ms == 50
    assert result.by_level == (("error", 2),)
    assert result.by_service == (("api", 1), ("db", 1))
    assert result.report == (
        "total=6\nvalid=4\ninvalid=2\nmatching=2\n"
        "duration_sum_ms=80\nduration_average_ms=40\nduration_max_ms=50\n"
        "level error=2\nservice api=1\nservice db=1\n"
    )


def test_ndjson_analyzer_rejects_invalid_utf8(tmp_path: Path):
    source = tmp_path / "invalid.ndjson"
    source.write_bytes(b'{"level":"info"}\n\xff\n')

    with pytest.raises(ProductiveApplicationError, match="InvalidUtf8"):
        analyze_ndjson(source)


def test_csv_aggregator_supports_quotes_and_checked_revenue(tmp_path: Path):
    source = tmp_path / "sales.csv"
    source.write_text(
        "date,product,region,quantity,unit_price_cents\n"
        '2026-08-01,"Widget, large",north,2,125\n'
        "2026-08-02,Gadget,south,3,200\n"
        "bad,row\n"
        "2026-08-03,Gadget,south,nope,200\n",
        encoding="utf-8",
    )

    result = aggregate_csv(source, CsvOptions(delimiter=","))

    assert result.total == 4
    assert result.valid == 2
    assert result.invalid == 2
    assert result.quantity == 5
    assert result.revenue_cents == 850
    assert result.revenue_by_product == (("Widget, large", 250), ("Gadget", 600))
    assert result.revenue_by_region == (("north", 250), ("south", 600))
    assert "revenue_cents=850\n" in result.report


def test_csv_aggregator_detects_uint64_overflow(tmp_path: Path):
    source = tmp_path / "overflow.csv"
    source.write_text(
        "date,product,region,quantity,unit_price_cents\n"
        f"2026-08-01,Widget,north,{UINT64_MAX},2\n",
        encoding="utf-8",
    )

    with pytest.raises(ProductiveApplicationError, match="RevenueOverflow"):
        aggregate_csv(source)


def test_grep_search_exact_ascii_ignore_case_and_unterminated_line(tmp_path: Path):
    source = tmp_path / "input.txt"
    source.write_bytes("Alpha\nβeta\nALPHABET\nlast alpha".encode("utf-8"))

    result = search_text(source, GrepOptions(contains="alpha", ignore_case=True))

    assert result.total_lines == 4
    assert result.matching_lines == 3
    assert result.matches == ((1, "Alpha"), (3, "ALPHABET"), (4, "last alpha"))
    assert result.output == "1:Alpha\n3:ALPHABET\n4:last alpha\n"
    count = search_text(
        source,
        GrepOptions(contains="alpha", ignore_case=True, count_only=True),
    )
    assert count.output == "3\n"


def test_grep_search_rejects_invalid_utf8(tmp_path: Path):
    source = tmp_path / "invalid.txt"
    source.write_bytes(b"ok\n\xff\n")

    with pytest.raises(ProductiveApplicationError, match="InvalidUtf8"):
        search_text(source, GrepOptions(contains="ok"))



def test_application_option_types_flow_from_checked_cli_schema():
    parsed = ProductiveCliOptions(
        path=Path("input"),
        level="error",
        service="api",
        contains="failed",
        minimum_duration=20,
        delimiter=ord(";"),
        ignore_case=True,
        count=True,
    )

    assert NdjsonOptions(
        level=parsed.level,
        service=parsed.service,
        contains=parsed.contains,
        minimum_duration_ms=parsed.minimum_duration,
    ) == NdjsonOptions("error", "api", "failed", 20)
    assert CsvOptions(chr(parsed.delimiter or ord(","))) == CsvOptions(";")
    assert GrepOptions(
        parsed.contains or "",
        parsed.ignore_case,
        parsed.count,
    ) == GrepOptions("failed", True, True)


def test_productive_applications_have_multifile_merlo_source_projects():
    report = validate_productive_source_projects()

    assert report["passed"] is True
    assert set(PRODUCTIVE_PROJECTS) == {"csv", "grep", "ndjson"}
    assert all(item["module_count"] >= 2 for item in report["applications"])
    assert all(item["dynamic_any"] == 0 for item in report["applications"])
    assert all(item["manual_resource_operations"] == 0 for item in report["applications"])
    assert all(item["domain_opaque_c_helpers"] == [] for item in report["applications"])
    assert report["applications"][0]["reuses_general_json_parser"] is True

@pytest.mark.parametrize(
    ("application", "public_error_owner", "internal_error_prefix"),
    (
        ("ndjson", "app.report.AppError", "Merlo_app_report_"),
        ("csv", "app.sales.AppError", "Merlo_app_sales_"),
        ("grep", "app.main.AppError", "AppError"),
    ),
)
def test_productive_projects_compile_extracted_path_result_main(
    application: str,
    public_error_owner: str,
    internal_error_prefix: str,
):
    compilation = compile_project(
        PRODUCTIVE_PROJECTS[application][0],
        require_interface_lock=False,
    )

    entry = next(
        function for function in compilation.hir.functions
        if function.name == "main"
    )
    assert tuple(parameter.type_name for parameter in entry.parameters) == ("Path",)
    internal_error = entry.return_type.removeprefix("Result[Text,").removesuffix("]")
    if internal_error_prefix == "AppError":
        assert internal_error == internal_error_prefix
    else:
        assert internal_error.startswith(internal_error_prefix)
        assert internal_error.endswith("__AppError")
    task = next(
        item for item in compilation.elaborated.tasks
        if item.name == "main"
    )
    assert task.return_type == f"Result[Text,{public_error_owner}]"
    assert "task main(path: Path)" in compilation.elaborated.canonical_source
    assert (
        f"fn main(path: Path) -> Result_Text_{internal_error}_:"
        in compilation.elaborated.machine_source
    )

def test_productive_cli_entrypoints_execute_each_application(tmp_path: Path):
    ndjson = tmp_path / "events.ndjson"
    ndjson.write_text(
        '{"timestamp":"t","level":"error","service":"api","message":"failed","duration_ms":9}\n',
        encoding="utf-8",
    )
    csv_source = tmp_path / "sales.csv"
    csv_source.write_text(
        "date,product,region,quantity,unit_price_cents\n"
        "2026-08-01,Widget,north,2,125\n",
        encoding="utf-8",
    )
    text = tmp_path / "input.txt"
    text.write_text("Alpha\nbeta\n", encoding="utf-8")

    ndjson_run = run_ndjson_cli(
        [str(ndjson), "--level", "error", "--minimum-duration", "5"]
    )
    csv_run = run_csv_cli([str(csv_source), "--delimiter", ","])
    grep_run = run_grep_cli(
        [str(text), "--contains", "alpha", "--ignore-case"]
    )

    assert ndjson_run.exit_code == 0
    assert "matching=1\n" in ndjson_run.stdout
    assert csv_run.exit_code == 0
    assert "revenue_cents=250\n" in csv_run.stdout
    assert grep_run.exit_code == 0
    assert grep_run.stdout == "1:Alpha\n"
    grep_count_run = run_grep_cli(
        [str(text), "--contains", "alpha", "--ignore-case", "--count"]
    )
    assert grep_count_run.exit_code == 0
    assert grep_count_run.stdout == "1\n"
    assert ndjson_run.stderr == csv_run.stderr == grep_run.stderr == ""


def test_productive_cli_entrypoints_return_typed_argument_errors(tmp_path: Path):
    result = run_grep_cli([str(tmp_path / "input.txt"), "--contains"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "family=missing" in result.stderr
