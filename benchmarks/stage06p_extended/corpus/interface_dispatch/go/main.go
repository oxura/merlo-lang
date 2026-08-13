package main
import("fmt";"os";"strconv")
type Operation interface{Apply(uint64)uint64};type Square struct{};type Increment struct{};func(Square)Apply(v uint64)uint64{return v*v};func(Increment)Apply(v uint64)uint64{return v+1}
func run(n uint64)uint64{ops:=[2]Operation{Square{},Increment{}};var sum uint64;for i:=uint64(0);i<n;i++{sum+=ops[i&1].Apply(i)};return sum}
func main(){n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{panic(e)};fmt.Printf("%d\n",run(n))}
