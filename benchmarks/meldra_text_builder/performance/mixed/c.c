#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; } builder;
static uint64_t raw_allocations = 0;
static uint64_t raw_frees = 0;
static uint64_t raw_reallocations = 0;
static uint64_t raw_growth_copied = 0;
static uint64_t raw_required_append = 0;

static void fail(void) { abort(); }
static void grow(builder *b, uint64_t additional) {
    if (additional > UINT64_MAX - b->length) fail();
    uint64_t required = b->length + additional;
    if (required <= b->capacity) return;
    uint64_t capacity;
    if (b->capacity == 0) capacity = required > 8 ? required : 8;
    else {
        if (b->capacity > UINT64_MAX / 2) fail();
        uint64_t doubled = b->capacity * 2;
        capacity = required > doubled ? required : doubled;
    }
    if (capacity > UINT64_C(9223372036854775807)) fail();
    uint8_t *data = (uint8_t *)malloc((size_t)capacity);
    if (data == NULL) fail();
    ++raw_allocations;
    if (b->data != NULL) {
        if (b->length) memcpy(data, b->data, (size_t)b->length);
        raw_growth_copied += b->length;
        free(b->data);
        ++raw_frees;
        ++raw_reallocations;
    }
    b->data = data;
    b->capacity = capacity;
}
static void reserve_bytes(builder *b, uint64_t additional) { grow(b, additional); }
static void push_ascii(builder *b, uint64_t scalar) {
    if (scalar > 0x7F) fail();
    ++raw_required_append;
    grow(b, 1);
    b->data[b->length++] = (uint8_t)scalar;
}
static void push_scalar(builder *b, uint64_t scalar) {
    if (scalar > 0x10FFFF || (scalar >= 0xD800 && scalar <= 0xDFFF)) fail();
    uint64_t width = scalar <= 0x7F ? 1 : scalar <= 0x7FF ? 2 : scalar <= 0xFFFF ? 3 : 4;
    raw_required_append += width;
    grow(b, width);
    uint8_t *out = b->data + b->length;
    if (width == 1) out[0] = (uint8_t)scalar;
    else if (width == 2) { out[0] = (uint8_t)(0xC0 | (scalar >> 6)); out[1] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    else if (width == 3) { out[0] = (uint8_t)(0xE0 | (scalar >> 12)); out[1] = (uint8_t)(0x80 | ((scalar >> 6) & 0x3F)); out[2] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    else { out[0] = (uint8_t)(0xF0 | (scalar >> 18)); out[1] = (uint8_t)(0x80 | ((scalar >> 12) & 0x3F)); out[2] = (uint8_t)(0x80 | ((scalar >> 6) & 0x3F)); out[3] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    b->length += width;
}
static void extend_bytes(builder *b, const uint8_t *data, uint64_t length) {
    raw_required_append += length;
    grow(b, length);
    if (length) memcpy(b->data + b->length, data, (size_t)length);
    b->length += length;
}
static void destroy(builder *b) {
    if (b->data != NULL) { free(b->data); ++raw_frees; }
    b->data = NULL; b->length = 0; b->capacity = 0;
}
static uint64_t hex_digit(uint64_t value) { return value < 10 ? 48 + value : 87 + value; }
static builder json_encode(const uint8_t *data, uint64_t length) {
    builder out = { NULL, 0, 0 };
    reserve_bytes(&out, length + 2);
    push_ascii(&out, 34);
    uint64_t index = 0;
    while (index < length) {
        uint8_t byte = data[index];
        if (byte == 34 || byte == 92) { push_ascii(&out, 92); push_ascii(&out, byte); ++index; }
        else if (byte == 8) { push_ascii(&out, 92); push_ascii(&out, 98); ++index; }
        else if (byte == 12) { push_ascii(&out, 92); push_ascii(&out, 102); ++index; }
        else if (byte == 10) { push_ascii(&out, 92); push_ascii(&out, 110); ++index; }
        else if (byte == 13) { push_ascii(&out, 92); push_ascii(&out, 114); ++index; }
        else if (byte == 9) { push_ascii(&out, 92); push_ascii(&out, 116); ++index; }
        else if (byte < 32) { push_ascii(&out, 92); push_ascii(&out, 117); push_ascii(&out, 48); push_ascii(&out, 48); push_ascii(&out, hex_digit(byte >> 4)); push_ascii(&out, hex_digit(byte & 15)); ++index; }
        else {
            uint64_t width = byte < 0x80 ? 1 : byte < 0xE0 ? 2 : byte < 0xF0 ? 3 : 4;
            extend_bytes(&out, data + index, width);
            index += width;
        }
    }
    push_ascii(&out, 34);
    return out;
}
static void metrics(void) {
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0 RAW_REALLOCATIONS=%" PRIu64 " RAW_GROWTH_COPIED_BYTES=%" PRIu64 " RAW_REQUIRED_APPEND_BYTES=%" PRIu64 " RAW_FINISH_COPIES=0 RAW_UNEXPECTED_BYTES=0 RAW_VALIDATION_PASSES=0\n", raw_allocations, raw_frees, raw_reallocations, raw_growth_copied, raw_required_append);
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t repetitions = strtoull(argv[1], NULL, 10), checksum = 0;
    for (uint64_t i = 0; i < repetitions; ++i) {
        builder b = { NULL, 0, 10 };
        b.data = (uint8_t *)malloc(10); if (b.data == NULL) fail(); ++raw_allocations;
        push_ascii(&b, 65); push_scalar(&b, 2047); push_scalar(&b, 65535); push_scalar(&b, 1114111);
        checksum += b.length;
        destroy(&b);
    }
    metrics(); printf("%" PRIu64 "\n", checksum); return 0;
}
