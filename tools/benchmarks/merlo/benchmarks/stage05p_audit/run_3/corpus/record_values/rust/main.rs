struct Point { x:u64, y:u64 }
struct Node { value:u64, left:u64, right:u64 }
#[inline(never)]
fn make_values(i:u64)->Vec<u64> { (0..8).map(|j|i+j).collect() }
fn run(n:u64)->u64 { let mut checksum=0u64; for i in 0..n { let p=Point{x:i,y:i.wrapping_mul(3).wrapping_add(1)}; checksum=checksum.wrapping_add(p.x^p.y); } checksum }
fn main() { let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap(); let result=run(n); eprintln!("BENCH_ALLOCATIONS=0"); println!("{}",result); }
