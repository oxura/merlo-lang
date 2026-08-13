#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef enum { SQUARE, INCREMENT } OperationTag;
static uint64_t apply(OperationTag tag,uint64_t value){return tag==SQUARE?value*value:value+1;}
static uint64_t run(uint64_t n){OperationTag operations[2]={SQUARE,INCREMENT};uint64_t sum=0;for(uint64_t i=0;i<n;++i)sum+=apply(operations[i&1],i);return sum;}
int main(int argc,char **argv){if(argc!=2)return 2;uint64_t n=strtoull(argv[1],NULL,10);printf("%" PRIu64 "\n",run(n));return 0;}
