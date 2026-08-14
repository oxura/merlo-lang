using System;
static class Program{enum OperationTag{Square,Increment}static ulong Apply(OperationTag tag,ulong value)=>tag==OperationTag.Square?value*value:value+1;
static ulong Run(ulong n){OperationTag[] ops={OperationTag.Square,OperationTag.Increment};ulong sum=0;for(ulong i=0;i<n;i++)sum+=Apply(ops[i&1],i);return sum;}
static void Main(string[] args){unchecked{Console.WriteLine(Run(ulong.Parse(args[0])));}}}
