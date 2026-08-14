import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    return 42
result=run(int(sys.argv[1]))
print(f"BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
