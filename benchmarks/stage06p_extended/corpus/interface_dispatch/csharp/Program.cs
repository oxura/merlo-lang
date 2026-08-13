using System;
static class Program{interface IOperation{ulong Apply(ulong value);}sealed class Square:IOperation{public ulong Apply(ulong value)=>value*value;}sealed class Increment:IOperation{public ulong Apply(ulong value)=>value+1;}
static ulong Run(ulong n){IOperation[] ops={new Square(),new Increment()};ulong sum=0;for(ulong i=0;i<n;i++)sum+=ops[i&1].Apply(i);return sum;}
static void Main(string[] args){unchecked{Console.WriteLine(Run(ulong.Parse(args[0])));}}}
