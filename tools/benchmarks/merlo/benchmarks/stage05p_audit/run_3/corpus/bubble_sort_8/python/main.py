import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    v=[9,1,8,2,7,3,6,4]
    for _ in range(n):
        for o in range(8):
            for i in range(7-o):
                if v[i]>v[i+1]: v[i],v[i+1]=v[i+1],v[i]
    return v[0]*131+v[7]
result=run(int(sys.argv[1]))
print("BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
