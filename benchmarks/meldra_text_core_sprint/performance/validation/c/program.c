#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint8_t *data;
    uint64_t length;
    uint64_t capacity;
    uint8_t state;
} raw_builder;

static uint64_t raw_allocations = 0;
static uint64_t raw_frees = 0;

static raw_builder builder_with_capacity(uint64_t capacity) {
    if (capacity > UINT64_C(9223372036854775807)) abort();
    uint8_t *data = capacity == 0 ? NULL : (uint8_t *)malloc((size_t)capacity);
    if (capacity != 0 && data == NULL) abort();
    if (data != NULL) ++raw_allocations;
    return (raw_builder){data, 0, capacity, 1};
}
static void builder_push(raw_builder *builder, uint64_t byte) {
    if (builder->state != 1 || byte > 255) abort();
    if (builder->length == UINT64_MAX) abort();
    uint64_t required = builder->length + 1;
    if (required > builder->capacity) {
        if (builder->capacity > UINT64_MAX / 2) abort();
        uint64_t doubled = builder->capacity * 2;
        uint64_t capacity = required > doubled ? required : doubled;
        if (capacity < 8) capacity = 8;
        if (capacity > UINT64_C(9223372036854775807)) abort();
        uint8_t *replacement = (uint8_t *)malloc((size_t)capacity);
        if (replacement == NULL) abort();
        ++raw_allocations;
        if (builder->length != 0) {
            memcpy(replacement, builder->data, (size_t)builder->length);
        }
        if (builder->data != NULL) {
            free(builder->data);
            ++raw_frees;
        }
        builder->data = replacement;
        builder->capacity = capacity;
    }
    if (builder->length >= builder->capacity) abort();
    builder->data[builder->length++] = (uint8_t)byte;
}
static uint8_t *builder_finish(raw_builder *builder, uint64_t *length) {
    if (builder->state != 1) abort();
    builder->state = 3;
    *length = builder->length;
    return builder->data;
}
static int cont(uint8_t byte) {
    return (byte & UINT8_C(0xC0)) == UINT8_C(0x80);
}
static int validate(const uint8_t *data, uint64_t length, uint64_t *error) {
    uint64_t i = 0;
    while (i < length) {
        uint8_t first = data[i];
        if (first <= UINT8_C(0x7F)) { ++i; continue; }
        if (first >= UINT8_C(0xC2) && first <= UINT8_C(0xDF)) {
            if (i + 1 >= length || !cont(data[i + 1])) { *error = i; return 0; }
            i += 2; continue;
        }
        if (first >= UINT8_C(0xE0) && first <= UINT8_C(0xEF)) {
            if (i + 2 >= length) { *error = i; return 0; }
            uint8_t second = data[i + 1];
            int ok = cont(second);
            if (first == UINT8_C(0xE0)) ok = second >= UINT8_C(0xA0) && second <= UINT8_C(0xBF);
            if (first == UINT8_C(0xED)) ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x9F);
            if (!ok || !cont(data[i + 2])) { *error = i; return 0; }
            i += 3; continue;
        }
        if (first >= UINT8_C(0xF0) && first <= UINT8_C(0xF4)) {
            if (i + 3 >= length) { *error = i; return 0; }
            uint8_t second = data[i + 1];
            int ok = cont(second);
            if (first == UINT8_C(0xF0)) ok = second >= UINT8_C(0x90) && second <= UINT8_C(0xBF);
            if (first == UINT8_C(0xF4)) ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x8F);
            if (!ok || !cont(data[i + 2]) || !cont(data[i + 3])) { *error = i; return 0; }
            i += 4; continue;
        }
        *error = i; return 0;
    }
    *error = 0; return 1;
}
static uint64_t scalar_count(const uint8_t *data, uint64_t length) {
    uint64_t count = 0;
    for (uint64_t i = 0; i < length; ++count) {
        uint8_t first = data[i];
        i += first <= UINT8_C(0x7F) ? 1 : first <= UINT8_C(0xDF) ? 2 : first <= UINT8_C(0xEF) ? 3 : 4;
    }
    return count;
}
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    uint64_t packed = strtoull(argv[1], NULL, 10);
    uint64_t requested = strtoull(argv[2], NULL, 10);
    uint64_t repetitions = strtoull(argv[3], NULL, 10);
    uint64_t checksum = 0;
    for (uint64_t repetition = 0; repetition < repetitions; ++repetition) {
        raw_builder builder = builder_with_capacity(requested);
        for (uint64_t i = 0; i < requested; ++i) {
            builder_push(&builder, (packed >> (i * 8)) & 255);
        }
        uint64_t length = 0;
        uint8_t *data = builder_finish(&builder, &length);
        uint64_t error = 0;
        if (validate(data, length, &error)) {
            checksum += (UINT64_C(1) << 63) | (scalar_count(data, length) << 32) | length;
        } else {
            checksum += error;
        }
        if (data != NULL) {
            free(data);
            ++raw_frees;
        }
    }
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0\n", raw_allocations, raw_frees);
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
