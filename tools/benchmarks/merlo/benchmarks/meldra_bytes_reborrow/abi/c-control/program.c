#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { const uint8_t *data; uint64_t length; } bytes_view;
__attribute__((noinline)) static uint64_t leaf(bytes_view data, uint64_t state) {
    for (uint64_t i = 0; i < data.length; ++i) state = (state ^ ((uint64_t)data.data[i] + i + 23)) * UINT64_C(1099511628211);
    return state;
}
__attribute__((noinline)) static uint64_t middle(bytes_view data, uint64_t state) { return leaf(data, state); }
__attribute__((noinline)) static uint64_t outer(bytes_view data, uint64_t state) { return middle(data, state); }
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint8_t *owner = n ? malloc((size_t)n) : NULL;
    if (n && owner == NULL) return 3;
    for (uint64_t i = 0; i < n; ++i) owner[i] = (uint8_t)i;
    bytes_view root = { owner, n };
    uint64_t result = outer(root, 7);
    free(owner);
    printf("%" PRIu64 "\n", result);
    return 0;
}
