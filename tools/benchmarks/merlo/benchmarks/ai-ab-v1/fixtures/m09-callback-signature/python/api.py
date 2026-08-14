def transform(wire):
    operation, value = wire.split("|", 1)
    # Pre-migration callback still stringifies its argument.
    return {"seen": value}
