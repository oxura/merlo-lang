using System;
static class Program{sealed class Node{public ulong Value;public Node? Left,Right;public Node(ulong v,Node? l,Node? r){Value=v;Left=l;Right=r;}}
static Node? Build(ulong value,int depth)=>depth==0?null:new Node(value,Build(value*2,depth-1),Build(value*2+1,depth-1));
static ulong Fold(Node? node)=>node is null?0:node.Value+Fold(node.Left)+Fold(node.Right);
static ulong Run(ulong n){Node? root=Build(1,12);ulong sum=0;for(ulong i=0;i<n;i++)sum+=Fold(root);return sum;}
static void Main(string[] args){unchecked{Console.WriteLine(Run(ulong.Parse(args[0])));}}}
