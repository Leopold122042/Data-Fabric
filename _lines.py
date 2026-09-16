import sys,io
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
f,a,b=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
l=open(f,encoding="utf-8").read().split("\n")
for i in range(a-1,min(b,len(l))): print("%4d: %s"%(i+1,l[i]))
