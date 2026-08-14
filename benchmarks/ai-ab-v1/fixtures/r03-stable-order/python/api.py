def repair(inp):
    return sorted(inp,key=lambda x:(x['k'],x['v']),reverse=True)
