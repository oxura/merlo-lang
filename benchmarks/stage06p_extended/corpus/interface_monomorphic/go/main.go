package main
import("fmt";"os";"strconv")
func square(v uint64)uint64{return v*v};func increment(v uint64)uint64{return v+1}
func run(n uint64)uint64{var sum uint64;for i:=uint64(0);i<n;i++{if i&1==0{sum+=square(i)}else{sum+=increment(i)}};return sum}
func main(){n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{panic(e)};fmt.Printf("%d\n",run(n))}
