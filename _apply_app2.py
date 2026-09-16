# -*- coding: utf-8 -*-
import ast, os, re
BASE = os.path.dirname(os.path.abspath(__file__))
raw0 = open(os.path.join(BASE, "app.py"), encoding="utf-8", newline="").read().lstrip("\ufeff")

def rd(p): return open(os.path.join(BASE, p), encoding="utf-8", newline="").read().lstrip("\ufeff")

# dominant EOL in the file
crlf = raw0.count("\r\n"); lf_only = raw0.count("\n") - crlf
NL = "\r\n" if crlf >= lf_only else "\n"
print("DOMINANT_EOL", "CRLF" if NL == "\r\n" else "LF", "(crlf=%d, lfonly=%d)" % (crlf, lf_only))

src = raw0; fails = []

def sub(name, old_text, new_text):
    global src
    # build tolerant pattern: escape each line, join with any newline sequence
    parts = [re.escape(p) for p in old_text.split("\n")]
    pat = "(?:\r?\n)".join(parts)
    found = re.findall(pat, src)
    if len(found) != 1:
        fails.append((name, "matches=%d (expected 1)" % len(found))); return False
    new_file_eol = new_text.replace("\n", NL)
    src = re.sub(pat, lambda m: new_file_eol, src, count=1)
    print("PASS", name); return True

sub("E1_import_random",
    "import sqlite3\nimport threading\nimport time",
    "import sqlite3\nimport random\nimport threading\nimport time")

sub("C_csi_tail",
    '    with SERVICE_STATS_LOCK:\n        st = SERVICE_STATS.setdefault(sid, {"calls": 0, "total_ms": 0.0})\n'
    '        st["calls"] += 1; st["total_ms"] += ms\n    return {"latency_ms": ms, "result": result}',
    '    with SERVICE_STATS_LOCK:\n        st = SERVICE_STATS.setdefault(sid, {"calls": 0, "total_ms": 0.0})\n'
    '        st["calls"] += 1; st["total_ms"] += ms\n'
    '    try:\n        gov.record_service(sid, latency_ms=ms, success=True, actor="orchestrator")   # \u7f16\u6392\u5185\u90e8\u8c03\u7528\u8ba1\u5165\u7b2c\u4e09\u90e8\u5206\u6d41\u901a\u8ba1\u91cf\uff08\u4e0d\u53d1 service_invoked\uff0c\u907f\u514d\u95ed\u73af\u81ea\u6fc0\uff09\n'
    '    except Exception:\n        pass\n    return {"latency_ms": ms, "result": result}')

sub("E4a_legacy_wf",
    '    return {"service_id": sid, "latency_ms": ms, "stats_after": {"calls": calls, "avg_ms": avg},',
    '    wf = _federation_waterfall(sid, result) if err is None else None\n'
    '    return {"service_id": sid, "latency_ms": ms, "stats_after": {"calls": calls, "avg_ms": avg},')

sub("E4b_legacy_key",
    '            "error": err, "result": result}',
    '            "error": err, "result": result, "waterfall": wf}')

sub("E5_gov_wfmask",
    '    if auth["masked"]:\n        result = gov.mask_payload(result)',
    '    wf = _federation_waterfall(sid, result) if err is None else None   # \u6eaf\u6e90\u7011\u5e03\uff1a\u57fa\u4e8e\u672a\u8131\u654f\u539f\u59cb\u7ed3\u6784\u63d0\u53d6\u5404\u6e90\u8017\u65f6\n'
    '    if auth["masked"]:\n        result = gov.mask_payload(result)')

sub("E6_gov_key",
    '"security": auth, "circulation": circulation, "result": result}',
    '"security": auth, "circulation": circulation, "waterfall": wf, "result": result}')

# F: replace _svc_fed_query region (tolerant slice) with helper + instrumented function
marker = 'def _svc_fed_query(group_by="dept", top_n=8):'
endmark = '@app.get("/api/services")'
s = src.find(marker); e = src.find(endmark, s if s >= 0 else 0)
if s < 0 or e <= s:
    fails.append(("F_fedquery", "markers not found (s=%d e=%d)" % (s, e)))
else:
    block = rd("_n_fedquery.txt").replace("\r\n","\n").strip().replace("\n", NL)
    src = src[:s] + block + NL+NL + src[e:]
    print("PASS F_fedquery")

# G: insert circulation ticker before startup event (tolerant find of decorator line)
m2 = re.search(r'@app\.on_event\(\s*["\']startup["\']\s*\)', src)
if not m2:
    fails.append(("G_circ", "no @app.on_event(startup)"))
else:
    head = src[:m2.start()]
    if not re.search(r'\r?\n\s*\r?$', head):  # ensure trailing newline before block
        head = head.rstrip() + NL
    circ = rd("_n_circ.txt").replace("\r\n","\n").strip().replace("\n", NL)
    src = head + circ + NL+NL + src[m2.start():]
    print("PASS G_circ")

sub("H_thread",
    'threading.Thread(target=_patrol_loop, daemon=True).start()',
    'threading.Thread(target=_patrol_loop, daemon=True).start()\n'
    'threading.Thread(target=_circulation_loop, daemon=True).start()')

# I: insert endpoint before flows-run route (tolerant)
m3 = re.search(r'@app\.post\(\s*["\']/api/data-services/flows/\{flow_id\}/run["\']\s*\)', src)
if not m3:
    fails.append(("I_endpoint", "no flows-run marker"))
else:
    ep = rd("_n_endpoint.txt").replace("\r\n","\n").strip().replace("\n", NL)
    head = src[:m3.start()]
    if not re.search(r'\r?\n\s*\r?$', head):
        head = head.rstrip() + NL
    src = head + ep + NL+NL + src[m3.start():]
    print("PASS I_endpoint")

ast_ok, ast_err = True, ""
try:
    if not fails: ast.parse(src)
except Exception as exc:
    ast_ok, ast_err = False, str(exc)[:200]
print("APP_AST_OK", ast_ok, ast_err)
if (not fails) and ast_ok:
    open(os.path.join(BASE, "app.py"), "w", encoding="utf-8", newline="").write(src)
    print("WROTE app.py")

# post checks
for token in ["import random", "_circulation_loop", "/api/circulation/recent", "_federation_waterfall", '"waterfall": wf']:
    present = re.search(re.escape(token), src) is not None or (token.replace('"','').replace("'","")) 
print("TOKENS:", {t: (src.count(t)>=1 if t in ('import random','_circulation_loop','/api/circulation/recent','_federation_waterfall') else True) for t in ["import random","_circulation_loop","/api/circulation/recent","_federation_waterfall"]})
if fails: print("APP_FAILS:", fails)
else:    print("ALL_APP_EDITS_OK")
