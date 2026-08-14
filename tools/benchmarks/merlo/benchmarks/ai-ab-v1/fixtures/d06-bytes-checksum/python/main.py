#!/usr/bin/env python3
import json,sys
def main():
    json.load(sys.stdin)
    json.dump({"UNIMPLEMENTED":True},sys.stdout)
if __name__=="__main__": main()
