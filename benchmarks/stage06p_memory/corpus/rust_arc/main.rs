use std::sync::Arc;
use std::rc::Rc;
fn run(n:u64)->u64 {
    let mut checksum=0u64;
    for i in 0..n {
        let value = Arc::new([i,i+1,i+2,i+3,i+4,i+5,i+6,i+7]);
        let alias = Arc::clone(&value);
        checksum=checksum.wrapping_add(alias[0]).wrapping_add(alias[7]);
        drop(alias); drop(value);
    }
    checksum
}
fn main() {
    let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();
    let result=run(n);
    eprintln!("BENCH_ALLOCATIONS={}",n);
    println!("{}",result);
}
