"""C11 runtime fragment for the Meldra streaming JSON token consumer."""

from __future__ import annotations


def json_streaming_c_source() -> str:
    return r'''
static uint64_t meldra_json_token_count = 0;
static uint64_t meldra_json_unescaped_strings = 0;
static uint64_t meldra_json_escaped_strings = 0;
static uint64_t meldra_json_semantic_output_bytes = 0;

static void meldra_panic_json(const char *kind, uint64_t offset) {
    fprintf(stderr, "%s: offset=%" PRIu64 "\n", kind, offset);
    abort();
}

static uint64_t meldra_json_mix(uint64_t checksum, uint64_t value) {
    return (checksum ^ value) * UINT64_C(1099511628211);
}

static bool meldra_json_ws(uint8_t byte) {
    return byte == UINT8_C(0x20) || byte == UINT8_C(0x09) ||
           byte == UINT8_C(0x0A) || byte == UINT8_C(0x0D);
}

static bool meldra_json_delimiter(uint8_t byte) {
    return meldra_json_ws(byte) || byte == '{' || byte == '}' ||
           byte == '[' || byte == ']' || byte == ':' || byte == ',';
}

typedef struct {
    uint8_t *data;
    uint64_t length;
    uint64_t capacity;
} meldra_json_scratch;

typedef struct {
    const uint8_t *data;
    uint64_t length;
    uint64_t index;
    uint64_t checksum;
    uint8_t frame_kind[64];
    uint8_t frame_state[64];
    uint64_t depth;
    bool root_done;
} meldra_json_scanner;

enum {
    MELDRA_JSON_OBJECT = 1,
    MELDRA_JSON_ARRAY = 2,
    MELDRA_JSON_ARRAY_VALUE_OR_END = 0,
    MELDRA_JSON_ARRAY_VALUE = 1,
    MELDRA_JSON_ARRAY_COMMA_OR_END = 2,
    MELDRA_JSON_OBJECT_KEY_OR_END = 0,
    MELDRA_JSON_OBJECT_KEY = 1,
    MELDRA_JSON_OBJECT_COLON = 2,
    MELDRA_JSON_OBJECT_VALUE = 3,
    MELDRA_JSON_OBJECT_COMMA_OR_END = 4
};

static void meldra_json_emit(
    meldra_json_scanner *scanner,
    uint64_t kind,
    uint64_t start,
    uint64_t end,
    const uint8_t *payload,
    uint64_t payload_length
) {
    scanner->checksum = meldra_json_mix(scanner->checksum, kind);
    scanner->checksum = meldra_json_mix(scanner->checksum, start);
    scanner->checksum = meldra_json_mix(scanner->checksum, end - start);
    scanner->checksum = meldra_json_mix(scanner->checksum, payload_length);
    for (uint64_t i = 0; i < payload_length; ++i) {
        scanner->checksum = meldra_json_mix(scanner->checksum, payload[i]);
    }
    ++meldra_json_token_count;
    meldra_json_semantic_output_bytes += payload_length;
}

static void meldra_json_scratch_reserve(
    meldra_json_scratch *scratch,
    uint64_t additional,
    uint64_t offset
) {
    if (additional > UINT64_MAX - scratch->length) {
        meldra_panic_json("TextBuilderLengthOverflow", offset);
    }
    uint64_t required = scratch->length + additional;
    if (required <= scratch->capacity) return;
    uint64_t capacity;
    if (scratch->capacity == 0) {
        capacity = required > UINT64_C(8) ? required : UINT64_C(8);
    } else {
        if (scratch->capacity > UINT64_MAX / UINT64_C(2)) {
            meldra_panic_json("TextBuilderCapacityOverflow", offset);
        }
        uint64_t doubled = scratch->capacity * UINT64_C(2);
        capacity = required > doubled ? required : doubled;
    }
    if (capacity > (uint64_t)SIZE_MAX) {
        meldra_panic_json("TextBuilderAllocationSizeOverflow", offset);
    }
    uint8_t *replacement = (uint8_t *)malloc((size_t)capacity);
    if (replacement == NULL) meldra_panic_alloc();
    ++meldra_heap_allocations;
    meldra_allocated_bytes += capacity;
    if (scratch->data != NULL) {
        if (scratch->length != 0) {
            memcpy(replacement, scratch->data, (size_t)scratch->length);
            meldra_builder_growth_copied_bytes += scratch->length;
        }
        free(scratch->data);
        ++meldra_heap_frees;
        ++meldra_builder_reallocations;
    }
    scratch->data = replacement;
    scratch->capacity = capacity;
}

static void meldra_json_scratch_append(
    meldra_json_scratch *scratch,
    const uint8_t *payload,
    uint64_t length,
    uint64_t offset
) {
    if (length == 0) return;
    meldra_json_scratch_reserve(scratch, length, offset);
    memcpy(scratch->data + scratch->length, payload, (size_t)length);
    scratch->length += length;
    meldra_builder_extend_copied_bytes += length;
    meldra_text_builder_required_append_bytes += length;
}

static void meldra_json_scratch_scalar(
    meldra_json_scratch *scratch,
    uint64_t scalar,
    uint64_t offset
) {
    uint8_t encoded[4];
    uint64_t length;
    if (scalar > UINT64_C(0x10FFFF) ||
        (scalar >= UINT64_C(0xD800) && scalar <= UINT64_C(0xDFFF))) {
        meldra_panic_json("JsonInvalidUnicodeEscape", offset);
    }
    if (scalar <= UINT64_C(0x7F)) {
        encoded[0] = (uint8_t)scalar;
        length = 1;
    } else if (scalar <= UINT64_C(0x7FF)) {
        encoded[0] = (uint8_t)(UINT64_C(0xC0) | (scalar >> 6));
        encoded[1] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        length = 2;
    } else if (scalar <= UINT64_C(0xFFFF)) {
        encoded[0] = (uint8_t)(UINT64_C(0xE0) | (scalar >> 12));
        encoded[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
        encoded[2] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        length = 3;
    } else {
        encoded[0] = (uint8_t)(UINT64_C(0xF0) | (scalar >> 18));
        encoded[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & UINT64_C(0x3F)));
        encoded[2] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
        encoded[3] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        length = 4;
    }
    meldra_json_scratch_append(scratch, encoded, length, offset);
}

static int meldra_json_hex(uint8_t byte) {
    if (byte >= '0' && byte <= '9') return (int)(byte - '0');
    if (byte >= 'A' && byte <= 'F') return (int)(byte - 'A') + 10;
    if (byte >= 'a' && byte <= 'f') return (int)(byte - 'a') + 10;
    return -1;
}

static void meldra_json_complete_value(meldra_json_scanner *scanner) {
    if (scanner->depth == 0) {
        scanner->root_done = true;
        return;
    }
    uint64_t top = scanner->depth - 1;
    if (scanner->frame_kind[top] == MELDRA_JSON_ARRAY &&
        (scanner->frame_state[top] == MELDRA_JSON_ARRAY_VALUE_OR_END ||
         scanner->frame_state[top] == MELDRA_JSON_ARRAY_VALUE)) {
        scanner->frame_state[top] = MELDRA_JSON_ARRAY_COMMA_OR_END;
        return;
    }
    if (scanner->frame_kind[top] == MELDRA_JSON_OBJECT &&
        scanner->frame_state[top] == MELDRA_JSON_OBJECT_VALUE) {
        scanner->frame_state[top] = MELDRA_JSON_OBJECT_COMMA_OR_END;
        return;
    }
    meldra_panic_json("JsonUnexpectedToken", scanner->index);
}

static void meldra_json_parse_string(meldra_json_scanner *scanner) {
    uint64_t start = scanner->index++;
    uint64_t segment = scanner->index;
    meldra_json_scratch scratch = { NULL, 0, 0 };
    bool escaped_string = false;
    while (scanner->index < scanner->length) {
        uint8_t byte = scanner->data[scanner->index];
        if (byte == '"') {
            if (!escaped_string) {
                ++meldra_json_unescaped_strings;
                ++scanner->index;
                meldra_json_emit(
                    scanner, UINT64_C(12), start, scanner->index,
                    scanner->data + segment, scanner->index - segment - 1
                );
            } else {
                meldra_json_scratch_append(
                    &scratch, scanner->data + segment,
                    scanner->index - segment, scanner->index
                );
                ++meldra_json_escaped_strings;
                ++scanner->index;
                meldra_json_emit(
                    scanner, UINT64_C(12), start, scanner->index,
                    scratch.data, scratch.length
                );
                if (scratch.data != NULL) {
                    free(scratch.data);
                    ++meldra_heap_frees;
                }
            }
            return;
        }
        if (byte < UINT8_C(0x20)) {
            meldra_panic_json("JsonInvalidStringControl", scanner->index);
        }
        if (byte != '\\') {
            ++scanner->index;
            continue;
        }
        if (!escaped_string) {
            escaped_string = true;
        }
        meldra_json_scratch_append(
            &scratch, scanner->data + segment,
            scanner->index - segment, scanner->index
        );
        ++scanner->index;
        if (scanner->index >= scanner->length) {
            meldra_panic_json("JsonTruncatedInput", scanner->index);
        }
        uint64_t escape_offset = scanner->index - 1;
        uint8_t escaped = scanner->data[scanner->index];
        uint8_t decoded;
        bool simple = true;
        switch (escaped) {
            case '"': decoded = '"'; break;
            case '\\': decoded = '\\'; break;
            case '/': decoded = '/'; break;
            case 'b': decoded = UINT8_C(0x08); break;
            case 'f': decoded = UINT8_C(0x0C); break;
            case 'n': decoded = UINT8_C(0x0A); break;
            case 'r': decoded = UINT8_C(0x0D); break;
            case 't': decoded = UINT8_C(0x09); break;
            default: simple = false; decoded = 0; break;
        }
        if (simple) {
            meldra_json_scratch_append(&scratch, &decoded, 1, escape_offset);
            ++scanner->index;
            segment = scanner->index;
            continue;
        }
        if (escaped != 'u') {
            meldra_panic_json("JsonInvalidEscape", escape_offset);
        }
        if (scanner->index > scanner->length - 5) {
            meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
        }
        uint64_t scalar = 0;
        for (uint64_t position = scanner->index + 1;
             position < scanner->index + 5; ++position) {
            int digit = meldra_json_hex(scanner->data[position]);
            if (digit < 0) {
                meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
            }
            scalar = scalar * UINT64_C(16) + (uint64_t)digit;
        }
        scanner->index += 5;
        if (scalar >= UINT64_C(0xD800) && scalar <= UINT64_C(0xDBFF)) {
            if (scanner->index > scanner->length - 6 ||
                scanner->data[scanner->index] != '\\' ||
                scanner->data[scanner->index + 1] != 'u') {
                meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
            }
            uint64_t low = 0;
            for (uint64_t position = scanner->index + 2;
                 position < scanner->index + 6; ++position) {
                int digit = meldra_json_hex(scanner->data[position]);
                if (digit < 0) {
                    meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
                }
                low = low * UINT64_C(16) + (uint64_t)digit;
            }
            if (low < UINT64_C(0xDC00) || low > UINT64_C(0xDFFF)) {
                meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
            }
            scalar = UINT64_C(0x10000) +
                ((scalar - UINT64_C(0xD800)) << 10) +
                (low - UINT64_C(0xDC00));
            scanner->index += 6;
        } else if (scalar >= UINT64_C(0xDC00) && scalar <= UINT64_C(0xDFFF)) {
            meldra_panic_json("JsonInvalidUnicodeEscape", escape_offset);
        }
        meldra_json_scratch_scalar(&scratch, scalar, escape_offset);
        segment = scanner->index;
    }
    meldra_panic_json("JsonUnfinishedString", start);
}

static void meldra_json_parse_literal(
    meldra_json_scanner *scanner,
    const char *literal,
    uint64_t length,
    uint64_t kind
) {
    uint64_t start = scanner->index;
    if (length > scanner->length - start ||
        memcmp(scanner->data + start, literal, (size_t)length) != 0) {
        meldra_panic_json("JsonUnexpectedToken", start);
    }
    uint64_t end = start + length;
    if (end < scanner->length &&
        !meldra_json_delimiter(scanner->data[end])) {
        meldra_panic_json("JsonUnexpectedToken", start);
    }
    scanner->index = end;
    meldra_json_emit(
        scanner, kind, start, end,
        (const uint8_t *)literal, length
    );
}

static void meldra_json_parse_number(meldra_json_scanner *scanner) {
    uint64_t start = scanner->index;
    if (scanner->data[scanner->index] == '-') {
        ++scanner->index;
        if (scanner->index >= scanner->length) {
            meldra_panic_json("JsonMalformedNumber", start);
        }
    }
    if (scanner->index >= scanner->length ||
        scanner->data[scanner->index] < '0' ||
        scanner->data[scanner->index] > '9') {
        meldra_panic_json("JsonMalformedNumber", start);
    }
    if (scanner->data[scanner->index] == '0') {
        ++scanner->index;
        if (scanner->index < scanner->length &&
            scanner->data[scanner->index] >= '0' &&
            scanner->data[scanner->index] <= '9') {
            meldra_panic_json("JsonMalformedNumber", start);
        }
    } else {
        while (scanner->index < scanner->length &&
               scanner->data[scanner->index] >= '0' &&
               scanner->data[scanner->index] <= '9') {
            ++scanner->index;
        }
    }
    bool is_float = false;
    if (scanner->index < scanner->length &&
        scanner->data[scanner->index] == '.') {
        is_float = true;
        ++scanner->index;
        if (scanner->index >= scanner->length ||
            scanner->data[scanner->index] < '0' ||
            scanner->data[scanner->index] > '9') {
            meldra_panic_json("JsonMalformedNumber", start);
        }
        while (scanner->index < scanner->length &&
               scanner->data[scanner->index] >= '0' &&
               scanner->data[scanner->index] <= '9') {
            ++scanner->index;
        }
    }
    if (scanner->index < scanner->length &&
        (scanner->data[scanner->index] == 'e' ||
         scanner->data[scanner->index] == 'E')) {
        is_float = true;
        ++scanner->index;
        if (scanner->index < scanner->length &&
            (scanner->data[scanner->index] == '+' ||
             scanner->data[scanner->index] == '-')) {
            ++scanner->index;
        }
        if (scanner->index >= scanner->length ||
            scanner->data[scanner->index] < '0' ||
            scanner->data[scanner->index] > '9') {
            meldra_panic_json("JsonMalformedNumber", start);
        }
        while (scanner->index < scanner->length &&
               scanner->data[scanner->index] >= '0' &&
               scanner->data[scanner->index] <= '9') {
            ++scanner->index;
        }
    }
    if (scanner->index < scanner->length &&
        !meldra_json_delimiter(scanner->data[scanner->index])) {
        meldra_panic_json("JsonMalformedNumber", start);
    }
    meldra_json_emit(
        scanner, is_float ? UINT64_C(11) : UINT64_C(10),
        start, scanner->index, scanner->data + start,
        scanner->index - start
    );
}

static void meldra_json_parse_value(meldra_json_scanner *scanner) {
    if (scanner->index >= scanner->length) {
        meldra_panic_json("JsonTruncatedInput", scanner->index);
    }
    uint64_t start = scanner->index;
    uint8_t byte = scanner->data[scanner->index];
    if (byte == '{' || byte == '[') {
        ++scanner->index;
        meldra_json_emit(
            scanner, byte == '{' ? UINT64_C(1) : UINT64_C(3),
            start, scanner->index, NULL, 0
        );
        if (scanner->depth >= UINT64_C(64)) {
            meldra_panic_json("JsonNestingDepthExceeded", start);
        }
        scanner->frame_kind[scanner->depth] =
            byte == '{' ? MELDRA_JSON_OBJECT : MELDRA_JSON_ARRAY;
        scanner->frame_state[scanner->depth] = 0;
        ++scanner->depth;
        return;
    }
    if (byte == '"') {
        meldra_json_parse_string(scanner);
    } else if (byte == 'n') {
        meldra_json_parse_literal(scanner, "null", 4, UINT64_C(7));
    } else if (byte == 't') {
        meldra_json_parse_literal(scanner, "true", 4, UINT64_C(8));
    } else if (byte == 'f') {
        meldra_json_parse_literal(scanner, "false", 5, UINT64_C(9));
    } else if (byte == '-' || (byte >= '0' && byte <= '9')) {
        meldra_json_parse_number(scanner);
    } else if (byte == '}' || byte == ']') {
        meldra_panic_json("JsonDelimiterMismatch", scanner->index);
    } else {
        meldra_panic_json("JsonUnexpectedToken", scanner->index);
    }
    meldra_json_complete_value(scanner);
}

static uint64_t meldra_json_token_checksum(
    const uint8_t *data,
    uint64_t length
) {
    uint64_t utf8_error = 0;
    if (!meldra_utf8_validate(data, length, &utf8_error)) {
        meldra_panic_json("JsonInvalidUtf8", utf8_error);
    }
    meldra_json_scanner scanner;
    memset(&scanner, 0, sizeof(scanner));
    scanner.data = data;
    scanner.length = length;
    scanner.checksum = UINT64_C(1469598103934665603);
    for (;;) {
        while (scanner.index < scanner.length &&
               meldra_json_ws(scanner.data[scanner.index])) {
            ++scanner.index;
        }
        if (scanner.root_done) {
            if (scanner.index != scanner.length) {
                meldra_panic_json("JsonUnexpectedToken", scanner.index);
            }
            return scanner.checksum;
        }
        if (scanner.depth != 0) {
            uint64_t top = scanner.depth - 1;
            uint8_t kind = scanner.frame_kind[top];
            uint8_t state = scanner.frame_state[top];
            if (kind == MELDRA_JSON_ARRAY &&
                state == MELDRA_JSON_ARRAY_COMMA_OR_END) {
                if (scanner.index >= scanner.length) {
                    meldra_panic_json("JsonTruncatedInput", scanner.index);
                }
                if (scanner.data[scanner.index] == ']') {
                    uint64_t start = scanner.index++;
                    meldra_json_emit(
                        &scanner, UINT64_C(4), start, scanner.index,
                        NULL, 0
                    );
                    --scanner.depth;
                    meldra_json_complete_value(&scanner);
                } else if (scanner.data[scanner.index] == ',') {
                    uint64_t start = scanner.index++;
                    meldra_json_emit(
                        &scanner, UINT64_C(6), start, scanner.index,
                        NULL, 0
                    );
                    scanner.frame_state[top] = MELDRA_JSON_ARRAY_VALUE;
                } else if (scanner.data[scanner.index] == '}') {
                    meldra_panic_json("JsonDelimiterMismatch", scanner.index);
                } else {
                    meldra_panic_json("JsonExpectedComma", scanner.index);
                }
                continue;
            }
            if (kind == MELDRA_JSON_OBJECT &&
                state == MELDRA_JSON_OBJECT_COMMA_OR_END) {
                if (scanner.index >= scanner.length) {
                    meldra_panic_json("JsonTruncatedInput", scanner.index);
                }
                if (scanner.data[scanner.index] == '}') {
                    uint64_t start = scanner.index++;
                    meldra_json_emit(
                        &scanner, UINT64_C(2), start, scanner.index,
                        NULL, 0
                    );
                    --scanner.depth;
                    meldra_json_complete_value(&scanner);
                } else if (scanner.data[scanner.index] == ',') {
                    uint64_t start = scanner.index++;
                    meldra_json_emit(
                        &scanner, UINT64_C(6), start, scanner.index,
                        NULL, 0
                    );
                    scanner.frame_state[top] = MELDRA_JSON_OBJECT_KEY;
                } else if (scanner.data[scanner.index] == ']') {
                    meldra_panic_json("JsonDelimiterMismatch", scanner.index);
                } else {
                    meldra_panic_json("JsonExpectedComma", scanner.index);
                }
                continue;
            }
            if (kind == MELDRA_JSON_OBJECT &&
                (state == MELDRA_JSON_OBJECT_KEY_OR_END ||
                 state == MELDRA_JSON_OBJECT_KEY)) {
                if (scanner.index >= scanner.length) {
                    meldra_panic_json("JsonTruncatedInput", scanner.index);
                }
                if (state == MELDRA_JSON_OBJECT_KEY_OR_END &&
                    scanner.data[scanner.index] == '}') {
                    uint64_t start = scanner.index++;
                    meldra_json_emit(
                        &scanner, UINT64_C(2), start, scanner.index,
                        NULL, 0
                    );
                    --scanner.depth;
                    meldra_json_complete_value(&scanner);
                } else if (scanner.data[scanner.index] == '"') {
                    meldra_json_parse_string(&scanner);
                    scanner.frame_state[top] = MELDRA_JSON_OBJECT_COLON;
                } else if (scanner.data[scanner.index] == '}' ||
                           scanner.data[scanner.index] == ']') {
                    meldra_panic_json("JsonDelimiterMismatch", scanner.index);
                } else {
                    meldra_panic_json("JsonExpectedObjectKey", scanner.index);
                }
                continue;
            }
            if (kind == MELDRA_JSON_OBJECT &&
                state == MELDRA_JSON_OBJECT_COLON) {
                if (scanner.index >= scanner.length) {
                    meldra_panic_json("JsonTruncatedInput", scanner.index);
                }
                if (scanner.data[scanner.index] != ':') {
                    meldra_panic_json("JsonExpectedColon", scanner.index);
                }
                uint64_t start = scanner.index++;
                meldra_json_emit(
                    &scanner, UINT64_C(5), start, scanner.index,
                    NULL, 0
                );
                scanner.frame_state[top] = MELDRA_JSON_OBJECT_VALUE;
                continue;
            }
            if (kind == MELDRA_JSON_ARRAY &&
                state == MELDRA_JSON_ARRAY_VALUE_OR_END &&
                scanner.index < scanner.length &&
                scanner.data[scanner.index] == ']') {
                uint64_t start = scanner.index++;
                meldra_json_emit(
                    &scanner, UINT64_C(4), start, scanner.index,
                    NULL, 0
                );
                --scanner.depth;
                meldra_json_complete_value(&scanner);
                continue;
            }
        }
        if (scanner.index >= scanner.length) {
            meldra_panic_json("JsonTruncatedInput", scanner.index);
        }
        meldra_json_parse_value(&scanner);
    }
}
'''.strip()


__all__ = ["json_streaming_c_source"]
