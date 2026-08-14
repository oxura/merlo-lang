#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t encode(uint64_t scalar, uint8_t *data) {
    if (scalar > UINT64_C(0x10FFFF) || (scalar >= UINT64_C(0xD800) && scalar <= UINT64_C(0xDFFF))) return 0;
    if (scalar <= UINT64_C(0x7F)) { data[0] = (uint8_t)scalar; return 1; }
    if (scalar <= UINT64_C(0x7FF)) {
        data[0] = (uint8_t)(UINT64_C(0xC0) | (scalar >> 6));
        data[1] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        return 2;
    }
    if (scalar <= UINT64_C(0xFFFF)) {
        data[0] = (uint8_t)(UINT64_C(0xE0) | (scalar >> 12));
        data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
        data[2] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        return 3;
    }
    data[0] = (uint8_t)(UINT64_C(0xF0) | (scalar >> 18));
    data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & UINT64_C(0x3F)));
    data[2] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
    data[3] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
    return 4;
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
    if (argc != 3) return 2;
    uint64_t scalar = strtoull(argv[1], NULL, 10);
    uint64_t repetitions = strtoull(argv[2], NULL, 10);
    uint64_t checksum = 0;
    uint64_t allocations = 0;
    uint64_t frees = 0;
    for (uint64_t iteration = 0; iteration < repetitions; ++iteration) {
        uint8_t *data = (uint8_t *)malloc(4);
        ++allocations;
        if (data == NULL) return 3;
        uint64_t length = encode(scalar, data);
        if (length == 0) return 4;
        checksum += length + scalar_count(data, length);
        free(data);
        ++frees;
    }
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0\n", allocations, frees);
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
