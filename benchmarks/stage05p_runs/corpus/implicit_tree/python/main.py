import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    checksum=0
    for i in range(n):
        x=((i*3+1)&MASK,(i*2+1)&MASK,(i*2+2)&MASK); checksum=(checksum^(x[0]*((x[1]+x[2]+1)&MASK)))&MASK
    return checksum
result=run(int(sys.argv[1]))
print(f"BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
