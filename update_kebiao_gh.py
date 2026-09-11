# -*- coding: utf-8 -*-
"""
路由器定时任务：一次性拉取本学期 1~20 周课表 -> 生成带"周选择下拉框"的页面
-> 推送到 GitHub Pages 公开仓。浏览器打开链接后，可自选任意周查看（纯前端切换，无网络请求）。
运行一次即完成"拉取全部周+渲染+推送"。
"""
import json, os, sys, time, base64, subprocess, urllib.request, urllib.parse

# ============ 配置（从本机配置文件读取，安全起见不写死在脚本里） ============
USER_NO = ""
PWD_ENC = ""
SCHOOL_CODE = ""
BASE = "http://222.243.161.213:81/hnrjzyxyhd"
GH_PAT = ""
GH_REPO = "WoShiYiGeManHuaJia/kebiao-page"
GH_PAGE = "index.html"
MAX_WEEK = 20
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
    raw = http_get(BASE + f"/student/curriculum?week={week}&kbjcmsid=",
                   headers={"Token": token, "schoolCode": SCHOOL_CODE}, timeout=15)
    return json.loads(raw.decode("utf-8", "ignore"))


def parse_courses(data):
    """把某一周的课程列表解析为精简结构：{day, secs, name, teacher, room, bld, time}"""
    parsed = []
    for c in (data.get("courses") or []):
        s = str(c.get("classTime") or "")
        secs = []
        if len(s) > 1:
            for i in range(0, len(s) - 1, 2):
                try:
                    secs.append(int(s[i + 1:i + 3]))
                except ValueError:
                    pass
        parsed.append({"day": int(c.get("weekDay") or 1), "secs": secs,
                       "name": c.get("courseName", ""), "teacher": c.get("teacherName", ""),
                       "room": c.get("classroomName", ""), "bld": c.get("buildingName", ""),
                       "time": f"{c.get('startTime','')}-{c.get('endTIme','')}"})
    return parsed


def load_all_weeks(token):
    """拉取本学期 1~MAX_WEEK 周，返回 {week_str: [parsed...]} 与学期信息"""
    weeks = {}
    semester = ""
    current_week = 1
    for w in range(1, MAX_WEEK + 1):
        try:
            cur = fetch_curriculum(token, week=str(w))
            if str(cur.get("code")) == "1":
                data = cur["data"][0]
                top = data["topInfo"][0]
                if w == 1:
                    semester = top.get("semesterId", "")
                    try:
                        current_week = int(top.get("week") or 1)
                    except ValueError:
                        current_week = 1
                weeks[str(w)] = parse_courses(data)
            else:
                weeks[str(w)] = []
        except Exception:
            weeks[str(w)] = []
    return weeks, semester, current_week


def table_html(parsed):
    """由某周课程列表渲染出表格 HTML"""
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for sec in p["secs"]:
            cell[(p["day"], sec)] = i
        if p["secs"]:
            start[(p["day"], p["secs"][0])] = i
    rows = []
    for sec in range(1, 11):
        td = f'<td class="time">第{sec}节</td>'
        for d in range(1, 7):
            if (d, sec) in start:
                p = parsed[start[(d, sec)]]
                rs = max(len(p["secs"]), 1)
                color = COLORS[start[(d, sec)] % len(COLORS)]
                td += (f'<td rowspan="{rs}" class="cls" style="background:{color}"><div class="cname">{esc(p["name"])}</div>'
                       f'<div class="cinfo">{esc(p["time"])}</div>'
                       f'<div class="cinfo">{esc(p["bld"])}·{esc(p["room"])}</div>'
                       f'<div class="cinfo">{esc(p["teacher"])}</div></td>')
            elif (d, sec) not in cell:
                td += "<td></td>"
        rows.append(f"<tr>{td}</tr>")
    header = "".join(f"<th>{DAY_NAMES[d]}</th>" for d in range(1, 7))
    return f'<table><tr><th class="time">节次</th>{header}</tr>{"".join(rows)}</table>'


def render_page(weeks, semester, current_week):
    """生成整页：自定义下拉弹窗 + 每周一个隐藏表格面板 + 切换 JS"""
    opts = "".join(f'<button class="opt{" on" if w == current_week else ""}" data-wk="{w}" onclick="pick(event,{w})">{w}</button>'
                   for w in range(1, MAX_WEEK + 1))
    panes = []
    for w in range(1, MAX_WEEK + 1):
        parsed = weeks.get(str(w), [])
        content = table_html(parsed) if parsed else '<div class="empty">本周暂无课程安排</div>'
        panes.append(f'<div class="pane" data-wk="{w}" style="display:none">{content}</div>')
    now = time.strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>我的课表</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f5f7fa;padding:14px;color:#222}}
h1{{font-size:20px;text-align:center;margin:6px 0 2px}}
.sub{{text-align:center;color:#888;font-size:13px;margin-bottom:10px}}
.pick{{text-align:center;margin:12px 0;position:relative;display:inline-block}}
.pick .btn{{font-size:14px;padding:7px 16px;border:none;border-radius:14px;background:linear-gradient(135deg,#5a8de1,#7fb3f0);color:#fff;font-weight:600;outline:none;box-shadow:0 3px 8px rgba(90,141,225,.32);cursor:pointer;display:inline-block}}
.pick .btn:active{{transform:scale(.97)}}
.ddpanel{{position:absolute;top:calc(100% + 8px);left:50%;transform:translateX(-50%);background:#fff;border-radius:14px;box-shadow:0 8px 24px rgba(30,60,110,.18);padding:10px;z-index:99;display:none;width:290px}}
.ddpanel.show{{display:block}}
.ddpanel .gtitle{{font-size:12px;color:#8a97ab;text-align:center;margin-bottom:8px;font-weight:600}}
.ddgrid{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}}
.opt{{font-size:13px;padding:6px 0;border:none;border-radius:9px;background:#eef3fb;color:#1a3a6b;font-weight:600;cursor:pointer;text-align:center;transition:background .15s,color .15s}}
.opt:hover{{background:#5a8de1;color:#fff}}
.opt.on{{background:linear-gradient(135deg,#5a8de1,#7fb3f0);color:#fff;box-shadow:0 2px 6px rgba(90,141,225,.35)}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;margin:0 auto}}
th,td{{border:1px solid #d5dbe3;padding:6px 4px;vertical-align:middle;text-align:center;font-size:13px}}
th{{background:#2b5aa0;color:#fff;font-weight:600}}
.time{{font-size:11px;color:#444;background:#eceff4;width:56px}}
.cname{{font-weight:700;font-size:13px;color:#1a3a6b}}
.cinfo{{font-size:11px;color:#555;margin-top:2px;line-height:1.45}}
.empty{{text-align:center;color:#999;padding:40px 0;font-size:15px}}
.pane{{display:none}}
@media(min-width:720px){{body{{max-width:900px;margin:0 auto}}.cname{{font-size:15px}}</style></head><body>
<h1>我的课表</h1><div class="sub">{esc(semester)} · 共 {MAX_WEEK} 周</div>
<div style="text-align:center"><span class="pick">
<button class="btn" id="wkBtn" onclick="toggleDd(event)">第 {current_week} 周 ▾</button>
<div class="ddpanel" id="ddPanel"><div class="gtitle">选择周次</div><div class="ddgrid">{opts}</div></div>
</span></div>
<div id="panes">{''.join(panes)}</div>
<div class="sub" style="margin-top:12px">数据来源：学校接口 · 更新于 {now} · 托管 GitHub Pages</div>
<script>
function showWeek(n) {{
  var panes = document.querySelectorAll('.pane');
  for (var i = 0; i < panes.length; i++) {{
    panes[i].style.display = (panes[i].getAttribute('data-wk') === String(n)) ? 'block' : 'none';
  }}
  document.getElementById('wkBtn').innerHTML = '第 ' + n + ' 周 ▾';
  var opts = document.querySelectorAll('.opt');
  for (var j = 0; j < opts.length; j++) {{
    opts[j].className = 'opt' + ((parseInt(opts[j].getAttribute('data-wk')) === n) ? ' on' : '');
  }}
  document.getElementById('ddPanel').classList.remove('show');
}}
function toggleDd(e) {{ e.stopPropagation(); document.getElementById('ddPanel').classList.toggle('show'); }}
function pick(e, n) {{ e.stopPropagation(); showWeek(n); }}
document.addEventListener('click', function() {{ document.getElementById('ddPanel').classList.remove('show'); }});
showWeek({current_week});
</script>
</body></html>"""


def gh_api(method, path, body=None, timeout=30):
    """GitHub API 用系统 curl 发送（路由器的 Python 缺 https 支持，curl 自带）"""
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method,
           "-H", f"Authorization: token {GH_PAT}",
           "-H", "Accept: application/vnd.github+json",
           "-H", "User-Agent: kebiao-updater"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    cmd.append(f"https://api.github.com{path}")
    p = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout + 5)
    if p.returncode != 0:
        raise RuntimeError("curl 失败(%s): %s" % (p.returncode, p.stderr.decode("utf-8", "ignore")[:200]))
    out = p.stdout.decode("utf-8", "ignore")
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError("GitHub 返回非 JSON: " + out[:200])


def push_to_github(html):
    cur = gh_api("GET", f"/repos/{GH_REPO}/contents/{GH_PAGE}")
    old_sha = cur.get("sha")
    if base64.b64decode(cur.get("content") or "").decode() == html:
        print("内容无变化，跳过推送")
        return "no-change"
    body = {
        "message": "auto-update " + time.strftime("%Y-%m-%d %H:%M"),
        "content": base64.b64encode(html.encode("utf-8")).decode(),
        "sha": old_sha,
    }
    resp = gh_api("PUT", f"/repos/{GH_REPO}/contents/{GH_PAGE}", body)
    return "pushed:" + resp.get("commit", {}).get("sha", "?")


if __name__ == "__main__":
    try:
        token = login_and_get_token()
        weeks, semester, cur_wk = load_all_weeks(token)
        html = render_page(weeks, semester, cur_wk)
        with open("/tmp/kebiao_preview.html", "w", encoding="utf-8") as f:
            f.write(html)  # 调试预览
        result = push_to_github(html)
        print("OK", result, f"(默认第{cur_wk}周)")
    except Exception as e:
        print("ERR", e)
