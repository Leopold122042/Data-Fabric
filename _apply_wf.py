# -*- coding: utf-8 -*-
import ast, os
def rd(p):
    s = open(os.path.join(BASE, p), encoding="utf-8", newline="").read()
    return s.lstrip("\ufeff")
def norm(s):  # force uniform CRLF (files are CRLF) and trim ends
    return s.replace("\r\n", "\n").replace("\n", "\r\n").strip()

BASE = os.path.dirname(os.path.abspath(__file__))

# ================= app.py =================
src0 = rd("app.py"); src = src0; fails = []
def sub(name, old, new):
    global src
    o = norm(old) if ("\n" in old or "\r" in old) else old
    n = norm(new) if ("\n" in new or "\r" in new) else new
    c = src.count(o)
    if c != 1: fails.append((name, "count=%d (expected 1)" % c)); return False
    src = src.replace(o, n); print("PASS", name); return True

sub("E1_import_random",
    "import sqlite3\nimport threading\nimport time",
    "import sqlite3\nimport random\nimport threading\nimport time")

sub("C_csi_tail",
    '    with SERVICE_STATS_LOCK:\n        st = SERVICE_STATS.setdefault(sid, {"calls": 0, "total_ms": 0.0})\n        st["calls"] += 1; st["total_ms"] += ms\n    return {"latency_ms": ms, "result": result}',
    '    with SERVICE_STATS_LOCK:\n        st = SERVICE_STATS.setdefault(sid, {"calls": 0, "total_ms": 0.0})\n        st["calls"] += 1; st["total_ms"] += ms\n'
    '    try:\n        gov.record_service(sid, latency_ms=ms, success=True, actor="orchestrator")   # \u7f16\u6392\u5185\u90e8\u8c03\u7528\u8ba1\u5165\u7b2c\u4e09\u90e8\u5206\u6d41\u901a\u8ba1\u91cf\uff08\u4e0d\u53d1 service_invoked\uff0c\u907f\u514d\u95ed\u73af\u81ea\u6fc0\uff09\n'
    '    except Exception:\n        pass\n    return {"latency_ms": ms, "result": result}')

sub("E4_legacy_return",
    '    return {"service_id": sid, "latency_ms": ms, "stats_after": {"calls": calls, "avg_ms": avg},\n            "error": err, "result": result}',
    '    wf = _federation_waterfall(sid, result) if err is None else None\n'
    '    return {"service_id": sid, "latency_ms": ms, "stats_after": {"calls": calls, "avg_ms": avg},\n            "error": err, "result": result, "waterfall": wf}')

sub("E5_gov_mask",
    '    if auth["masked"]:\n        result = gov.mask_payload(result)',
    '    wf = _federation_waterfall(sid, result) if err is None else None   # \u6eaf\u6e90\u7011\u5e03\uff1a\u57fa\u4e8e\u672a\u8131\u654f\u539f\u59cb\u7ed3\u6784\u63d0\u53d6\u5404\u6e90\u8017\u65f6\n'
    '    if auth["masked"]:\n        result = gov.mask_payload(result)')

sub("E6_gov_return",
    '            "error": err, "security": auth, "circulation": circulation, "result": result}',
    '            "error": err, "security": auth, "circulation": circulation, "waterfall": wf, "result": result}')

# F: replace _svc_fed_query region with helper + instrumented function
marker = 'def _svc_fed_query(group_by="dept", top_n=8):'
endmark = '@app.get("/api/services")'
s = src.find(marker); e = src.find(endmark, s if s >= 0 else 0)
if s < 0 or e <= s:
    fails.append(("F_fedquery", "markers not found (s=%d e=%d)" % (s, e)))
else:
    block = norm(rd("_n_fedquery.txt"))
    src = src[:s] + block + "\r\n\r\n" + src[e:]
    print("PASS F_fedquery")

# G: insert circulation ticker before startup event
smk = '@app.on_event("startup")'
si = src.find(smk)
if si < 0:
    fails.append(("G_circ", "no @app.on_event marker"))
else:
    head = src[:si]
    if not head.endswith("\r\n\r\n"):
        head = head.rstrip() + "\r\n"
    circ = norm(rd("_n_circ.txt"))
    src = head + circ + "\r\n\r\n" + src[si:]
    print("PASS G_circ")

# H: start circulation thread alongside patrol
sub("H_thread",
    '    threading.Thread(target=_patrol_loop, daemon=True).start()',
    '    threading.Thread(target=_patrol_loop, daemon=True).start()\n'
    '    threading.Thread(target=_circulation_loop, daemon=True).start()')

# I: insert /api/circulation/recent before flows run route
emk = '@app.post("/api/data-services/flows/{flow_id}/run")'
ei = src.find(emk)
if ei < 0:
    fails.append(("I_endpoint", "no flows-run marker"))
else:
    ep = norm(rd("_n_endpoint.txt"))
    src = src[:ei] + ep + "\r\n\r\n" + src[ei:]
    print("PASS I_endpoint")

ast_ok = True; ast_err = ""
try:
    if not fails: ast.parse(src)
except Exception as exc:
    ast_ok = False; ast_err = str(exc)[:200]
print("APP_AST_OK", ast_ok, ast_err)
if (not fails) and ast_ok:
    open(os.path.join(BASE, "app.py"), "w", encoding="utf-8", newline="").write(src)
    print("WROTE app.py")

# ================= static/index.html =================
h0 = rd("static/index.html"); h = h0; hfails = []
def hsub(name, old, new):
    global h
    o = norm(old) if ("\n" in old or "\r" in old) else old
    n = norm(new) if ("\n" in new or "\r" in new) else new
    c = h.count(o)
    if c != 1: hfails.append((name, "count=%d (expected 1)" % c)); return False
    h = h.replace(o, n); print("PASS", name); return True

hsub("H1_svc_container", rd("_o_svc_json.txt"), rd("_n_svc_container.txt"))
hsub("H4_dfout_container", rd("_o_dfout.txt"), rd("_n_dfout.txt"))
hsub("H3_regcard_ticker", rd("_o_regcard.txt"), rd("_n_regcard_new.txt"))

hsub("H2_invokeSvc_render",
    '''  $("#svcJson").textContent=r.error?`ERROR: ${r.error}`:JSON.stringify(r,null,1);''',
    '  if(!r.error){ renderWaterfall(r.waterfall||[], "svcWaterfall"); renderProvenance((typeof r.result==="string")?r.result:((r.result&&r.result.provenance)||""), "svcProvenance"); } else { var w=$("#svcWaterfall"),p=$("#svcProvenance"); if(w)w.style.display="none"; if(p)p.style.display="none"; }\n'
    '''  $("#svcJson").textContent=r.error?`ERROR: ${r.error}`:JSON.stringify(r,null,1);''')

hsub("H5_runGovernedSvc",
    'const r=await (await fetch("/api/data-services/"+sid+"/invoke",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})).json(); $("#dfOut").style.display="block";',
    'const r=await (await fetch("/api/data-services/"+sid+"/invoke",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})).json(); renderWaterfall(r.waterfall||[], "dfWaterfall"); renderProvenance((r.result&&r.result.provenance)||"", "dfProvenance"); $("#dfOut").style.display="block";')

hsub("H6_runFlow",
    'cross_org:true,params:{patient_id:"P0315",top_n:5}})})).json(); $("#dfOut").style.display="block";',
    'cross_org:true,params:{patient_id:"P0315",top_n:5}})})).json(); (function(){var st=(r.stages||[]),wf=null,prov="";for(var i=0;i<st.length;i++){if(st[i].result&&st[i].result.waterfall&&!wf)wf=st[i].result.waterfall;if(st[i].result&&st[i].result.provenance)prov=st[i].result.provenance;}renderWaterfall(wf||[],"dfWaterfall");renderProvenance(prov||(r.flow?(r.flow.name+":"+(r.flow.service_ids||[]).join(" -> ")):""),"dfProvenance");})(); $("#dfOut").style.display="block";')

hsub("H7_append_js",
    'async function loadDataServices(){ try{ DF=await (await fetch("/api/data-services")).json(); renderDfMetrics(); renderDfServices(); renderDfFlows(); renderDfCirculation(); }catch(e){} }',
    'async function loadDataServices(){ try{ DF=await (await fetch("/api/data-services")).json(); renderDfMetrics(); renderDfServices(); renderDfFlows(); renderDfCirculation(); }catch(e){} }\n' + rd("_n_js_wf.txt").strip("\r\n"))

checks = {
    "dfTicker": h.count('id="dfTicker"'),
    "svcWaterfall": h.count('id="svcWaterfall"'),
    "dfWaterfall": h.count('id="dfWaterfall"'),
    "renderWaterfall_def": h.count("window.renderWaterball".replace("waterball","waterfall")),
}
print("HTML_CHECKS", checks)
if (not hfails):
    open(os.path.join(BASE, "static/index.html"), "w", encoding="utf-8", newline="").write(h)
    print("WROTE static/index.html")

# ---- summary ----
if fails:  print("APP_FAILS:", fails)
else:      print("APP_OK no failures")
if hfails: print("HTML_FAILS:", hfails)
else:      print("HTML_OK no failures")
