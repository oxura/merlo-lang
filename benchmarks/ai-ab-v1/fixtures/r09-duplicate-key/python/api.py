def repair(inp):
    out={}
    for line in inp.splitlines():
        key,value=line.split('=',1)
        if key not in out: out[key]=value
    return out
