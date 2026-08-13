package main
import("fmt";"os";"strconv")
type Node struct{value uint64;left,right *Node}
func build(value uint64,depth int)*Node{if depth==0{return nil};return &Node{value,build(value*2,depth-1),build(value*2+1,depth-1)}}
func fold(node *Node)uint64{if node==nil{return 0};return node.value+fold(node.left)+fold(node.right)}
func run(n uint64)uint64{root:=build(1,12);var sum uint64;for i:=uint64(0);i<n;i++{sum+=fold(root)};return sum}
func main(){n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{panic(e)};fmt.Printf("%d\n",run(n))}
