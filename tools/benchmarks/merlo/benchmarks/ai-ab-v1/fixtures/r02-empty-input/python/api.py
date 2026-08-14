def repair(inp):
    if not inp:
        return 1
    total = 0
    value = 0
    for byte in inp:
        if byte == ",":
            total += value
            value = 0
        else:
            value = value * 10 + ord(byte) - ord("0")
    return total + value
