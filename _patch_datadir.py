import ast
p="medsim.py"
b=open(p,"rb").read()
anchor=b'DATA_DIR = os.path.join(BASE_DIR, "data")'
c=b.count(anchor); assert c==1, ("count",c)
newbytes=('DATA_DIR = os.environ.get("MEDFABRIC_DATA_DIR", "").strip() or os.path.join(BASE_DIR, "data")').encode()+b'\r\n'+('os.makedirs(DATA_DIR, exist_ok=True)').encode()
out=b.replace(anchor,newbytes); open(p,"wb").write(out)
ast.parse(open(p,encoding="utf-8-sig").read())
print("PATCH_OK total_lines=", out.count(b"\n")+1)
