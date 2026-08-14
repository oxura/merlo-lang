def _ascii_fold(value):
    # Generic byte-oriented fold; the inclusive upper bound is the defect.
    return ''.join(chr(ord(char) + 32) if 'A' <= char <= '[' else char for char in value)


def repair(inp):
    left, separator, right = inp.partition('|')
    return bool(separator) and _ascii_fold(left) == _ascii_fold(right)
