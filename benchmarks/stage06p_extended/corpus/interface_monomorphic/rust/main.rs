fn square(value:u64)->u64{value.wrapping_mul(value)}fn increment(value:u64)->u64{value.wrapping_add(1)}
fn run(n:u64)->u64{let mut sum=0u64;for i in 0..n{sum=sum.wrapping_add(if i&1==0{square(i)}else{increment(i)});}sum}
fn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}
