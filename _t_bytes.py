l=open("medsim.py","rb").read().split(b"\n")
for i in range(17,23): print(i+1, repr(l[i]))
print("EOL sample:", "CRLF" if b"\r\n" in open("medsim.py","rb").read()[:400] else "LF")
