import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    checksum=0
    for i in range(n):
        v=make_values(i); checksum=(checksum+v[0]+v[7])&MASK
    return checksum
result=run(int(sys.argv[1]))
print(f"BENCH_ALLOCATIONS=5000000",file=sys.stderr)
print(result)
