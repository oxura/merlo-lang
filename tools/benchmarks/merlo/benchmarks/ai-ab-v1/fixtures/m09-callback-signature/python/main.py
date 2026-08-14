import json
import sys

from service import run

wire = sys.stdin.read().rstrip("\n")
json.dump(run(wire), sys.stdout, sort_keys=True)
