#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#if defined(__GNUC__) || defined(__clang__)
#define NOINLINE __attribute__((noinline))
#else
#define NOINLINE
#endif
typedef struct { uint64_t *data; uint64_t *refs; } Values;
static uint64_t allocations = 0, retains = 0, releases = 0;
static NOINLINE Values make_values(uint64_t i) {
    Values value = { malloc(8 * sizeof(uint64_t)), malloc(sizeof(uint64_t)) };
    if (!value.data || !value.refs) abort();
    *value.refs = 1; ++allocations;
    for (uint64_t j = 0; j < 8; ++j) value.data[j] = i + j;
    return value;
}
static NOINLINE Values retain(Values value) { ++*value.refs; ++retains; return value; }
static NOINLINE void release(Values value) {
    ++releases;
    if (--*value.refs == 0) { free(value.data); free(value.refs); }
}
static uint64_t run(uint64_t n) {
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < n; ++i) {
        Values value = make_values(i);
        Values alias = retain(value);
        checksum += alias.data[0] + alias.data[7];
        release(alias); release(value);
    }
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t result = run(strtoull(argv[1], NULL, 10));
    fprintf(stderr, "BENCH_ALLOCATIONS=%" PRIu64 " RETAINS=%" PRIu64 " RELEASES=%" PRIu64 "\n", allocations, retains, releases);
    printf("%" PRIu64 "\n", result);
    return 0;
}
