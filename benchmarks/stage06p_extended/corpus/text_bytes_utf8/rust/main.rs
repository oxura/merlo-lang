fn run(n:u64)->u64{let text:[u8;9]=[109,195,169,108,240,159,152,128,10];let mut sum=14695981039346656037u64;for i in 0..n{sum^=text[(i%9)as usize]as u64;sum=sum.wrapping_mul(1099511628211);}sum}
fn main(){let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();println!("{}",run(n));}
