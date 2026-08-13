using System;
static class Program{static ulong Square(ulong value)=>value*value;static ulong Increment(ulong value)=>value+1;
static ulong Run(ulong n){ulong sum=0;for(ulong i=0;i<n;i++)sum+=(i&1)==0?Square(i):Increment(i);return sum;}
static void Main(string[] args){unchecked{Console.WriteLine(Run(ulong.Parse(args[0])));}}}
