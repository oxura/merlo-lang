enum Operation{Square,Increment}impl Operation{fn apply(&self,value:u64)->u64{match self{Self::Square=>value.wrapping_mul(value),Self::Increment=>value.wrapping_add(1)}}}
fn run(n:u64)->u64{let operations=[Operation::Square,Operation::Increment];let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(operations[(i&1)as usize].apply(i));}sum}
fn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}
