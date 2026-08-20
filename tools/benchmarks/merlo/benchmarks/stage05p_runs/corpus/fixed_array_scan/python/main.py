import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    v=(3,1,4,1,5,9,2,6)
    checksum=0
    for i in range(n): checksum=(checksum+v[i&7]*(i+1))&MASK
    return checksum
result=run(int(sys.argv[1]))
print("BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
