use std::env;

#[inline(always)]
fn mix(value: u64, index: u64, seed: u64) -> u64 {
    let shifted = seed.wrapping_add(index.wrapping_mul(17));
    let mixed = value ^ shifted;
    let product = mixed.wrapping_mul(11_400_714_819_323_198_485);
    product ^ (product >> 29)
}

#[inline(never)]
fn workload(n: usize, seed: u64, rounds: u64, slice_start: usize, slice_length: usize) -> u64 {
    let mut bytes = vec![0u8; n];
    for i in 0..n { bytes[i] = (mix(seed, i as u64, seed) & 255) as u8; }
    for round in 0..rounds {
        for i in 0..n { bytes[i] = (mix(bytes[i] as u64, i as u64, seed.wrapping_add(round)) & 255) as u8; }
    }
    let view = &bytes[slice_start..slice_start + slice_length];
    let mut checksum = seed;
    for j in 0..slice_length {
        checksum ^= ((view[j] as u64).wrapping_add(j as u64)).wrapping_mul(1_099_511_628_211);
    }
    let observed_length = view.len() as u64;
    bytes[0] = ((bytes[0] as u64).wrapping_add(checksum).wrapping_add(observed_length) & 255) as u8;
    checksum ^ observed_length ^ bytes[0] as u64
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 6 { std::process::exit(2); }
    let n = args[1].parse::<usize>().unwrap();
    let seed = args[2].parse::<u64>().unwrap();
    let rounds = args[3].parse::<u64>().unwrap();
    let slice_start = args[4].parse::<usize>().unwrap();
    let slice_length = args[5].parse::<usize>().unwrap();
    println!("{}", workload(n, seed, rounds, slice_start, slice_length));
    eprintln!("BENCH_ALLOCATIONS=1 BENCH_FREES=1 BENCH_ALLOCATED_BYTES={} BENCH_PAYLOAD_COPIES=0", n);
}
