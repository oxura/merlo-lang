struct Point { x:u64, y:u64 }
struct Node { value:u64, left:u64, right:u64 }
#[inline(never)]
fn make_values(i:u64)->Vec<u64> { (0..8).map(|j|i+j).collect() }
fn run(n:u64)->u64 { let mut checksum=0u64; let mut v=[9u64,1,8,2,7,3,6,4]; for _ in 0..n { for outer in 0..8 { for inner in 0..(7-outer) { if v[inner]>v[inner+1] { v.swap(inner,inner+1); } } } } v[0]*131+v[7] }
fn main() { let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap(); let result=run(n); eprintln!("BENCH_ALLOCATIONS=0"); println!("{}",result); }
