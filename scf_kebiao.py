# -*- coding: utf-8 -*-
"""腾讯云 SCF 云函数：每次访问实时拉取学校课表并返回网页
部署后用 URL 直接访问，刷新即最新数据。
"""

import json
import urllib.request
import urllib.parse

BASE = "http://222.243.161.213:81/hnrjzyxyhd"
USER = "202405190231"
PWD = "QlRZY0NDcTBDRG5uQnZ4TDROSWFzZz09"
SCH = "4711"

DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 0: "周日"}
COLORS = ["#e8f0fe", "#e6f4ea", "#fef7e0", "#fce8e6", "#f3e8fd", "#e0f7fa", "#fff3e0", "#e8f5e9"]


def esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def login():
    q = urllib.parse.urlencode({"userNo": USER, "pwd": PWD, "encode": "1",
                                "captchaData": "", "codeVal": ""})
    j = json.loads(urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/login?{q}", method="POST"), timeout=30).read().decode())
    assert str(j.get("code")) == "1", "登录失败: " + json.dumps(j, ensure_ascii=False)[:200]
    return j["data"]["token"]


def fetch(token):
    req = urllib.request.Request(f"{BASE}/student/curriculum?week=&kbjcmsid=",
                                 headers={"Token": token, "schoolCode": SCH})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def render(cur):
    data = cur["data"][0]
    top = data["topInfo"][0]
    courses = data.get("courses") or []
    parsed = []
    for c in courses:
        s = str(c.get("classTime") or "")
        secs = []
        if len(s) > 1:
            body = s[1:]
            for i in range(0, len(body) - 1, 2):
                secs.append(int(body[i:i + 2], 10))
        weeks = [int(w) for w in str(c.get("classWeekDetails") or "").split(",") if w]
        parsed.append({"day": int(c.get("weekDay", 1) or 1), "secs": secs, "weeks": weeks,
                       "name": c.get("courseName"), "teacher": c.get("teacherName"),
                       "room": c.get("classroomName"), "bld": c.get("buildingName"),
                       "time": f"{c.get('startTime')}-{c.get('endTIme')}"})
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for s in p["secs"]:
            cell[f"{p['day']}-{s}"] = i
    for i, p in enumerate(parsed):
        start[f"{p['day']}-{p['secs'][0]}"] = i
    rows = []
    for sec in range(1, 11):
        td = f'<td class="time">第{sec}节</td>'
        for d in range(1, 6):
            if f"{d}-{sec}" in start:
                p = parsed[start[f"{d}-{sec}"]]
                rs = len(p["secs"])
                w = f"{p['weeks'][0]}-{p['weeks'][-1]}周" if p["weeks"] else ""
                color = COLORS[start[f"{d}-{sec}"] % len(COLORS)]
                td += (f'<td rowspan="{rs}" class="cls" style="background:{color}">'
                       f'<div class="cname">{esc(p["name"])}</div>'
                       f'<div class="cinfo">{esc(p["time"])}</div>'
                       f'<div class="cinfo">{esc(p["bld"])} · {esc(p["room"])}</div>'
                       f'<div class="cinfo">{esc(p["teacher"])} · {esc(w)}</div></td>')
            elif f"{d}-{sec}" not in cell:
                td += "<td></td>"
        rows.append(f"<tr>{td}</tr>")
    info = (f'{esc(top["semesterId"])} · 第{top["week"]}周 / 共{top["maxWeek"]}周 · '
            f'{esc(top["today"])}（{esc(top["weekday"])}）')
    header = "".join(f'<th>{DAY_NAMES[d]}</th>' for d in [1, 2, 3, 4, 5])
    import datetime
    now = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
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
<h1>我的课表·实时</h1>
<div class="sub">{info}</div>
<table><tr><th class="time">节次</th>{header}</tr>{''.join(rows)}</table>
<div class="sub" style="margin-top:10px">每次打开实时从学校接口拉取 · 最后更新 {now}（北京时间）</div>
</body></html>"""


def main_handler(event, context):
    try:
        cur = fetch(login())
        if str(cur.get("code")) != "1":
            raise RuntimeError("课表接口异常: " + json.dumps(cur, ensure_ascii=False)[:200])
        return {
            "statusCode": 200,
            "isBase64Encoded": False,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": render(cur),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": "课表拉取失败: %s" % e,
        }
