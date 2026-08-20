import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    value=1
    checksum=0
    for i in range(n):
        value=(value*1664525+1013904223)&MASK
        checksum=(checksum^(value+i))&MASK
    return checksum
result=run(int(sys.argv[1]))
print("BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
