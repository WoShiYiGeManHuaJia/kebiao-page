# -*- coding: utf-8 -*-
"""寿命探测：只用 API（快），记录每次成功/失败，测登录态到底活多久"""
import os, json, base64, time, urllib.request, urllib.error, ssl

CK = os.environ.get("CMCC_COOKIE", "")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
PHONE_B64 = base64.b64encode("17274079953".encode()).decode()
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def get(url):
    h = {"User-Agent": UA, "Cookie": CK, "Accept": "application/json,*/*",
         "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://touch.10086.cn/i/"}
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25, context=ctx)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)[:120]

t = time.time()
bj = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t + 8 * 3600))
lines = [f"北京时间: {bj}", f"cookie长度: {len(CK)}"]

targets = [
    ("话费", f"https://touch.10086.cn/i/v1/fee/cusinfomerger/{PHONE_B64}?channel=02"),
    ("流量", f"https://touch.10086.cn/i/v1/cust/flow/flowInfoQry/{PHONE_B64}"),
    ("sysdate", "https://touch.10086.cn/i/v1/res/sysdate/?channel=02"),
    ("numarea", f"https://touch.10086.cn/i/v1/res/numarea/{PHONE_B64}?channel=02"),
]
alive = False
for name, u in targets:
    s, b = get(u)
    ok = '"retCode":"000000"' in b or '"retCode": "000000"' in b
    if name in ("话费", "流量") and ok and len(b) > 200:
        alive = True
    lines.append(f"[{name}] status={s} ok={ok} len={len(b)}")
    lines.append("   " + b[:160].replace("\n", " "))

lines.append("=" * 50)
lines.append("结论: " + ("登录态有效 ✅" if alive else "登录态失效 ❌"))

out = "\n".join(lines)
print(out, flush=True)

# 追加到寿命记录文件（回传）
_tok = os.environ.get("CM_PAT", "")
if _tok:
    _H = {"Authorization": f"token {_tok}", "Accept": "application/vnd.github+json", "User-Agent": "probe"}
    _repo = os.environ.get("GITHUB_REPOSITORY", "")
    path = "probe_life.txt"
    prev = ""
    try:
        _r = _u = urllib.request.Request(f"https://api.github.com/repos/{_repo}/contents/{path}", headers=_H)
        _o = json.load(urllib.request.urlopen(_r, timeout=30))
        prev = base64.b64decode(_o["content"]).decode("utf-8", "ignore")
        sha = _o.get("sha")
    except Exception:
        sha = None
    new = prev + f"\n\n===== {bj} =====\n" + out
    _b = {"message": f"life {bj}", "content": base64.b64encode(new.encode()).decode()}
    if sha: _b["sha"] = sha
    try:
        _r2 = urllib.request.Request(f"https://api.github.com/repos/{_repo}/contents/{path}",
                                     data=json.dumps(_b).encode(),
                                     headers={**_H, "Content-Type": "application/json"}, method="PUT")
        urllib.request.urlopen(_r2, timeout=60)
        print("UPLOADED")
    except Exception as e:
        print("UPLOAD FAIL", str(e)[:120])
