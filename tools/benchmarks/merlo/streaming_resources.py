from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Iterator


_DEFAULT_BUFFER_SIZE = 8192


class FileResourceError(Exception):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{type(self).__name__}: {path}: {reason}")


class FileOpenError(FileResourceError):
    pass


class FileReadError(FileResourceError):
    pass


class FileReaderClosedError(FileReadError):
    def __init__(self, path: Path) -> None:
        super().__init__(path, "reader is closed")


class FileUtf8Error(FileReadError):
    def __init__(
        self,
        path: Path,
        *,
        line_number: int,
        byte_offset: int,
        reason: str,
    ) -> None:
        self.line_number = line_number
        self.byte_offset = byte_offset
        super().__init__(
            path,
            f"invalid UTF-8 on line {line_number} at byte {byte_offset}: {reason}",
        )


class BorrowedLineExpiredError(RuntimeError):
    pass


class BorrowedLineView:
    __slots__ = ("_generation", "_owner")

    def __init__(self, owner: FileReader, generation: int) -> None:
        self._owner = owner
        self._generation = generation

    def text(self) -> str:
        return self._owner._borrowed_text(self._generation)

    def __str__(self) -> str:
        return self.text()

    def __len__(self) -> int:
        return len(self.text())


class FileReader(Iterator[BorrowedLineView]):
    def __init__(
        self,
        path: Path,
        stream: io.FileIO,
        *,
        buffer_size: int,
    ) -> None:
        self._path = path
        self._stream: io.FileIO | None = stream
        self._read_buffer = bytearray(buffer_size)
        self._read_position = 0
        self._read_limit = 0
        self._line_buffer = bytearray()
        self._current_text: str | None = None
        self._has_current_line = False
        self._current_line_start = 0
        self._generation = 0
        self._absolute_offset = 0
        self._line_number = 0
        self._at_eof = False
        self._open_count = 1
        self._close_count = 0
        self._buffer_fill_count = 0
        self._buffer_reuse_count = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def open_count(self) -> int:
        return self._open_count

    @property
    def close_count(self) -> int:
        return self._close_count

    @property
    def active_descriptor_count(self) -> int:
        return self._open_count - self._close_count

    @property
    def buffer_fill_count(self) -> int:
        return self._buffer_fill_count

    @property
    def buffer_reuse_count(self) -> int:
        return self._buffer_reuse_count

    def _require_stream(self) -> io.FileIO:
        if self._stream is None:
            raise FileReaderClosedError(self._path)
        return self._stream

    def _fill_buffer(self) -> bool:
        stream = self._require_stream()
        try:
            count = stream.readinto(self._read_buffer)
        except OSError as error:
            raise FileReadError(self._path, str(error)) from error
        if count is None:
            raise FileReadError(self._path, "binary read made no progress")
        self._buffer_fill_count += 1
        if self._buffer_fill_count > 1:
            self._buffer_reuse_count += 1
        self._read_position = 0
        self._read_limit = count
        return count != 0

    def _borrow_line(self, line_start: int) -> BorrowedLineView:
        self._line_number += 1
        self._current_line_start = line_start
        self._has_current_line = True
        return BorrowedLineView(self, self._generation)

    def _borrowed_text(self, generation: int) -> str:
        if generation != self._generation or not self._has_current_line:
            raise BorrowedLineExpiredError("borrowed line view is no longer valid")
        if self._current_text is not None:
            return self._current_text
        try:
            self._current_text = self._line_buffer.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise FileUtf8Error(
                self._path,
                line_number=self._line_number,
                byte_offset=self._current_line_start + error.start,
                reason=error.reason,
            ) from error
        return self._current_text

    def read_line(self) -> BorrowedLineView | None:
        self._require_stream()
        self._generation += 1
        self._current_text = None
        self._has_current_line = False
        self._line_buffer.clear()
        line_start = self._absolute_offset

        while True:
            if self._read_position == self._read_limit:
                if self._at_eof or not self._fill_buffer():
                    self._at_eof = True
                    if not self._line_buffer:
                        return None
                    return self._borrow_line(line_start)

            newline = self._read_buffer.find(
                b"\n",
                self._read_position,
                self._read_limit,
            )
            if newline >= 0:
                self._line_buffer.extend(
                    memoryview(self._read_buffer)[self._read_position:newline]
                )
                consumed = newline - self._read_position + 1
                self._read_position = newline + 1
                self._absolute_offset += consumed
                return self._borrow_line(line_start)

            self._line_buffer.extend(
                memoryview(self._read_buffer)[self._read_position:self._read_limit]
            )
            self._absolute_offset += self._read_limit - self._read_position
            self._read_position = self._read_limit

    def __iter__(self) -> FileReader:
        return self

    def __next__(self) -> BorrowedLineView:
        line = self.read_line()
        if line is None:
            raise StopIteration
        return line

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        self._generation += 1
        self._current_text = None
        self._has_current_line = False
        self._read_position = 0
        self._read_limit = 0
        self._line_buffer.clear()
        try:
            stream.close()
        except OSError as error:
            self._close_count += 1
            raise FileReadError(self._path, f"close failed: {error}") from error
        self._close_count += 1

    def __enter__(self) -> FileReader:
        self._require_stream()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_read(
    path: str | os.PathLike[str],
    *,
    buffer_size: int = _DEFAULT_BUFFER_SIZE,
) -> FileReader:
    if (
        not isinstance(buffer_size, int)
        or isinstance(buffer_size, bool)
        or buffer_size <= 0
    ):
        raise ValueError("buffer_size must be a positive integer")
    normalized = Path(path)
    try:
        stream = io.FileIO(os.fspath(normalized), mode="r")
    except OSError as error:
        raise FileOpenError(normalized, str(error)) from error
    return FileReader(normalized, stream, buffer_size=buffer_size)


__all__ = [
    "BorrowedLineExpiredError",
    "BorrowedLineView",
    "FileOpenError",
    "FileReadError",
    "FileReader",
    "FileReaderClosedError",
    "FileResourceError",
    "FileUtf8Error",
    "open_read",
]
