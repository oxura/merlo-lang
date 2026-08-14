use std::env;
fn main() {
    let repetitions: u64 = env::args().nth(1).unwrap().parse().unwrap();
    let input = "\"\\\n/Ж😀";
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut out = String::with_capacity(input.len() + 2); out.push('"');
        for ch in input.chars() {
            match ch { '"' => out.push_str("\\\""), '\\' => out.push_str("\\\\"), '\u{8}' => out.push_str("\\b"), '\u{c}' => out.push_str("\\f"), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"), c if (c as u32) < 32 => { use std::fmt::Write; write!(&mut out, "\\u{:04x}", c as u32).unwrap(); }, c => out.push(c) }
        }
        out.push('"'); checksum = checksum.wrapping_add(out.len() as u64);
    }
    println!("{}", checksum);
}
