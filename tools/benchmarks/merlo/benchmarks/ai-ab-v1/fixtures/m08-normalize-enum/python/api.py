def transform(wire):
    operation, status = wire.split("|", 1)
    # Pre-migration enum keeps the source spelling.
    return {"status": status}
