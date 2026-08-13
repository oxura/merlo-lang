from __future__ import annotations

from pathlib import Path

import pytest

from merlo.streaming_resources import (
    BorrowedLineExpiredError,
    FileOpenError,
    FileReaderClosedError,
    FileUtf8Error,
    open_read,
)


def collect_lines(path: Path, *, buffer_size: int = 8) -> list[str]:
    with open_read(path, buffer_size=buffer_size) as reader:
        return [line.text() for line in reader]


def test_empty_file_has_no_lines(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")

    assert collect_lines(path) == []


def test_terminated_and_unterminated_lines_are_exact(tmp_path: Path) -> None:
    terminated = tmp_path / "terminated.txt"
    terminated.write_bytes(b"only line\n")
    unterminated = tmp_path / "unterminated.txt"
    unterminated.write_bytes(b"first\nfinal")

    assert collect_lines(terminated) == ["only line"]
    assert collect_lines(unterminated, buffer_size=3) == ["first", "final"]


def test_unicode_is_decoded_across_incremental_buffer_fills(tmp_path: Path) -> None:
    path = tmp_path / "unicode.txt"
    path.write_bytes("κόσμος\n漢字😀\n".encode("utf-8"))

    assert collect_lines(path, buffer_size=2) == ["κόσμος", "漢字😀"]


def test_invalid_utf8_raises_typed_error_and_context_closes(tmp_path: Path) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"valid\ninvalid-\xff\n")
    reader = open_read(path, buffer_size=3)

    with pytest.raises(FileUtf8Error) as caught:
        with reader:
            first = reader.read_line()
            assert first is not None
            assert first.text() == "valid"
            invalid = reader.read_line()
            assert invalid is not None
            invalid.text()

    assert caught.value.line_number == 2
    assert caught.value.byte_offset == len(b"valid\ninvalid-")
    assert reader.close_count == 1
    assert reader.active_descriptor_count == 0


def test_line_view_expires_on_next_read_and_close(tmp_path: Path) -> None:
    path = tmp_path / "lines.txt"
    path.write_bytes(b"first\nsecond\n")
    reader = open_read(path)

    first = reader.read_line()
    assert first is not None
    assert first.text() == "first"
    second = reader.read_line()
    assert second is not None
    with pytest.raises(BorrowedLineExpiredError):
        _ = first.text()
    assert second.text() == "second"

    reader.close()
    with pytest.raises(BorrowedLineExpiredError):
        _ = second.text()


def test_early_iterator_exit_closes_owned_file(tmp_path: Path) -> None:
    path = tmp_path / "many.txt"
    path.write_bytes(b"one\ntwo\nthree\n")
    reader = open_read(path)

    with reader:
        for line in reader:
            assert line.text() == "one"
            break

    assert reader.open_count == 1
    assert reader.close_count == 1
    assert reader.active_descriptor_count == 0


def test_missing_path_raises_typed_open_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"

    with pytest.raises(FileOpenError) as caught:
        open_read(path)

    assert caught.value.path == path


def test_repeated_reads_reach_stable_eof_and_closed_reads_are_typed(tmp_path: Path) -> None:
    path = tmp_path / "single.txt"
    path.write_bytes(b"value")
    reader = open_read(path)

    line = reader.read_line()
    assert line is not None
    assert line.text() == "value"
    assert reader.read_line() is None
    assert reader.read_line() is None

    reader.close()
    with pytest.raises(FileReaderClosedError):
        reader.read_line()


def test_buffers_are_reused_and_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "buffered.txt"
    path.write_bytes(b"abcdefghij\nklmnop\n")
    reader = open_read(path, buffer_size=4)

    with reader:
        assert [line.text() for line in reader] == ["abcdefghij", "klmnop"]
        assert reader.buffer_fill_count >= 5
        assert reader.buffer_reuse_count == reader.buffer_fill_count - 1
        assert reader.active_descriptor_count == 1

    reader.close()
    assert reader.open_count == 1
    assert reader.close_count == 1
    assert reader.active_descriptor_count == 0
