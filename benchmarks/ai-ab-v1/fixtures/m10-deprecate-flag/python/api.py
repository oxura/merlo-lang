def transform(wire):
    operation, compact, name = wire.split("|", 2)
    # Pre-migration path ignores the compact flag.
    return {"text": "Name: " + name}
