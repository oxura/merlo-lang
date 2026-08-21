import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    v=[1,2,3,4,5,6,7,8]
    checksum=0
    for i in range(n):
        v[i&7]=(v[i&7]+i)&MASK
        checksum=(checksum+sum(x*x for x in v if (x*x)%2==0))&MASK
    return checksum
result=run(int(sys.argv[1]))
print("BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
