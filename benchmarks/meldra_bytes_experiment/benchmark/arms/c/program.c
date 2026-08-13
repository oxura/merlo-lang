#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t mix(uint64_t value, uint64_t index, uint64_t seed) {
    uint64_t shifted = seed + index * UINT64_C(17);
    uint64_t mixed = value ^ shifted;
    uint64_t product = mixed * UINT64_C(11400714819323198485);
    return product ^ (product >> 29);
}

__attribute__((noinline)) static uint64_t workload(uint64_t n, uint64_t seed, uint64_t rounds, uint64_t slice_start, uint64_t slice_length) {
    if (n == 0 || slice_start > n || slice_length > n - slice_start) abort();
    uint8_t *bytes = (uint8_t *)malloc((size_t)n);
    if (bytes == NULL) abort();
    for (uint64_t i = 0; i < n; ++i) bytes[i] = (uint8_t)(mix(seed, i, seed) & UINT64_C(255));
    for (uint64_t round = 0; round < rounds; ++round) {
        for (uint64_t i = 0; i < n; ++i) bytes[i] = (uint8_t)(mix(bytes[i], i, seed + round) & UINT64_C(255));
    }
    const uint8_t *view = bytes + slice_start;
    uint64_t checksum = seed;
    for (uint64_t j = 0; j < slice_length; ++j) checksum ^= ((uint64_t)view[j] + j) * UINT64_C(1099511628211);
    uint64_t observed_length = slice_length;
    bytes[0] = (uint8_t)((bytes[0] + checksum + observed_length) & UINT64_C(255));
    uint64_t result = checksum ^ observed_length ^ bytes[0];
    free(bytes);
    return result;
}

int main(int argc, char **argv) {
    if (argc != 6) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t seed = strtoull(argv[2], NULL, 10);
    uint64_t rounds = strtoull(argv[3], NULL, 10);
    uint64_t slice_start = strtoull(argv[4], NULL, 10);
    uint64_t slice_length = strtoull(argv[5], NULL, 10);
    printf("%" PRIu64 "\n", workload(n, seed, rounds, slice_start, slice_length));
    fprintf(stderr, "BENCH_ALLOCATIONS=1 BENCH_FREES=1 BENCH_ALLOCATED_BYTES=%" PRIu64 " BENCH_PAYLOAD_COPIES=0\n", n);
    return 0;
}
