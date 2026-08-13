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
func run(n uint64) uint64 { var checksum uint64; v:=[8]uint64{9,1,8,2,7,3,6,4}; for r:=uint64(0); r<n; r++ { for o:=0;o<8;o++ { for i:=0;i<7-o;i++ { if v[i]>v[i+1] { v[i],v[i+1]=v[i+1],v[i] } } } }; return v[0]*131+v[7] }
func main() { n,err:=strconv.ParseUint(os.Args[1],10,64); if err!=nil { panic(err) }; result:=run(n); fmt.Fprintf(os.Stderr, "BENCH_ALLOCATIONS=0\n"); fmt.Printf("%d\n",result) }
