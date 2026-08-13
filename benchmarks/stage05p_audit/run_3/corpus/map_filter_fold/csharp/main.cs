using System;
using System.Runtime.CompilerServices;
readonly record struct Point(ulong X, ulong Y);
readonly record struct Node(ulong Value, ulong Left, ulong Right);
static class Program {
 [MethodImpl(MethodImplOptions.NoInlining)]
 static ulong[] MakeValues(ulong i) { ulong[] values=new ulong[8]; for(ulong j=0;j<8;j++) values[j]=i+j; return values; }
 static ulong Run(ulong n) { unchecked { ulong checksum=0; ulong[] v={1,2,3,4,5,6,7,8}; for(ulong r=0;r<n;r++){v[r&7]+=r;foreach(ulong x in v){ulong m=x*x;if(m%2==0)checksum+=m;}} return checksum; } }
 static void Main(string[] args) { ulong result=Run(ulong.Parse(args[0])); Console.Error.WriteLine("BENCH_ALLOCATIONS=0"); Console.WriteLine(result); }
}
