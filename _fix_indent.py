import re, os, ast
p = "app.py"
raw = open(p, encoding="utf-8", newline="").read()
pat = re.compile(r"(?P<nl>\r?\n)(threading\.Thread\(target=_circulation_loop, daemon=True\)\.start\(\))")
new, n = pat.subn(lambda m: m.group("nl") + "    threading.Thread(target=_circulation_loop, daemon=True).start()", raw)
print("replacements:", n)
if n == 1:
    ast.parse(new)
    open(p, "w", encoding="utf-8", newline="").write(new)
    print("FIXED + AST_OK")
else:
    print("NOOP (expected exactly 1)")
