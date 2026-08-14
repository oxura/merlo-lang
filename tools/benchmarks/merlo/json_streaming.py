"""Streaming JSON token checksum runtime shared by Meldra evaluators and codegen.

The supported subset is JSON tokens with fixed-depth delimiter validation. It
streams directly into a checksum consumer and never constructs an AST.
"""

from __future__ import annotations

from dataclasses import dataclass


MASK64 = (1 << 64) - 1
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MAX_NESTING = 64
TOKEN_KINDS = {
    "object_start": 1,
    "object_end": 2,
    "array_start": 3,
    "array_end": 4,
    "colon": 5,
    "comma": 6,
    "null": 7,
    "true": 8,
    "false": 9,
    "integer": 10,
    "float": 11,
    "string": 12,
}


class JsonTokenError(ValueError):
    def __init__(self, kind: str, offset: int) -> None:
        super().__init__(f"{kind}: offset={offset}")
        self.kind = kind
        self.offset = offset


@dataclass
class JsonTokenStats:
    token_count: int = 0
    unescaped_strings: int = 0
    escaped_strings: int = 0
    text_builder_allocations: int = 0
    text_builder_reallocations: int = 0
    text_builder_frees: int = 0
    text_builder_growth_copied_bytes: int = 0
    text_builder_semantic_bytes: int = 0
    text_builder_finish_copies: int = 0
    semantic_output_bytes: int = 0


@dataclass(frozen=True)
class JsonTokenResult:
    checksum: int
    stats: JsonTokenStats


class _TextScratch:
    def __init__(self, stats: JsonTokenStats) -> None:
        self.data = bytearray()
        self.capacity = 0
        self.stats = stats

    def append(self, payload: bytes) -> None:
        if not payload:
            return
        required = len(self.data) + len(payload)
        if required > MASK64:
            raise JsonTokenError("TextBuilderLengthOverflow", 0)
        if required > self.capacity:
            doubled = self.capacity * 2
            if doubled > MASK64:
                raise JsonTokenError("TextBuilderCapacityOverflow", 0)
            new_capacity = max(required, max(8, doubled))
            if self.capacity == 0:
                self.stats.text_builder_allocations += 1
            else:
                self.stats.text_builder_reallocations += 1
                self.stats.text_builder_growth_copied_bytes += len(self.data)
                self.stats.text_builder_frees += 1
            self.capacity = new_capacity
        self.data.extend(payload)
        self.stats.text_builder_semantic_bytes += len(payload)

    def finish(self) -> bytes:
        return bytes(self.data)


def _mix(checksum: int, value: int) -> int:
    return ((checksum ^ (value & MASK64)) * FNV_PRIME) & MASK64


def strict_utf8_error(data: bytes) -> int | None:
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        return exc.start
    return None


def _hex_value(byte: int) -> int:
    if 48 <= byte <= 57:
        return byte - 48
    if 65 <= byte <= 70:
        return byte - 55
    if 97 <= byte <= 102:
        return byte - 87
    return -1


def _scalar_utf8(value: int, offset: int) -> bytes:
    if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        raise JsonTokenError("JsonInvalidUnicodeEscape", offset)
    return chr(value).encode("utf-8")


class _Scanner:
    _WS = {0x20, 0x09, 0x0A, 0x0D}
    _DELIMITERS = _WS | {ord("{"), ord("}"), ord("["), ord("]"), ord(":"), ord(",")}

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.length = len(data)
        self.index = 0
        self.checksum = FNV_OFFSET
        self.stats = JsonTokenStats()
        self.frames: list[list[str]] = []
        self.root_done = False

    def fail(self, kind: str, offset: int | None = None) -> None:
        raise JsonTokenError(kind, self.index if offset is None else offset)

    def emit(self, kind: str, start: int, end: int, payload: bytes = b"") -> None:
        self.checksum = _mix(self.checksum, TOKEN_KINDS[kind])
        self.checksum = _mix(self.checksum, start)
        self.checksum = _mix(self.checksum, end - start)
        self.checksum = _mix(self.checksum, len(payload))
        for byte in payload:
            self.checksum = _mix(self.checksum, byte)
        self.stats.token_count += 1
        self.stats.semantic_output_bytes += len(payload)

    def skip_ws(self) -> None:
        while self.index < self.length and self.data[self.index] in self._WS:
            self.index += 1

    def complete_value(self) -> None:
        if not self.frames:
            self.root_done = True
            return
        frame = self.frames[-1]
        if frame == ["array", "value_or_end"] or frame == ["array", "value"]:
            frame[1] = "comma_or_end"
        elif frame == ["object", "value"]:
            frame[1] = "comma_or_end"
        else:
            self.fail("JsonUnexpectedToken")

    def parse_string(self) -> None:
        start = self.index
        self.index += 1
        segment = self.index
        scratch: _TextScratch | None = None
        while self.index < self.length:
            byte = self.data[self.index]
            if byte == ord('"'):
                if scratch is None:
                    payload = self.data[segment:self.index]
                    self.stats.unescaped_strings += 1
                else:
                    scratch.append(self.data[segment:self.index])
                    payload = scratch.finish()
                    self.stats.escaped_strings += 1
                    self.stats.text_builder_frees += int(scratch.capacity > 0)
                self.index += 1
                self.emit("string", start, self.index, payload)
                return
            if byte < 0x20:
                self.fail("JsonInvalidStringControl", self.index)
            if byte != ord("\\"):
                self.index += 1
                continue
            if scratch is None:
                scratch = _TextScratch(self.stats)
            scratch.append(self.data[segment:self.index])
            self.index += 1
            if self.index >= self.length:
                self.fail("JsonTruncatedInput", self.index)
            escape_offset = self.index - 1
            escaped = self.data[self.index]
            simple = {
                ord('"'): b'"',
                ord("\\"): b"\\",
                ord("/"): b"/",
                ord("b"): b"\x08",
                ord("f"): b"\x0c",
                ord("n"): b"\x0a",
                ord("r"): b"\x0d",
                ord("t"): b"\x09",
            }
            if escaped in simple:
                scratch.append(simple[escaped])
                self.index += 1
                segment = self.index
                continue
            if escaped != ord("u"):
                self.fail("JsonInvalidEscape", escape_offset)
            if self.index + 4 >= self.length:
                self.fail("JsonInvalidUnicodeEscape", escape_offset)
            scalar = 0
            for position in range(self.index + 1, self.index + 5):
                digit = _hex_value(self.data[position])
                if digit < 0:
                    self.fail("JsonInvalidUnicodeEscape", escape_offset)
                scalar = scalar * 16 + digit
            self.index += 5
            if 0xD800 <= scalar <= 0xDBFF:
                if (
                    self.index + 5 >= self.length
                    or self.data[self.index] != ord("\\")
                    or self.data[self.index + 1] != ord("u")
                ):
                    self.fail("JsonInvalidUnicodeEscape", escape_offset)
                low = 0
                for position in range(self.index + 2, self.index + 6):
                    digit = _hex_value(self.data[position])
                    if digit < 0:
                        self.fail("JsonInvalidUnicodeEscape", escape_offset)
                    low = low * 16 + digit
                if not 0xDC00 <= low <= 0xDFFF:
                    self.fail("JsonInvalidUnicodeEscape", escape_offset)
                scalar = 0x10000 + ((scalar - 0xD800) << 10) + (low - 0xDC00)
                self.index += 6
            elif 0xDC00 <= scalar <= 0xDFFF:
                self.fail("JsonInvalidUnicodeEscape", escape_offset)
            scratch.append(_scalar_utf8(scalar, escape_offset))
            segment = self.index
        self.fail("JsonUnfinishedString", start)

    def parse_literal(self, literal: bytes, kind: str) -> None:
        start = self.index
        end = start + len(literal)
        if self.data[start:end] != literal:
            self.fail("JsonUnexpectedToken", start)
        if end < self.length and self.data[end] not in self._DELIMITERS:
            self.fail("JsonUnexpectedToken", start)
        self.index = end
        self.emit(kind, start, end, literal)

    def parse_number(self) -> None:
        start = self.index
        if self.data[self.index] == ord("-"):
            self.index += 1
            if self.index >= self.length:
                self.fail("JsonMalformedNumber", start)
        if self.index >= self.length or not 48 <= self.data[self.index] <= 57:
            self.fail("JsonMalformedNumber", start)
        if self.data[self.index] == ord("0"):
            self.index += 1
            if self.index < self.length and 48 <= self.data[self.index] <= 57:
                self.fail("JsonMalformedNumber", start)
        else:
            while self.index < self.length and 48 <= self.data[self.index] <= 57:
                self.index += 1
        is_float = False
        if self.index < self.length and self.data[self.index] == ord("."):
            is_float = True
            self.index += 1
            if self.index >= self.length or not 48 <= self.data[self.index] <= 57:
                self.fail("JsonMalformedNumber", start)
            while self.index < self.length and 48 <= self.data[self.index] <= 57:
                self.index += 1
        if self.index < self.length and self.data[self.index] in (ord("e"), ord("E")):
            is_float = True
            self.index += 1
            if self.index < self.length and self.data[self.index] in (ord("+"), ord("-")):
                self.index += 1
            if self.index >= self.length or not 48 <= self.data[self.index] <= 57:
                self.fail("JsonMalformedNumber", start)
            while self.index < self.length and 48 <= self.data[self.index] <= 57:
                self.index += 1
        if self.index < self.length and self.data[self.index] not in self._DELIMITERS:
            self.fail("JsonMalformedNumber", start)
        payload = self.data[start:self.index]
        self.emit("float" if is_float else "integer", start, self.index, payload)

    def parse_value(self) -> None:
        if self.index >= self.length:
            self.fail("JsonTruncatedInput", self.index)
        byte = self.data[self.index]
        if byte == ord("{"):
            start = self.index
            self.index += 1
            self.emit("object_start", start, self.index)
            if len(self.frames) >= MAX_NESTING:
                self.fail("JsonNestingDepthExceeded", start)
            self.frames.append(["object", "key_or_end"])
            return
        if byte == ord("["):
            start = self.index
            self.index += 1
            self.emit("array_start", start, self.index)
            if len(self.frames) >= MAX_NESTING:
                self.fail("JsonNestingDepthExceeded", start)
            self.frames.append(["array", "value_or_end"])
            return
        if byte == ord('"'):
            self.parse_string()
        elif byte == ord("n"):
            self.parse_literal(b"null", "null")
        elif byte == ord("t"):
            self.parse_literal(b"true", "true")
        elif byte == ord("f"):
            self.parse_literal(b"false", "false")
        elif byte == ord("-") or 48 <= byte <= 57:
            self.parse_number()
        elif byte in (ord("}"), ord("]")):
            self.fail("JsonDelimiterMismatch", self.index)
        else:
            self.fail("JsonUnexpectedToken", self.index)
        self.complete_value()

    def run(self) -> JsonTokenResult:
        utf8_error = strict_utf8_error(self.data)
        if utf8_error is not None:
            self.fail("JsonInvalidUtf8", utf8_error)
        while True:
            self.skip_ws()
            if self.root_done:
                if self.index != self.length:
                    self.fail("JsonUnexpectedToken", self.index)
                break
            if self.frames:
                kind, state = self.frames[-1]
                if kind == "array" and state == "comma_or_end":
                    if self.index >= self.length:
                        self.fail("JsonTruncatedInput", self.index)
                    if self.data[self.index] == ord("]"):
                        start = self.index
                        self.index += 1
                        self.emit("array_end", start, self.index)
                        self.frames.pop()
                        self.complete_value()
                    elif self.data[self.index] == ord(","):
                        start = self.index
                        self.index += 1
                        self.emit("comma", start, self.index)
                        self.frames[-1][1] = "value"
                    elif self.data[self.index] == ord("}"):
                        self.fail("JsonDelimiterMismatch", self.index)
                    else:
                        self.fail("JsonExpectedComma", self.index)
                    continue
                if kind == "object" and state == "comma_or_end":
                    if self.index >= self.length:
                        self.fail("JsonTruncatedInput", self.index)
                    if self.data[self.index] == ord("}"):
                        start = self.index
                        self.index += 1
                        self.emit("object_end", start, self.index)
                        self.frames.pop()
                        self.complete_value()
                    elif self.data[self.index] == ord(","):
                        start = self.index
                        self.index += 1
                        self.emit("comma", start, self.index)
                        self.frames[-1][1] = "key"
                    elif self.data[self.index] == ord("]"):
                        self.fail("JsonDelimiterMismatch", self.index)
                    else:
                        self.fail("JsonExpectedComma", self.index)
                    continue
                if kind == "object" and state in {"key_or_end", "key"}:
                    if self.index >= self.length:
                        self.fail("JsonTruncatedInput", self.index)
                    if state == "key_or_end" and self.data[self.index] == ord("}"):
                        start = self.index
                        self.index += 1
                        self.emit("object_end", start, self.index)
                        self.frames.pop()
                        self.complete_value()
                    elif self.data[self.index] == ord('"'):
                        self.parse_string()
                        self.frames[-1][1] = "colon"
                    elif self.data[self.index] in (ord("}"), ord("]")):
                        self.fail("JsonDelimiterMismatch", self.index)
                    else:
                        self.fail("JsonExpectedObjectKey", self.index)
                    continue
                if kind == "object" and state == "colon":
                    if self.index >= self.length:
                        self.fail("JsonTruncatedInput", self.index)
                    if self.data[self.index] != ord(":"):
                        self.fail("JsonExpectedColon", self.index)
                    start = self.index
                    self.index += 1
                    self.emit("colon", start, self.index)
                    self.frames[-1][1] = "value"
                    continue
                if kind == "array" and state == "value_or_end":
                    if self.index < self.length and self.data[self.index] == ord("]"):
                        start = self.index
                        self.index += 1
                        self.emit("array_end", start, self.index)
                        self.frames.pop()
                        self.complete_value()
                        continue
            if self.index >= self.length:
                self.fail("JsonTruncatedInput", self.index)
            self.parse_value()
        return JsonTokenResult(self.checksum, self.stats)


def tokenize_json(data: bytes | bytearray | memoryview) -> JsonTokenResult:
    return _Scanner(bytes(data)).run()


JSON_STREAMING_LIMITATIONS = {
    "ast": "not constructed",
    "maximum_nesting": MAX_NESTING,
    "numbers": "JSON integer and Float64 lexical forms; checksum consumes lexeme",
    "strings": "RFC 8259 escapes including paired UTF-16 surrogate escapes",
    "unicode": "UTF-8 validity and scalar escape decoding only",
    "normalization": "UNSUPPORTED_DECLARED",
    "grapheme_clusters": "UNSUPPORTED_DECLARED",
}


JSON_STREAMING_MIR_SCHEMA_VERSION = 1
JSON_STREAMING_MIR_CONTRACT = "meldra.json-streaming-mir.v1"


def json_streaming_mir_manifest(mir: object) -> dict[str, object]:
    events = [
        {
            "function": function.name,
            "block": block.id,
            "instruction_id": instruction.id,
            "op": instruction.op,
            "attributes": instruction.attribute_map,
            "source": (
                instruction.source.to_dict()
                if instruction.source is not None
                else None
            ),
        }
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "json_token_checksum"
    ]
    validation = {
        "operation_present": bool(events),
        "source_mappings_complete": bool(events)
        and all(event["source"] is not None for event in events),
        "streaming_explicit": bool(events)
        and all(
            event["attributes"].get("streaming") is True
            for event in events
        ),
        "ast_absent": bool(events)
        and all(
            event["attributes"].get("constructs_ast") is False
            for event in events
        ),
        "consumer_explicit": bool(events)
        and all(
            event["attributes"].get("consumer")
            == "deterministic_fnv1a64_v1"
            for event in events
        ),
    }
    return {
        "schema_version": JSON_STREAMING_MIR_SCHEMA_VERSION,
        "contract": JSON_STREAMING_MIR_CONTRACT,
        "source_sha256": mir.source_sha256,
        "events": events,
        "validation": validation,
    }


def validate_json_streaming_mir(mir: object) -> dict[str, bool]:
    return json_streaming_mir_manifest(mir)["validation"]


__all__ = [
    "FNV_OFFSET",
    "FNV_PRIME",
    "JSON_STREAMING_LIMITATIONS",
    "JSON_STREAMING_MIR_CONTRACT",
    "JSON_STREAMING_MIR_SCHEMA_VERSION",
    "JsonTokenError",
    "JsonTokenResult",
    "JsonTokenStats",
    "MAX_NESTING",
    "TOKEN_KINDS",
    "json_streaming_mir_manifest",
    "validate_json_streaming_mir",
    "strict_utf8_error",
    "tokenize_json",
]
