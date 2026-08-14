using System;
static class Program{static ulong Run(ulong n){byte[] text={109,195,169,108,240,159,152,128,10};ulong sum=14695981039346656037UL;for(ulong i=0;i<n;i++){sum^=text[i%9];sum*=1099511628211;}return sum;}
static void Main(string[] args){unchecked{Console.WriteLine(Run(ulong.Parse(args[0])));}}}
