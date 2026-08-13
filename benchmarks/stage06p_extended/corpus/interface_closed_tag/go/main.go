package main
import("fmt";"os";"strconv")
type OperationTag uint8;const(Square OperationTag=iota;Increment);func apply(tag OperationTag,v uint64)uint64{if tag==Square{return v*v};return v+1}
func run(n uint64)uint64{ops:=[2]OperationTag{Square,Increment};var sum uint64;for i:=uint64(0);i<n;i++{sum+=apply(ops[i&1],i)};return sum}
func main(){n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{panic(e)};fmt.Printf("%d\n",run(n))}
