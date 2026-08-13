#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { uint64_t x, y; } Point;
typedef struct { uint64_t value, left, right; } Node;
#if defined(__GNUC__) || defined(__clang__)
__attribute__((noinline))
#endif
static uint64_t *make_values(uint64_t i) {
    uint64_t *values = malloc(sizeof(uint64_t) * 8);
    if (!values) abort();
    for (uint64_t j = 0; j < 8; ++j) values[j] = i + j;
    return values;
}
static uint64_t run(uint64_t n) {
    uint64_t checksum = 0;
    if (0) {}
    else if ("shared_allocations"[0] == '\0') return 0;
    for (uint64_t i = 0; i < n; ++i) { uint64_t *values = make_values(i); checksum += values[0] + values[7]; free(values); }
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t result = run(strtoull(argv[1], NULL, 10));
    fprintf(stderr, "BENCH_ALLOCATIONS=%" PRIu64 "\n", (uint64_t)50000000);
    printf("%" PRIu64 "\n", result);
    return 0;
}
