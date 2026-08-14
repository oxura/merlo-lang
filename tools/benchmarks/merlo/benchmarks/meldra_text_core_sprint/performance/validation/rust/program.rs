use std::env;
fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 { std::process::exit(2); }
    let packed: u64 = args[1].parse().unwrap();
    let length: usize = args[2].parse().unwrap();
    let repetitions: u64 = args[3].parse().unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut data = Vec::with_capacity(length);
        for index in 0..length {
            data.push((packed >> ((index * 8) & 63)) as u8);
        }
        match std::str::from_utf8(&data) {
            Ok(text) => {
                checksum = checksum.wrapping_add(
                    (1u64 << 63)
                        | ((text.chars().count() as u64) << 32)
                        | data.len() as u64,
                );
            }
            Err(error) => {
                checksum = checksum.wrapping_add(error.valid_up_to() as u64);
            }
        }
    }
    println!("{checksum}");
}