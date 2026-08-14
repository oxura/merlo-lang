def repair(inp):
    # Fixed-offset, day-boundary-only contract (no timezone database needed).
    hour_text, offset_text = inp.split('|', 1)
    local_hour = int(hour_text) + int(offset_text)
    # Baseline defect: exact midnight rollover is excluded.
    return 1 if local_hour > 24 else 0
