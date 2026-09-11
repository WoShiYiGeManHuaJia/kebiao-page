# -*- coding: utf-8 -*-
"""
路由器定时任务：每小时拉最新课表 -> 推送到 GitHub Pages 公开仓
运行一次即完成"拉取+渲染+推送"，把 index.html 覆盖到 kebiao-page 仓库，
GitHub 自动重建，公网链接 woshiyigemanhuajia.github.io/kebiao-page 更新为最新。
"""
import json, os, time, base64, urllib.request, urllib.parse

# ============ 配置（从本机配置文件读取，安全起见不写死在脚本里） ============
USER_NO = ""
PWD_ENC = ""
SCHOOL_CODE = ""
BASE = "http://222.243.161.213:81/hnrjzyxyhd"
GH_PAT = ""
GH_REPO = "WoShiYiGeManHuaJia/kebiao-page"
GH_PAGE = "index.html"
# ==============================

def _load_kb_conf():
    """从 ~/.kb_conf 读取学校账号配置（每行 key=value）"""
    conf = {}
    for f in ("/root/.kb_conf", os.path.expanduser("~/.kb_conf")):
        if os.path.exists(f):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip("'\"")
    return conf

_kb = _load_kb_conf()
USER_NO = _kb.get("USER_NO", os.environ.get("KB_USER_NO", ""))
PWD_ENC = _kb.get("PWD_ENC", os.environ.get("KB_PWD_ENC", ""))
SCHOOL_CODE = _kb.get("SCHOOL_CODE", os.environ.get("KB_SCHOOL_CODE", ""))
if not (USER_NO and PWD_ENC and SCHOOL_CODE):
    raise SystemExit("未找到学校账号配置：请写入 ~/.kb_conf（格式 USER_NO=.. PWD_ENC=.. SCHOOL_CODE=..），或用环境变量 KB_USER_NO/KB_PWD_ENC/KB_SCHOOL_CODE")

token_file = os.path.expanduser("~/.gh_token")
if os.path.exists(token_file):
    GH_PAT = open(token_file).read().strip()
elif os.environ.get("GH_TOKEN"):
    GH_PAT = os.environ["GH_TOKEN"]
if not GH_PAT:
    raise SystemExit("未找到 GitHub token：请写入 ~/.gh_token 或设置 GH_TOKEN 环境变量")

DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 0: "周日"}
COLORS = ["#e8f0fe", "#e6f4ea", "#fef7e0", "#fce8e6", "#f3e8fd", "#e0f7fa", "#fff3e0", "#e8f5e9"]


def esc(s):
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def http_get(url, headers=None, data=None, method=None, timeout=15):
    if isinstance(url, urllib.request.Request):
        req = url
    else:
        req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def login_and_get_token():
    q = urllib.parse.urlencode({"userNo": USER_NO, "pwd": PWD_ENC, "encode": "1",
                                "captchaData": "", "codeVal": ""})
    raw = http_get(BASE + "/login?" + q, method="POST", timeout=15).decode("utf-8", "ignore")
    j = json.loads(raw)
    if str(j.get("code")) != "1":
        raise RuntimeError("登录失败: " + raw[:300])
    return j["data"]["token"]


def fetch_curriculum(token, week=""):
    raw = http_get(BASE + "/student/curriculum?week=&kbjcmsid=",
                   headers={"Token": token, "schoolCode": SCHOOL_CODE}, timeout=15)
    return json.loads(raw.decode("utf-8", "ignore"))


def render_html(cur):
    data = cur["data"][0]
    top = data["topInfo"][0]
    courses = data.get("courses") or []
    parsed = []
    for c in courses:
        s = str(c.get("classTime") or "")
        secs = []
        if len(s) > 1:
            for i in range(0, len(s) - 1, 2):
                try:
                    secs.append(int(s[i + 1:i + 3]))
                except ValueError:
                    pass
        weeks = [int(x) for x in str(c.get("classWeekDetails") or "").split(",") if x]
        day = int(c.get("weekDay") or 1)
        parsed.append({"day": day, "secs": secs, "weeks": weeks,
                       "name": c.get("courseName", ""), "teacher": c.get("teacherName", ""),
                       "room": c.get("classroomName", ""), "bld": c.get("buildingName", ""),
                       "time": f"{c.get('startTime','')}-{c.get('endTIme','')}"})
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for sec in p["secs"]:
            cell[(p["day"], sec)] = i
        if p["secs"]:
            start[(p["day"], p["secs"][0])] = i
    rows = []
    for sec in range(1, 11):
        td = f'<td class="time">第{sec}节</td>'
        for d in range(1, 6):
            if (d, sec) in start:
                p = parsed[start[(d, sec)]]
                rs = len(p["secs"]) if len(p["secs"]) > 0 else 1
                w = f'{p["weeks"][0]}-{p["weeks"][-1]}周' if p["weeks"] else ""
                color = COLORS[start[(d, sec)] % len(COLORS)]
                td += (f'<td rowspan="{rs}" class="cls" style="background:{color}"><div class="cname">{esc(p["name"])}</div>'
                       f'<div class="cinfo">{esc(p["time"])}</div>'
                       f'<div class="cinfo">{esc(p["bld"])}·{esc(p["room"])}</div>'
                       f'<div class="cinfo">{esc(p["teacher"])} · {esc(w)}</div></td>')
            elif (d, sec) not in cell:
                td += "<td></td>"
        rows.append(f"<tr>{td}</tr>")
    weeks_info = ', '.join(str(x) for x in sorted(set(w for p in parsed for w in p["weeks"]) or [1]))
    info = (f'{esc(top.get("semesterId",""))} · 第{top.get("week","")}周 / 共{top.get("maxWeek","")}周 · '
            f'{esc(top.get("today",""))}（{esc(top.get("weekday",""))}） · 覆盖第{esc(weeks_info)}周')
    header = "".join(f"<th>{DAY_NAMES[d]}</th>" for d in range(1, 6))
    now = time.strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>我的课表·实时</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f5f7fa;padding:14px;color:#222}}
h1{{font-size:20px;text-align:center;margin:6px 0 2px}}
.sub{{text-align:center;color:#888;font-size:13px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th,td{{border:1px solid #d5dbe3;padding:6px 4px;vertical-align:middle;text-align:center;font-size:13px}}
th{{background:#2b5aa0;color:#fff;font-weight:600}}
.time{{font-size:11px;color:#444;background:#eceff4;width:56px}}
.cname{{font-weight:700;font-size:13px;color:#1a3a6b}}
.cinfo{{font-size:11px;color:#555;margin-top:2px;line-height:1.45}}
@media(min-width:720px){{body{{max-width:900px;margin:0 auto}}.cname{{font-size:15px}}}}</style></head><body>
<h1>我的课表·实时</h1><div class="sub">{info}</div>
<table><tr><th class="time">节次</th>{header}</tr>{''.join(rows)}</table>
<div class="sub" style="margin-top:10px">数据来源：学校接口 · 自动更新于 {now} · 网页托管 GitHub Pages</div>
</body></html>"""


def push_to_github(html):
    # 1) 取当前 index.html 的 sha
    req = urllib.request.Request(f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PAGE}")
    req.add_header("Authorization", f"token {GH_PAT}")
    req.add_header("Accept", "application/vnd.github+json")
    cur = json.loads(http_get(req, timeout=20).decode())
    old_sha = cur.get("sha")

    # 2) 比对内容，无变化则跳过，避免无意义 commit
    if base64.b64decode(cur.get("content") or "").decode() == html:
        print("内容无变化，跳过推送")
        return "no-change"

    # 3) 上传新内容
    body = {
        "message": "auto-update " + time.strftime("%Y-%m-%d %H:%M"),
        "content": base64.b64encode(html.encode("utf-8")).decode(),
        "sha": old_sha,
    }
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PAGE}",
        data=json.dumps(body).encode(), method="PUT")
    req.add_header("Authorization", f"token {GH_PAT}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    resp = json.loads(http_get(req, timeout=30).decode())
    return "pushed:" + resp.get("commit", {}).get("sha", "?")


if __name__ == "__main__":
    try:
        token = login_and_get_token()
        cur = fetch_curriculum(token)
        if str(cur.get("code")) != "1":
            raise RuntimeError("课表接口异常: " + json.dumps(cur, ensure_ascii=False)[:200])
        html = render_html(cur)
        result = push_to_github(html)
        print("OK", result)
    except Exception as e:
        print("ERR", e)
