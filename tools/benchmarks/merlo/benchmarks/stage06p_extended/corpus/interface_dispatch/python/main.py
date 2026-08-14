import sys
MASK=(1<<64)-1
class Square:
    __slots__=()
    def apply(self,value): return value*value
class Increment:
    __slots__=()
    def apply(self,value): return value+1
def run(n):
    operations=(Square(),Increment());total=0
    for i in range(n): total=(total+operations[i&1].apply(i))&MASK
    return total
print(run(int(sys.argv[1])))
