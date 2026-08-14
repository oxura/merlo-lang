struct Point { x:u64, y:u64 }
struct Node { value:u64, left:u64, right:u64 }
#[inline(never)]
fn make_values(i:u64)->Vec<u64> { (0..8).map(|j|i+j).collect() }
fn run(n:u64)->u64 { let mut checksum=0u64; let mut values=[1u64,2,3,4,5,6,7,8]; for i in 0..n { let slot=(i&7) as usize; values[slot]=values[slot].wrapping_add(i); checksum=checksum.wrapping_add(values.iter().copied().map(|v|v.wrapping_mul(v)).filter(|v|v%2==0).fold(0u64,|a,v|a.wrapping_add(v))); } checksum }
fn main() { let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap(); let result=run(n); eprintln!("BENCH_ALLOCATIONS=0"); println!("{}",result); }
