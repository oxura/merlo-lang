def repair(inp):
    fields = {}
    for part in inp.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    if "count" not in fields:
        return None
    return int(fields["count"])
