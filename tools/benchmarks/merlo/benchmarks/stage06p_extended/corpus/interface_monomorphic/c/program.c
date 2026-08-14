#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static uint64_t square(uint64_t value){return value*value;}
static uint64_t increment(uint64_t value){return value+1;}
static uint64_t run(uint64_t n){uint64_t sum=0;for(uint64_t i=0;i<n;++i)sum+=(i&1)?increment(i):square(i);return sum;}
int main(int argc,char **argv){if(argc!=2)return 2;uint64_t n=strtoull(argv[1],NULL,10);printf("%" PRIu64 "\n",run(n));return 0;}
