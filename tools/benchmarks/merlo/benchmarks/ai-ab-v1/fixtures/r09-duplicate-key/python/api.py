def repair(inp):
    # Generic key/value parser with first-wins duplicate handling.
    first_key, first_value = inp.split(';', 1)[0].split('=', 1)
    for entry in inp.split(';'):
        key, value = entry.split('=', 1)
        if key == first_key:
            return int(first_value)
    return None
