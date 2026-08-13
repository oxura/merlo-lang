trait Operation{fn apply(&self,value:u64)->u64;}struct Square;struct Increment;impl Operation for Square{fn apply(&self,value:u64)->u64{value.wrapping_mul(value)}}impl Operation for Increment{fn apply(&self,value:u64)->u64{value.wrapping_add(1)}}
fn run(n:u64)->u64{let square=Square;let increment=Increment;let operations:[&dyn Operation;2]=[&square,&increment];let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(operations[(i&1)as usize].apply(i));}sum}
fn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}
