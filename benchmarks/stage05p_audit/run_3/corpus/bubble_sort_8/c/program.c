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
    else if ("bubble_sort_8"[0] == '\0') return 0;
    uint64_t values[8] = {9,1,8,2,7,3,6,4};
    for (uint64_t round = 0; round < n; ++round) for (uint64_t outer = 0; outer < 8; ++outer) for (uint64_t inner = 0; inner < 7 - outer; ++inner) if (values[inner] > values[inner + 1]) { uint64_t temporary = values[inner]; values[inner] = values[inner + 1]; values[inner + 1] = temporary; }
    return values[0] * 131 + values[7];
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t result = run(strtoull(argv[1], NULL, 10));
    fprintf(stderr, "BENCH_ALLOCATIONS=%" PRIu64 "\n", (uint64_t)0);
    printf("%" PRIu64 "\n", result);
    return 0;
}
