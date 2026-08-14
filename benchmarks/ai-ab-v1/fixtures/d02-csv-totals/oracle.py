#!/usr/bin/env python3
import argparse,json,subprocess
from pathlib import Path
CASES=[[[{'product': 'tea', 'quantity': 2}, {'product': 'coffee', 'quantity': 3}, {'product': 'tea', 'quantity': 4}], {'coffee': 3, 'tea': 6}], [[{'product': 'tea', 'quantity': 4}, {'product': 'coffee', 'quantity': 3}, {'product': 'tea', 'quantity': 2}], {'coffee': 3, 'tea': 6}], [[{'product': 'tea', 'quantity': 2}, {'product': 'coffee', 'quantity': 3}, {'product': 'tea', 'quantity': 4}, {'product': 'tea', 'quantity': 2}], {'coffee': 3, 'tea': 8}]]
def check(workspace,arm):
 root=Path(workspace)/arm; source=root/("main.py" if arm=="python" else "main.mlo"); results=[]
 for request,expected in CASES:
  try:
    proc=subprocess.run((["python3", "-B", str(source)] if arm=="python" else ["merlo","run",str(source)]),input=json.dumps(request, ensure_ascii=False).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
  except subprocess.TimeoutExpired:
    results.append({"passed":False,"terminal_reason":"timeout"}); continue
  try: actual=json.loads(proc.stdout)
  except (json.JSONDecodeError,UnicodeDecodeError): actual=None
  results.append({"passed":proc.returncode==0 and actual==expected,"actual":actual,"expected":expected})
 return {"case_id":"d02-csv-totals","passed":bool(results) and all(x["passed"] for x in results),"cases":results}
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("--workspace",required=True); p.add_argument("--arm",required=True,choices=["merlo","python"]); a=p.parse_args(); print(json.dumps(check(a.workspace,a.arm),sort_keys=True))
