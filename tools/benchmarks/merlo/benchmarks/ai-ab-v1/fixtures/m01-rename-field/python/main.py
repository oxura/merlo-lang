import sys
from service import run

sys.stdout.write(run(sys.stdin.read()))
