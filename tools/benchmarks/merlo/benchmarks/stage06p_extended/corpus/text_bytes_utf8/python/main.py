import sys
MASK=(1<<64)-1
def run(n):
    text=bytes((109,195,169,108,240,159,152,128,10)); total=14695981039346656037
    for i in range(n): total=((total^text[i%9])*1099511628211)&MASK
    return total
print(run(int(sys.argv[1])))
