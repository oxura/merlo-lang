import sys
MASK=(1<<64)-1
def apply(tag,value): return value*value if tag==0 else value+1
def run(n):
    operations=(0,1);total=0
    for i in range(n): total=(total+apply(operations[i&1],i))&MASK
    return total
print(run(int(sys.argv[1])))
