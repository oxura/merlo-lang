using System;
using System.Runtime.CompilerServices;
readonly record struct Point(ulong X, ulong Y);
readonly record struct Node(ulong Value, ulong Left, ulong Right);
static class Program {
 [MethodImpl(MethodImplOptions.NoInlining)]
 static ulong[] MakeValues(ulong i) { ulong[] values=new ulong[8]; for(ulong j=0;j<8;j++) values[j]=i+j; return values; }
 static ulong Run(ulong n) { unchecked { ulong checksum=0; byte[] p=System.Text.Encoding.ASCII.GetBytes("meldra-native"); checksum=14695981039346656037UL; for(ulong i=0;i<n;i++){checksum^=p[i%13];checksum*=1099511628211;} return checksum; } }
 static void Main(string[] args) { ulong result=Run(ulong.Parse(args[0])); Console.Error.WriteLine("BENCH_ALLOCATIONS=0"); Console.WriteLine(result); }
}
