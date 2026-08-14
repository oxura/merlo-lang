using System;
using System.Runtime.CompilerServices;
readonly record struct Point(ulong X, ulong Y);
readonly record struct Node(ulong Value, ulong Left, ulong Right);
static class Program {
 [MethodImpl(MethodImplOptions.NoInlining)]
 static ulong[] MakeValues(ulong i) { ulong[] values=new ulong[8]; for(ulong j=0;j<8;j++) values[j]=i+j; return values; }
 static ulong Run(ulong n) { unchecked { ulong checksum=0; for(ulong i=0;i<n;i++){ulong[] v=MakeValues(i);checksum+=v[0]+v[7];} return checksum; } }
 static void Main(string[] args) { ulong result=Run(ulong.Parse(args[0])); Console.Error.WriteLine("BENCH_ALLOCATIONS=500000"); Console.WriteLine(result); }
}
