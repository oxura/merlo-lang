#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int cont(uint8_t byte) { return (byte & UINT8_C(0xC0)) == UINT8_C(0x80); }
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
    uint64_t length = strtoull(argv[2], NULL, 10);
    uint64_t repetitions = strtoull(argv[3], NULL, 10);
    uint64_t checksum = 0;
    for (uint64_t repetition = 0; repetition < repetitions; ++repetition) {
        uint8_t *data = length == 0 ? NULL : (uint8_t *)malloc((size_t)length);
        if (length != 0 && data == NULL) return 3;
        for (uint64_t i = 0; i < length; ++i) data[i] = (uint8_t)(packed >> (i * 8));
        uint64_t error = 0;
        if (validate(data, length, &error)) checksum += (UINT64_C(1) << 63) | (scalar_count(data, length) << 32) | length;
        else checksum += error;
        free(data);
    }
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
