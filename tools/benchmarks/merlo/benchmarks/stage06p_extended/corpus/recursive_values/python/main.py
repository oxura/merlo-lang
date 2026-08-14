import sys
MASK=(1<<64)-1
class Node:
    __slots__=("value","left","right")
    def __init__(self,value,left,right): self.value=value;self.left=left;self.right=right
def build(value,depth): return None if depth==0 else Node(value,build(value*2,depth-1),build(value*2+1,depth-1))
def fold(node): return 0 if node is None else (node.value+fold(node.left)+fold(node.right))&MASK
def run(n):
    root=build(1,12);total=0
    for _ in range(n): total=(total+fold(root))&MASK
    return total
print(run(int(sys.argv[1])))
