package main
import (
    "fmt"
    "os"
    "strconv"
)
type Point struct { x,y uint64 }
type Node struct { value,left,right uint64 }
//go:noinline
func makeValues(i uint64) []uint64 { v:=make([]uint64,8); for j:=uint64(0);j<8;j++ { v[j]=i+j }; return v }
func run(n uint64) uint64 { var checksum uint64; v:=[8]uint64{1,2,3,4,5,6,7,8}; for r:=uint64(0); r<n; r++ { v[r&7]+=r; for _,x:=range v { m:=x*x; if m%2==0 { checksum+=m } } }; return checksum }
func main() { n,err:=strconv.ParseUint(os.Args[1],10,64); if err!=nil { panic(err) }; result:=run(n); fmt.Fprintf(os.Stderr, "BENCH_ALLOCATIONS=0\n"); fmt.Printf("%d\n",result) }
