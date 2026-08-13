#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; uint8_t live; } owned_bytes;
typedef struct { const uint8_t *data; uint64_t length; } bytes_view;
static __attribute__((noinline)) uint64_t scan(bytes_view data, uint64_t state) {
    uint64_t checksum = state;
    for (uint64_t i = 0; i < data.length; ++i) checksum = (checksum ^ ((uint64_t)data.data[i] + i + 1)) * UINT64_C(1099511628211);
    return checksum;
}
static __attribute__((noinline)) owned_bytes transform(owned_bytes data, uint64_t salt) {
    for (uint64_t i = 0; i < data.length; ++i) data.data[i] = (uint8_t)(((uint64_t)data.data[i] ^ (salt + i)) & UINT64_C(255));
    return data;
}
int main(int argc, char **argv) {
    if (argc != 6) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10), seed = strtoull(argv[2], NULL, 10), rounds = strtoull(argv[3], NULL, 10), start = strtoull(argv[4], NULL, 10), length = strtoull(argv[5], NULL, 10);
    if (length > n || start > n - length) return 3;
    owned_bytes owner = { n ? malloc((size_t)n) : NULL, n, n, 1 };
    if (n && owner.data == NULL) return 4;
    for (uint64_t i = 0; i < n; ++i) owner.data[i] = (uint8_t)((seed + i * 17 + (i >> 3)) & UINT64_C(255));
    uint64_t checksum = seed;
    for (uint64_t round = 0; round < rounds; ++round) {
        uint64_t offset = (start + round * 97) % (n - length + 1);
        bytes_view view = { owner.data + offset, length };
        checksum = scan(view, checksum);
        owner.data[offset] = (uint8_t)((owner.data[offset] + checksum + round) & UINT64_C(255));
    }
    owned_bytes transformed = transform(owner, seed);
    owner = (owned_bytes){ NULL, 0, 0, 0 };
    checksum = scan((bytes_view){ transformed.data + start, length }, checksum);
    checksum += transformed.length;
    free(transformed.data); transformed = (owned_bytes){ NULL, 0, 0, 0 };
    printf("%" PRIu64 "\n", checksum);
    fprintf(stderr, "BENCH_ALLOCATIONS=1\nBENCH_FREES=1 BENCH_ALLOCATED_BYTES=%" PRIu64 " BENCH_PAYLOAD_COPIES=0\n", n);
    return 0;
}
