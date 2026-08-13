#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static uint64_t run(uint64_t n) {
    static const unsigned char text[9] = {109,195,169,108,240,159,152,128,10};
    uint64_t checksum = UINT64_C(14695981039346656037);
    for (uint64_t i=0;i<n;++i) { checksum ^= text[i%9]; checksum *= UINT64_C(1099511628211); }
    return checksum;
}
int main(int argc,char **argv){if(argc!=2)return 2;uint64_t n=strtoull(argv[1],NULL,10);printf("%" PRIu64 "\n",run(n));return 0;}
