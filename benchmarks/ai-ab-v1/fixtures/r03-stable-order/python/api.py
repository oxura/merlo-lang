def repair(inp):
    items = []
    for token in inp.split(","):
        key, value = token.split(":")
        items.append((int(key), value))
    items.sort()
    return ",".join(f"{key}:{value}" for key, value in items)
