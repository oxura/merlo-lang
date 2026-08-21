import sys
MASK=(1<<64)-1
def make_values(i):
    return [(i+j)&MASK for j in range(8)]
def run(n):
    p=b'meldra-native'
    checksum=14695981039346656037
    for i in range(n): checksum=((checksum^p[i%13])*1099511628211)&MASK
    return checksum
result=run(int(sys.argv[1]))
print("BENCH_ALLOCATIONS=0",file=sys.stderr)
print(result)
