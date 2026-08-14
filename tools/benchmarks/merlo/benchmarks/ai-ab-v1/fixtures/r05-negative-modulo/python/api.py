def repair(inp):
    value, modulus = (int(part) for part in inp.split(",", 1))
    remainder = abs(value) % modulus
    return -remainder if value < 0 else remainder
