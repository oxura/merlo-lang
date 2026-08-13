import sys
MASK=(1<<64)-1
def square(value): return value*value
def increment(value): return value+1
def run(n):
    total=0
    for i in range(n): total=(total+(square(i) if i&1==0 else increment(i)))&MASK
    return total
print(run(int(sys.argv[1])))
