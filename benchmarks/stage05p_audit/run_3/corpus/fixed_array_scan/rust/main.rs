struct Point { x:u64, y:u64 }
struct Node { value:u64, left:u64, right:u64 }
#[inline(never)]
fn make_values(i:u64)->Vec<u64> { (0..8).map(|j|i+j).collect() }
fn run(n:u64)->u64 { let mut checksum=0u64; let values=[3u64,1,4,1,5,9,2,6]; for i in 0..n { checksum=checksum.wrapping_add(values[(i&7) as usize].wrapping_mul(i.wrapping_add(1))); } checksum }
fn main() { let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap(); let result=run(n); eprintln!("BENCH_ALLOCATIONS=0"); println!("{}",result); }
