use std::env;
fn main() {
    let repetitions: u64 = env::args().nth(1).unwrap().parse().unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut value = String::with_capacity(10);
        value.push('A'); value.push('\u{7ff}'); value.push('\u{ffff}'); value.push('\u{10ffff}');
        checksum = checksum.wrapping_add(value.len() as u64);
    }
    println!("{}", checksum);
}
