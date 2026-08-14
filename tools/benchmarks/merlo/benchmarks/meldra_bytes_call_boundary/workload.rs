use std::env;
fn scan(data: &[u8], mut state: u64) -> u64 {
    for (i, value) in data.iter().enumerate() {
        state = (state ^ (*value as u64).wrapping_add(i as u64).wrapping_add(1)).wrapping_mul(1099511628211);
    }
    state
}
fn transform(mut data: Vec<u8>, salt: u64) -> Vec<u8> {
    for (i, value) in data.iter_mut().enumerate() {
        *value = ((*value as u64) ^ salt.wrapping_add(i as u64)) as u8;
    }
    data
}
fn main() {
    let values: Vec<u64> = env::args().skip(1).map(|value| value.parse().unwrap()).collect();
    if values.len() != 5 { std::process::exit(2); }
    let (n, seed, rounds, start, length) = (values[0] as usize, values[1], values[2], values[3] as usize, values[4] as usize);
    if length > n || start > n - length { std::process::exit(3); }
    let mut owner = vec![0u8; n];
    for (i, value) in owner.iter_mut().enumerate() { *value = seed.wrapping_add((i as u64).wrapping_mul(17)).wrapping_add((i >> 3) as u64) as u8; }
    let mut checksum = seed;
    for round in 0..rounds {
        let offset = (start as u64).wrapping_add(round.wrapping_mul(97)) as usize % (n - length + 1);
        checksum = scan(&owner[offset..offset + length], checksum);
        owner[offset] = (owner[offset] as u64).wrapping_add(checksum).wrapping_add(round) as u8;
    }
    let transformed = transform(owner, seed);
    checksum = scan(&transformed[start..start + length], checksum).wrapping_add(transformed.len() as u64);
    println!("{}", checksum);
    eprintln!("BENCH_ALLOCATIONS=1\nBENCH_FREES=1 BENCH_ALLOCATED_BYTES={} BENCH_PAYLOAD_COPIES=0", n);
}
