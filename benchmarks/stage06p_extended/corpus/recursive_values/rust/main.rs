struct Node{value:u64,left:Option<Box<Node>>,right:Option<Box<Node>>}
fn build(value:u64,depth:u32)->Option<Box<Node>>{if depth==0{None}else{Some(Box::new(Node{value,left:build(value*2,depth-1),right:build(value*2+1,depth-1)}))}}
fn fold(node:&Option<Box<Node>>)->u64{match node{None=>0,Some(value)=>value.value.wrapping_add(fold(&value.left)).wrapping_add(fold(&value.right))}}
fn run(n:u64)->u64{let root=build(1,12);let mut sum=0u64;for _ in 0..n{sum=sum.wrapping_add(fold(&root));}sum}
fn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}
