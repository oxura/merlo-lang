package main
import("fmt";"os";"strconv")
func run(n uint64)uint64{text:=[]byte{109,195,169,108,240,159,152,128,10};sum:=uint64(14695981039346656037);for i:=uint64(0);i<n;i++{sum^=uint64(text[i%9]);sum*=1099511628211};return sum}
func main(){n,e:=strconv.ParseUint(os.Args[1],10,64);if e!=nil{panic(e)};fmt.Printf("%d\n",run(n))}
