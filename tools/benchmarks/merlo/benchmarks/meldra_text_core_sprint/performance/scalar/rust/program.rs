use std::env;
fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 { std::process::exit(2); }
    let scalar: u32 = args[1].parse().unwrap();
    let repetitions: u64 = args[2].parse().unwrap();
    let value = char::from_u32(scalar).unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut encoded = [0u8; 4];
        let text = value.encode_utf8(&mut encoded);
        let mut data = Vec::with_capacity(text.len());
        data.extend_from_slice(text.as_bytes());
        checksum = checksum.wrapping_add(data.len() as u64 + 1);
    }
    println!("{checksum}");
}