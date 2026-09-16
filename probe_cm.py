# -*- coding: utf-8 -*-
"""移动掌厅探测：用新鲜 Cookie 渲染掌厅首页，抓余额/流量/语音 + 真实 API 报文"""
import os, re, json, asyncio, base64

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

print("cookie 项数:", len(cookies))

API_HITS = []

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        ctx = await b.new_context(
            user_agent=UA,
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
        )
        await ctx.add_cookies(cookies)

        async def on_resp(resp):
            u = resp.url
            if "/i/v1/" in u or "/v1/" in u:
                try:
                    txt = await resp.text()
                except Exception:
                    txt = ""
                API_HITS.append({"url": u, "status": resp.status, "body": txt[:1200]})

        pg = await ctx.new_page()
        pg.on("response", lambda r: asyncio.create_task(on_resp(r)))

        for url in ["https://touch.10086.cn/i/mobile/home.html"]:
            try:
                await pg.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                print("goto warn:", str(e)[:100])
            await asyncio.sleep(4)
            print("=" * 60)
            print("URL:", pg.url)
            print("TITLE:", await pg.title())
            txt = await pg.inner_text("body")
            print("BODY-LEN:", len(txt))
            for kw in ["余额", "流量", "语音", "套餐余量", "请登录", "用户登录", "短信随机码"]:
                if kw in txt:
                    print("  HIT-KW:", kw)
            # 抽取数字
            for m in re.finditer(r"([\d.]+)\s*(元|GB|MB|分钟|分)", txt):
                print("  NUM:", m.group(0).strip())
            print("---- 前 800 字 ----")
            print(txt[:800].replace("\n", " | ")[:800])

        print("=" * 60)
        print("API 命中:", len(API_HITS))
        for h in API_HITS[:12]:
            print("-", h["status"], h["url"][:110])
            print("   ", h["body"][:300].replace("\n", " "))
        await b.close()

asyncio.run(main())

with open("probe_out.json", "w") as f:
    json.dump(API_HITS, f, ensure_ascii=False, indent=1)
print("saved probe_out.json")

# ── 回传结果到仓库（用 Actions 自带的 GITHUB_TOKEN）──
import urllib.request as _u
_tok = os.environ.get("GITHUB_TOKEN", "")
if _tok:
    _log = open("probe_log.txt").read() if os.path.exists("probe_log.txt") else "(no log)"
    _payload = _log + "\n\n===== API HITS =====\n" + json.dumps(API_HITS[:12], ensure_ascii=False, indent=1)
    _H = {"Authorization": f"token {_tok}", "Accept": "application/vnd.github+json", "User-Agent": "probe"}
    _repo = os.environ.get("GITHUB_REPOSITORY", "")
    def _put(path, content, msg):
        _b = {"message": msg, "content": base64.b64encode(content.encode()).decode()}
        try:
            _r = _u.Request(f"https://api.github.com/repos/{_repo}/contents/{path}", headers=_H)
            _old = json.load(_u.urlopen(_r, timeout=30))
            if _old.get("sha"): _b["sha"] = _old["sha"]
        except Exception:
            pass
        try:
            _r2 = _u.Request(f"https://api.github.com/repos/{_repo}/contents/{path}",
                             data=json.dumps(_b).encode(), headers={**_H, "Content-Type": "application/json"},
                             method="PUT")
            _u.urlopen(_r2, timeout=60)
            print("UPLOADED", path)
        except Exception as e:
            print("UPLOAD FAIL", path, str(e)[:150])
    _put("probe_result.txt", _payload, "probe result")
