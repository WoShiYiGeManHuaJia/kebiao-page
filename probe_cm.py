# -*- coding: utf-8 -*-
"""移动掌厅探测 v2：渲染取明文 + 试明文号码直调 API + 记录登录态寿命"""
import os, re, json, asyncio, base64, time

CK = os.environ.get("CMCC_COOKIE", "")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

cookies = []
for part in CK.split(";"):
    part = part.strip()
    if not part or "=" not in part:
        continue
    n, v = part.split("=", 1)
    cookies.append({"name": n.strip(), "value": v, "domain": ".10086.cn", "path": "/"})

OUT = []
def P(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)

P("cookie 项数:", len(cookies))
P("运行时刻 UTC:", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))

# 已知加密 ID（URL 里那个）
ENC_ID = "RGhxY25xZklxVGQxd1BzVjRHdUhWUT09"
P("加密串第一层 base64 解:", base64.b64decode(ENC_ID + "==").decode("utf-8", "ignore"))

# 明文手机号 -> base64
PHONE = "17274079953"
PHONE_B64 = base64.b64encode(PHONE.encode()).decode()
P("明文号码 base64:", PHONE_B64)

API_HITS = []

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, locale="zh-CN")
        await ctx.add_cookies(cookies)

        async def on_resp(resp):
            u = resp.url
            if "/i/v1/" in u:
                try: txt = await resp.text()
                except Exception: txt = ""
                API_HITS.append({"url": u, "status": resp.status, "body": txt[:600]})

        pg = await ctx.new_page()
        pg.on("response", lambda r: asyncio.create_task(on_resp(r)))

        try:
            await pg.goto("https://touch.10086.cn/i/mobile/home.html", wait_until="networkidle", timeout=60000)
        except Exception as e:
            P("goto warn:", str(e)[:80])
        await asyncio.sleep(5)

        P("=" * 60)
        P("FINAL URL:", pg.url)
        P("TITLE:", await pg.title())
        txt = await pg.inner_text("body")
        P("BODY-LEN:", len(txt))
        for kw in ["余额", "流量", "语音", "套餐余量", "请登录", "用户登录", "短信随机码", "积分"]:
            if kw in txt: P("  HIT-KW:", kw)
        P("---- 数字抽取 ----")
        for m in re.finditer(r"([\d.]+\s*(?:元|GB|MB|分钟|分))", txt):
            P("  NUM:", m.group(1).strip())
        P("---- 页面文本前 1500 ----")
        P(txt[:1500].replace("\n", " | "))

        # 用明文号码 base64 直调 API（在页面上下文里，带 cookie 同源）
        P("=" * 60)
        P("---- 明文号码直调测试 ----")
        for name, path in [
            ("话费 cusinfomerger", f"/i/v1/fee/cusinfomerger/{PHONE_B64}?channel=02"),
            ("流量 flowInfoQry", f"/i/v1/cust/flow/flowInfoQry/{PHONE_B64}"),
            ("加密串对照 cusinfomerger", f"/i/v1/fee/cusinfomerger/{ENC_ID}?channel=02"),
        ]:
            try:
                r = await pg.evaluate("""async (u) => {
                    const resp = await fetch(u, {credentials:'include'});
                    const t = await resp.text();
                    return {status: resp.status, body: t.slice(0,400)};
                }""", path)
                P(f"[{name}] status={r['status']}")
                P("   ", r["body"][:350])
            except Exception as e:
                P(f"[{name}] ERR", str(e)[:100])

        # 再看余量页
        try:
            await pg.goto("https://touch.10086.cn/i/mobile/home.html", wait_until="networkidle", timeout=45000)
            await asyncio.sleep(3)
            html = await pg.content()
            for kw in ["剩余", "余量", "已用", "通用流量", "语音"]:
                idxs = [m.start() for m in re.finditer(kw, html)][:2]
                for i in idxs:
                    seg = re.sub(r"<[^>]+>", " ", html[max(0,i-80):i+120])
                    P(f"  [{kw}]", " ".join(seg.split())[:150])
        except Exception as e:
            P("第二遍 warn:", str(e)[:80])

        await b.close()

asyncio.run(main())

P("=" * 60)
P("API 命中数:", len(API_HITS))
ok = [h for h in API_HITS if '"retCode":"000000"' in h["body"] or '"retCode": "000000"' in h["body"]]
P("其中 000000 成功:", len(ok), "/", len(API_HITS))
for h in API_HITS[:10]:
    P("-", h["status"], h["url"][:100])

# 回传
import urllib.request as _u
_tok = os.environ.get("CM_PAT", "")
if _tok:
    _H = {"Authorization": f"token {_tok}", "Accept": "application/vnd.github+json", "User-Agent": "probe"}
    _repo = os.environ.get("GITHUB_REPOSITORY", "")
    _payload = "\n".join(OUT) + "\n\n===== API =====\n" + json.dumps(API_HITS[:14], ensure_ascii=False, indent=1)
    _b = {"message": "probe2 result", "content": base64.b64encode(_payload.encode()).decode()}
    try:
        _r = _u.Request(f"https://api.github.com/repos/{_repo}/contents/probe_result2.txt", headers=_H)
        _o = json.load(_u.urlopen(_r, timeout=30))
        if _o.get("sha"): _b["sha"] = _o["sha"]
    except Exception: pass
    try:
        _r2 = _u.Request(f"https://api.github.com/repos/{_repo}/contents/probe_result2.txt",
                         data=json.dumps(_b).encode(), headers={**_H, "Content-Type": "application/json"}, method="PUT")
        _u.urlopen(_r2, timeout=60); print("UPLOADED")
    except Exception as e: print("UPLOAD FAIL", str(e)[:150])
