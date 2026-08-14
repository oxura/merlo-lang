def repair(inp):
    # Generic baseline: splitting every delimiter ignores quoted fields.
    return len(inp.split(','))
