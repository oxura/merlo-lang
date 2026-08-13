#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint8_t *data; uint64_t len; uint64_t cap; } Buffer;
static uint64_t allocations=0, frees=0, reallocations=0, growth_copies=0;
static void grow(Buffer *b, uint64_t additional) {
    if (additional > UINT64_MAX - b->len) abort();
    uint64_t required = b->len + additional;
    if (required <= b->cap) return;
    uint64_t cap;
    if (b->cap == 0) cap = required > 8 ? required : 8;
    else { if (b->cap > UINT64_MAX / 2) abort(); uint64_t doubled=b->cap*2; cap=required>doubled?required:doubled; }
    uint8_t *next=(uint8_t*)malloc((size_t)cap); if(!next) abort(); ++allocations;
    if (b->data) { if(b->len) memcpy(next,b->data,(size_t)b->len); growth_copies+=b->len; free(b->data); ++frees; ++reallocations; }
    b->data=next; b->cap=cap;
}
__attribute__((noinline)) static uint64_t workload(uint64_t n,uint64_t seed,uint64_t reserved) {
    Buffer b={0}; if(reserved) grow(&b,n);
    for(uint64_t i=0;i<n;++i){ grow(&b,1); b.data[b.len++]=(uint8_t)((seed+i*17)&255); }
    uint64_t out=seed; for(uint64_t i=0;i<b.len;++i) out=(out*UINT64_C(1099511628211))^((uint64_t)b.data[i]+i);
    out ^= b.len; free(b.data); if(b.cap) ++frees; return out;
}
int main(int argc,char**argv){if(argc!=4)return 2;uint64_t n=strtoull(argv[1],0,10),s=strtoull(argv[2],0,10),r=strtoull(argv[3],0,10);printf("%" PRIu64 "\n",workload(n,s,r));fprintf(stderr,"BENCH_ALLOCATIONS=%" PRIu64 " BENCH_REALLOCATIONS=%" PRIu64 " BENCH_FREES=%" PRIu64 " BENCH_GROWTH_COPIED_BYTES=%" PRIu64 " BENCH_FINISH_COPIES=0\n",allocations,reallocations,frees,growth_copies);}
