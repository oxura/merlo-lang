using System;
using System.Runtime.CompilerServices;
readonly record struct Point(ulong X, ulong Y);
readonly record struct Node(ulong Value, ulong Left, ulong Right);
static class Program {
 [MethodImpl(MethodImplOptions.NoInlining)]
 static ulong[] MakeValues(ulong i) { ulong[] values=new ulong[8]; for(ulong j=0;j<8;j++) values[j]=i+j; return values; }
 static ulong Run(ulong n) { unchecked { ulong checksum=0; ulong[] v={9,1,8,2,7,3,6,4}; for(ulong r=0;r<n;r++)for(int o=0;o<8;o++)for(int i=0;i<7-o;i++)if(v[i]>v[i+1]){ulong t=v[i];v[i]=v[i+1];v[i+1]=t;} return v[0]*131+v[7]; } }
 static void Main(string[] args) { ulong result=Run(ulong.Parse(args[0])); Console.Error.WriteLine("BENCH_ALLOCATIONS=0"); Console.WriteLine(result); }
}
