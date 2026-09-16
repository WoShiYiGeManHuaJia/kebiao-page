# -*- coding: utf-8 -*-
"""
路由器定时任务：一次性拉取本学期 1~20 周课表 -> 生成带"周选择下拉框"的页面
-> 推送到 GitHub Pages 公开仓。浏览器打开链接后，可自选任意周查看（纯前端切换，无网络请求）。
运行一次即完成"拉取全部周+渲染+推送"。
"""
import json, os, sys, time, base64, subprocess, urllib.request, urllib.parse
from datetime import date, timedelta

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

DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日", 0: "周日"}

# ────────────────────────────────────────────────────────────────
# 2026-2027-1 学期放假调休规则
#   本段为人工确认的固定安排，自动更新只会应用它、不会改动它。
#   如需变更（改日期/取消），须由本人明确要求后再改。
# ────────────────────────────────────────────────────────────────
HOLIDAY_RULES = {
    "enabled": True,
    # 放假区间（含首含尾），区间内的日期一律不上课
    "holidays": [("2026-09-25", "2026-10-07")],
    # 调课补课：「实际日期」上「源日期」那天的课
    "makeup": {
        "2026-09-20": "2026-09-28",   # 周日 补 周一
        "2026-10-10": "2026-09-29",   # 周六 补 周二
        "2026-10-17": "2026-09-30",   # 周六 补 周三
        "2026-10-24": "2026-10-06",   # 周六 补 周二
        "2026-10-31": "2026-10-07",   # 周六 补 周三
    },
}


def _kb_date(sv):
    y, m, d = (int(x) for x in str(sv).split("-"))
    return date(y, m, d)


def apply_holiday_rules(weeks, w1mon):
    """按真实日期重排每周课程。

    规则：
      1. 落在放假区间的日期 -> 当天无课（整周若全在假期内则该周为空）
      2. 属于调课日的日期    -> 当天改上「源日期」那天的课
      3. 其余日期            -> 保持该周该星期的原始课程
    返回 (新的 weeks, 每周调休说明 dict)
    """
    if not HOLIDAY_RULES.get("enabled"):
        return weeks, {}

    hol = [(_kb_date(a), _kb_date(b)) for a, b in HOLIDAY_RULES.get("holidays") or []]
    mk = {}
    for k, v in (HOLIDAY_RULES.get("makeup") or {}).items():
        try:
            mk[_kb_date(k)] = _kb_date(v)
        except Exception:
            pass

    def is_holiday(D):
        return any(a <= D <= b for a, b in hol)

    def courses_on(D):
        """取某个真实日期原本（未调休前）的课程"""
        delta = (D - w1mon).days
        wk = delta // 7 + 1
        wd = delta % 7 + 1          # 1=周一 ... 7=周日
        if wk < 1 or wk > MAX_WEEK:
            return []
        return [dict(p) for p in (weeks.get(str(wk)) or []) if int(p.get("day", 0)) == wd]

    new_weeks, notes = {}, {}
    for w in range(1, MAX_WEEK + 1):
        mon = w1mon + timedelta(weeks=w - 1)
        rows, wk_notes = [], []
        for i in range(7):                      # 周一..周日
            D = mon + timedelta(days=i)
            wd = i + 1
            if is_holiday(D):
                continue                        # 放假：当天无课
            if D in mk:
                src = mk[D]
                for p in courses_on(src):
                    q = dict(p)
                    q["day"] = wd               # 挪到实际这一天的列
                    rows.append(q)
                wk_notes.append("%d/%d 上 %d/%d 的课" % (D.month, D.day, src.month, src.day))
            else:
                rows.extend(courses_on(D))
        new_weeks[str(w)] = rows
        if wk_notes:
            notes[str(w)] = "；".join(wk_notes)
    return new_weeks, notes

# 时段划分（依据实际作息：上午 08:00-11:40 / 下午 14:30-18:10 / 晚上 19:30-21:10）
def seg_of(sec):
    if sec <= 4:
        return "am", "上午"
    if sec <= 8:
        return "pm", "下午"
    return "nt", "晚上"
# 清新马卡龙：低饱和粉彩，柔和耐看，同屏易区分
COLORS = ["#dbeafe", "#dcfce7", "#fef3c7", "#ffe4e6", "#ede9fe", "#cffafe", "#ffedd5", "#e0f2fe"]
# 每门课的深色主色（左侧色条 / 弹窗头部），与上面浅色一一对应
COLOR_TINTS = {"#dbeafe": "#3b82f6", "#dcfce7": "#22c55e", "#fef3c7": "#f59e0b", "#ffe4e6": "#f43f5e",
               "#ede9fe": "#8b5cf6", "#cffafe": "#06b6d4", "#ffedd5": "#f97316", "#e0f2fe": "#0ea5e9"}


def esc(s):
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# 访问学校接口的 UA —— 必须伪装成正常浏览器。
# urllib 默认是 "Python-urllib/3.x"，这是个一眼机器人的独立指纹：
# 路由器下真实设备的 UA 若已统一伪装，唯独脚本露出一个 Python 指纹，
# 检测系统就会把它当成「第 N 个设备」，从而判定多终端共享。
# 如需与路由器上已有的伪装 UA 完全一致，改这一行即可。
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def http_get(url, headers=None, data=None, method=None, timeout=15):
    if isinstance(url, urllib.request.Request):
        req = url
    else:
        h = {"User-Agent": UA_BROWSER}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h, data=data, method=method)
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


def _hol_cls(week_monday, day_no):
    """某周第 day_no 天(1=周一)若落在放假区间，返回 class 属性用于标注「放假」空列"""
    if not week_monday or not HOLIDAY_RULES.get("enabled"):
        return ""
    try:
        D = week_monday + timedelta(days=day_no - 1)
    except Exception:
        return ""
    for a, b in (HOLIDAY_RULES.get("holidays") or []):
        try:
            if _kb_date(a) <= D <= _kb_date(b):
                return ' class="hol"'
        except Exception:
            continue
    return ""


def table_html(parsed, week_monday=None):
    """由某周课程列表渲染出表格 HTML"""
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for sec in p["secs"]:
            cell[(p["day"], sec)] = i
        if p["secs"]:
            start[(p["day"], p["secs"][0])] = i
    # 列数：固定周一~周日 7 列。
    # 之前按课程动态取 6/7 列，会和顶部 Dock 的固定 7 项对不上
    # （第 3 周放假后只剩前几天的课，Dock 却仍显示到周日，点周日没有对应列 → 排版错乱）。
    # 固定 7 列后列数与 Dock 恒等；放假的日子自然为空列。
    max_col = 7
    rows = []
    prev_seg = None
    for sec in range(1, 11):
        seg_key, seg_name = seg_of(sec)
        seg_start = " seg-start" if seg_key != prev_seg else ""
        td = (f'<td class="time s-{seg_key}{seg_start}" data-col="0">'
              f'<div class="sec-no">第{sec}节</div>'
              f'<div class="sec-tag">{seg_name}</div></td>')
        prev_seg = seg_key
        for d in range(1, max_col + 1):
            if (d, sec) in start:
                p = parsed[start[(d, sec)]]
                rs = max(len(p["secs"]), 1)
                color = COLORS[start[(d, sec)] % len(COLORS)]
                tint = COLOR_TINTS.get(color, "#5a8de1")
                t0, t1 = (p.get("time", "").split("-") + ["", ""])[:2]
                td += (f'<td rowspan="{rs}" class="cls" data-col="{d}" data-t0="{esc(t0.strip())}" data-t1="{esc(t1.strip())}" style="background:{color};--tc:{tint}" data-tint="{tint}"><div class="cname">{esc(p["name"])}</div>'
                       f'<div class="cinfo">{esc(p["time"])}</div>'
                       f'<div class="cinfo cinfo-x">{esc(p["bld"])}·{esc(p["room"])}</div>'
                       f'<div class="cinfo cinfo-x">{esc(p["teacher"])}</div></td>')
            elif (d, sec) not in cell:
                td += '<td data-col="%d"%s></td>' % (d, _hol_cls(week_monday, d))
        rows.append(f'<tr class="seg-{seg_key}">{td}</tr>')
    body = "".join(rows)
    return "<table>" + body + "</table>"


def render_page(weeks, semester, current_week):
    """生成整页：自定义下拉弹窗 + 每周一个隐藏表格面板 + 切换 JS"""
    opts = "".join(f'<button class="opt{" on" if w == current_week else ""}" data-wk="{w}" onclick="pick(event,{w})">{w}</button>'
                   for w in range(1, MAX_WEEK + 1))
    # 依据"今天"与当前周次反推第 1 周周一，进而算出每一周的日期
    _today = date.today()
    _this_mon = _today - timedelta(days=_today.weekday())
    _w1mon = _this_mon - timedelta(weeks=max(int(current_week) - 1, 0))
    weeks, _hd_notes = apply_holiday_rules(weeks, _w1mon)
    # 假期区间（用于页面提示）
    _hd_ranges = []
    for _a, _b in (HOLIDAY_RULES.get("holidays") or []):
        try:
            _hd_ranges.append((_kb_date(_a), _kb_date(_b)))
        except Exception:
            pass
    # 放假区间（给 JS 用：在 Dock 上把放假那天标出来）
    _hol_js = []
    for _a, _b in (HOLIDAY_RULES.get("holidays") or []):
        try:
            _da, _db = _kb_date(_a), _kb_date(_b)
            _hol_js.append([_da.year, _da.month, _da.day, _db.year, _db.month, _db.day])
        except Exception:
            pass
    _hol_js = json.dumps(_hol_js)
    panes = []
    for w in range(1, MAX_WEEK + 1):
        parsed = weeks.get(str(w), [])
        _mon = _w1mon + timedelta(weeks=w - 1)
        # 统计本周落在假期的天数
        _hd_days = sum(1 for _i in range(7)
                       if any(_ra <= (_mon + timedelta(days=_i)) <= _rb for _ra, _rb in _hd_ranges))
        _tip = ""
        if _hd_days >= 7:
            _tip = ('<div class="hdTip hdFull">放假调休 · 本周无课</div>')
        elif _hd_days > 0:
            _tip = ('<div class="hdTip">含 %d 天放假，已按调休安排调整</div>' % _hd_days)
        if _hd_notes.get(str(w)):
            _tip += ('<div class="hdNote">%s</div>' % esc(_hd_notes[str(w)]))
        if parsed:
            content = '<div class="kbGlass">' + _tip + table_html(parsed, _mon) + '</div>'
        else:
            content = _tip + ('<div class="empty">%s</div>'
                              % ("放假调休 · 本周无课" if _hd_days >= 7 else "本周暂无课程安排"))
        panes.append(f'<div class="pane" data-wk="{w}" data-mon="{_mon.year}-{_mon.month}-{_mon.day}" style="display:none">{content}</div>')
    _cur_mon = _w1mon + timedelta(weeks=max(int(current_week) - 1, 0))
    _dock_items = []
    for _d in range(1, 8):
        _dt = _cur_mon + timedelta(days=_d - 1)
        _dock_items.append(
            '<button class="dockItem" data-col="' + str(_d) + '" onclick="setFocusDay(' + str(_d) + ')">'
            '<b>' + DAY_NAMES[_d] + '</b><i>' + str(_dt.month) + '/' + str(_dt.day) + '</i><span class="dot"></span></button>')
    dock_html = ('<div class="dock" id="dock">'
                 '<span class="dockLabel" id="dockLabel">第 ' + str(current_week) + ' 周</span>'
                 '<div class="dockThumb" id="dockThumb"></div>'
                 + "".join(_dock_items) + "</div>")
    now = time.strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>我的课表</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--line:rgba(148,163,184,.30);--ink:#0f172a;--tw:56px}}
html{{-webkit-text-size-adjust:100%}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
  color:var(--ink);padding:14px;min-height:100vh;
  background-color:#eef2f8;
  background-image:
    radial-gradient(760px 460px at 8% -6%, rgba(99,102,241,.26), transparent 62%),
    radial-gradient(680px 420px at 96% 4%, rgba(236,72,153,.18), transparent 60%),
    radial-gradient(720px 480px at 46% 104%, rgba(14,165,233,.22), transparent 62%);
  background-attachment:fixed;
}}
h1{{font-size:21px;text-align:center;margin:6px 0 2px;color:#0b1220}}
.sub{{text-align:center;color:#64748b;font-size:13px;margin-bottom:10px}}

/* ── 玻璃：外框保留通透感，内容区提高不透明度保证文字锐利 ── */
.glassLite{{
  background:rgba(255,255,255,.62);
  -webkit-backdrop-filter:blur(16px) saturate(170%);
  backdrop-filter:blur(16px) saturate(170%);
  border:1px solid rgba(255,255,255,.8);
  box-shadow:0 10px 30px rgba(31,38,135,.12), inset 0 1px 0 rgba(255,255,255,.9);
}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .glassLite{{background:rgba(255,255,255,.9)}}
}}

/* 周次按钮 */
.pick{{text-align:center;margin:12px 0;position:relative;display:inline-block}}
.pick .btn{{font-size:14px;padding:9px 20px;border:none;border-radius:999px;
  background:linear-gradient(135deg,rgba(90,141,225,.95),rgba(127,179,240,.95));color:#fff;
  font-weight:700;outline:none;cursor:pointer;display:inline-flex;align-items:center;gap:6px;
  -webkit-tap-highlight-color:transparent;
  box-shadow:0 6px 18px rgba(90,141,225,.32), inset 0 1px 0 rgba(255,255,255,.5);
  transition:transform .22s cubic-bezier(.34,1.4,.5,1), box-shadow .22s}}
.pick .btn:active{{transform:scale(.94)}}
.pick .btn.open{{box-shadow:0 10px 26px rgba(90,141,225,.46), inset 0 1px 0 rgba(255,255,255,.5);
  transform:scale(1.03)}}
.pick .btn .caret{{display:inline-block;font-size:10px;line-height:1;
  transition:transform .32s cubic-bezier(.34,1.4,.5,1)}}
.pick .btn.open .caret{{transform:rotate(180deg)}}
.ddpanel{{position:absolute;top:calc(100% + 10px);left:50%;width:290px;
  border-radius:22px;padding:12px;z-index:99;
  /* 用 transform 做缩放淡入，替代 display 硬切 */
  transform:translateX(-50%) translateY(-10px) scale(.92);
  transform-origin:50% 0;opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity .26s ease, transform .32s cubic-bezier(.34,1.42,.5,1), visibility .26s;
  background:rgba(255,255,255,.82);
  -webkit-backdrop-filter:blur(24px) saturate(180%);backdrop-filter:blur(24px) saturate(180%);
  border:1px solid rgba(255,255,255,.85);
  box-shadow:0 16px 40px rgba(30,60,110,.2), inset 0 1px 0 rgba(255,255,255,.9)}}
.ddpanel.show{{opacity:1;visibility:visible;pointer-events:auto;
  transform:translateX(-50%) translateY(0) scale(1)}}
.ddpanel .gtitle{{font-size:12px;color:#64748b;text-align:center;margin-bottom:9px;font-weight:700}}
.ddgrid{{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}}
.opt{{font-size:13px;padding:8px 0;border:none;border-radius:999px;background:rgba(238,243,251,.9);
  color:#1a3a6b;font-weight:700;cursor:pointer;text-align:center;
  -webkit-tap-highlight-color:transparent;
  opacity:0;transform:scale(.6) translateY(-6px);
  transition:background .16s, color .16s, box-shadow .16s, transform .2s, opacity .2s}}
/* 展开时逐个错峰淡入（stagger） */
.ddpanel.show .opt{{animation:optIn .34s cubic-bezier(.34,1.42,.5,1) forwards}}
@keyframes optIn{{
  0%{{opacity:0;transform:scale(.6) translateY(-6px)}}
  100%{{opacity:1;transform:scale(1) translateY(0)}}
}}
.ddpanel.show .opt:nth-child(1){{animation-delay:.02s}}  .ddpanel.show .opt:nth-child(2){{animation-delay:.04s}}
.ddpanel.show .opt:nth-child(3){{animation-delay:.06s}}  .ddpanel.show .opt:nth-child(4){{animation-delay:.08s}}
.ddpanel.show .opt:nth-child(5){{animation-delay:.10s}}  .ddpanel.show .opt:nth-child(6){{animation-delay:.12s}}
.ddpanel.show .opt:nth-child(7){{animation-delay:.14s}}  .ddpanel.show .opt:nth-child(8){{animation-delay:.16s}}
.ddpanel.show .opt:nth-child(9){{animation-delay:.18s}}  .ddpanel.show .opt:nth-child(10){{animation-delay:.20s}}
.ddpanel.show .opt:nth-child(11){{animation-delay:.22s}} .ddpanel.show .opt:nth-child(12){{animation-delay:.24s}}
.ddpanel.show .opt:nth-child(13){{animation-delay:.26s}} .ddpanel.show .opt:nth-child(14){{animation-delay:.28s}}
.ddpanel.show .opt:nth-child(15){{animation-delay:.30s}} .ddpanel.show .opt:nth-child(16){{animation-delay:.32s}}
.ddpanel.show .opt:nth-child(17){{animation-delay:.34s}} .ddpanel.show .opt:nth-child(18){{animation-delay:.36s}}
.ddpanel.show .opt:nth-child(19){{animation-delay:.38s}} .ddpanel.show .opt:nth-child(20){{animation-delay:.40s}}
.opt:active{{transform:scale(.88) !important}}
/* 当前选中项：展开时轻轻弹一下 */
.ddpanel.show .opt.on{{animation:optIn .34s cubic-bezier(.34,1.42,.5,1) forwards, optPop .46s .30s cubic-bezier(.34,1.5,.5,1)}}
@keyframes wkPop{{
  0%{{transform:scale(1)}} 40%{{transform:scale(1.10)}} 100%{{transform:scale(1)}}
}}
@keyframes optPop{{
  0%{{transform:scale(1)}} 45%{{transform:scale(1.16)}} 100%{{transform:scale(1)}}
}}
.opt:hover{{background:#5a8de1;color:#fff}}
.opt.on{{background:linear-gradient(135deg,#5a8de1,#7fb3f0);color:#fff;
  box-shadow:0 4px 12px rgba(90,141,225,.42)}}

/* 时段图例：胶囊 */
.legend{{display:flex;justify-content:center;gap:10px;margin:10px 0 4px;flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:6px;font-size:12px;color:#334155;
  background:rgba(255,255,255,.72);padding:5px 12px;border-radius:999px;
  border:1px solid rgba(255,255,255,.85);font-weight:700;
  box-shadow:0 2px 8px rgba(31,38,135,.08)}}
.legend i{{width:9px;height:9px;border-radius:999px;display:inline-block}}

/* ── 苹果 Dock 风格日期选择条 ── */
.dock{{position:sticky;top:0;z-index:60;display:flex;align-items:stretch;margin:12px 0 8px;
  padding:4px 4px 4px calc(var(--tw) + 4px);
  border-radius:999px;
  /* overflow:hidden —— 把椭圆高光裁进胶囊内，否则会露出条外 */
  overflow:hidden;
  /* 液态玻璃：冷调渐变，白底上也能看出厚度与折射（与选中列玻璃同一套视觉语言） */
  background:linear-gradient(135deg,
    rgba(255,255,255,.72) 0%,
    rgba(238,245,255,.52) 32%,
    rgba(210,230,255,.42) 56%,
    rgba(255,255,255,.58) 100%);
  -webkit-backdrop-filter:blur(26px) saturate(180%) brightness(1.04);
  backdrop-filter:blur(26px) saturate(180%) brightness(1.04);
  border:1px solid rgba(255,255,255,.88);
  /* 悬浮投影 + 镜片描边（上缘高光 / 下缘暗边 / 左右侧壁）+ 冷调内辉 */
  box-shadow:0 14px 34px rgba(31,38,135,.20),
             0 3px 9px rgba(15,23,42,.09),
             inset 0 1.5px 1px rgba(255,255,255,.95),
             inset 0 -1.5px 1px rgba(148,163,184,.34),
             inset 1.5px 0 1px rgba(255,255,255,.55),
             inset -1.5px 0 1px rgba(148,163,184,.24),
             inset 0 0 13px rgba(186,216,255,.34)}}
/* 曲面高光：左上横向椭圆镜面反射。
   必须 position:absolute —— 否则伪元素会变成 flex item 挤掉日期按钮。
   z-index:0 让高光留在文字之下，日期不被冲白。 */
.dock::before{{content:'';position:absolute;top:-30%;left:-8%;width:76%;height:78%;z-index:0;
  background:radial-gradient(ellipse at 35% 40%,
    rgba(255,255,255,.95) 0%, rgba(255,255,255,.40) 44%, rgba(255,255,255,0) 72%);
  border-radius:50%;filter:blur(9px);pointer-events:none}}
/* 底部折射光 + 虹彩色散（右下偏冷蓝） */
.dock::after{{content:'';position:absolute;inset:0;border-radius:inherit;z-index:0;pointer-events:none;
  background:radial-gradient(ellipse at 78% 118%, rgba(178,210,255,.52) 0%, rgba(178,210,255,0) 60%),
             linear-gradient(118deg, rgba(255,255,255,0) 50%, rgba(198,222,255,.30) 74%, rgba(255,255,255,.10) 100%);
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.36)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .dock{{background:linear-gradient(135deg,
    rgba(255,255,255,.92) 0%, rgba(228,239,255,.84) 45%, rgba(206,226,255,.80) 100%)}}
}}
/* 滑块：苹果分段控件的胶囊拇指 */
.dockThumb{{position:absolute;top:4px;left:calc(var(--tw) + 4px);height:calc(100% - 8px);
  width:calc((100% - var(--tw) - 8px) / 7);border-radius:999px;z-index:0;
  background:linear-gradient(135deg,#3b82f6,#60a5fa);
  box-shadow:0 4px 14px rgba(59,130,246,.42), inset 0 1px 0 rgba(255,255,255,.45);
  transition:left .30s cubic-bezier(.34,1.4,.5,1), opacity .2s;opacity:0}}
.dockThumb.on{{opacity:1}}
.dockLabel{{position:absolute;left:0;top:0;bottom:0;width:var(--tw);z-index:1;
  display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:800;color:#64748b;pointer-events:none;letter-spacing:.2px}}
.dockItem{{position:relative;z-index:1;flex:1 1 0;min-width:0;border:none;background:transparent;
  padding:7px 2px 8px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;
  font-family:inherit;color:#5b6b82;transition:color .22s;-webkit-tap-highlight-color:transparent}}
.dockItem b{{font-size:11.5px;font-weight:800;line-height:1.1;letter-spacing:.2px}}
.dockItem i{{font-size:10px;font-style:normal;font-weight:600;opacity:.72;line-height:1.1}}
.dockItem.on{{color:#fff}}
.dockItem.on i{{opacity:.92}}
.dockItem:active{{opacity:.7}}
/* 今天的小圆点 */
.dockItem .dot{{font-size:8.5px;font-weight:800;line-height:1;margin-top:2px;
  letter-spacing:.5px;color:#f59e0b;display:none}}
.dockItem.isToday .dot{{display:block}}
.dockItem.on .dot{{color:#fff}}
/* 放假那天：淡灰 + 减淡，文案由 JS 写成「放假」 */
.dockItem.holDay{{opacity:.45}}
.dockItem.holDay b{{text-decoration:line-through;text-decoration-thickness:1px}}
.dockItem.holDay i{{font-weight:800;letter-spacing:.3px}}

/* 表格：separate 模式让圆角生效 */
.kbGlass{{position:relative;border-radius:22px;overflow:hidden;
  background:rgba(255,255,255,.88);
  -webkit-backdrop-filter:blur(10px) saturate(150%);backdrop-filter:blur(10px) saturate(150%);
  border:1px solid rgba(255,255,255,.85);
  box-shadow:0 14px 38px rgba(31,38,135,.14), inset 0 1px 0 rgba(255,255,255,.95)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .kbGlass{{background:#fff}}
}}
table{{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;background:transparent}}
th,td{{border-right:1px solid var(--line);border-bottom:1px solid var(--line);
  padding:3px 2px;vertical-align:middle;text-align:center;font-size:12.5px}}
th{{background:rgba(43,90,160,.90);color:#fff;font-weight:700;font-size:12px;padding:8px 2px 7px;
  border-bottom:none;line-height:1.25}}
th[data-col]{{cursor:pointer;-webkit-tap-highlight-color:transparent}}
th[data-col]:active{{filter:brightness(1.12)}}
tr:last-child td{{border-bottom:none}}
td:last-child,th:last-child{{border-right:none}}

/* 时间列：时段色条 + 圆角 */
.time{{width:var(--tw);background:rgba(248,250,252,.9);padding:4px 2px}}
.time .sec-no{{font-size:11px;font-weight:800;color:#1e293b;line-height:1.25}}
.time .sec-tag{{font-size:10px;margin-top:2px;font-weight:800;line-height:1.25}}
.time.s-am{{background:rgba(255,251,235,.95)}}
.time.s-am .sec-no{{color:#92400e}}
.time.s-am .sec-tag{{color:#f59e0b}}
.time.s-pm{{background:rgba(255,247,237,.95)}}
.time.s-pm .sec-no{{color:#9a3412}}
.time.s-pm .sec-tag{{color:#f97316}}
.time.s-nt{{background:rgba(238,242,255,.95)}}
.time.s-nt .sec-no{{color:#3730a3}}
.time.s-nt .sec-tag{{color:#6366f1}}

/* 空格子时段底色 */
tr.seg-am td:not(.cls):not(.time){{background:rgba(255,251,235,.6)}}
tr.seg-pm td:not(.cls):not(.time){{background:rgba(255,247,237,.6)}}
tr.seg-nt td:not(.cls):not(.time){{background:rgba(238,242,255,.65)}}
/* 放假的空列：淡灰斜纹，一眼看出这天不上课 */
td.hol{{background-image:repeating-linear-gradient(135deg,
  rgba(100,116,139,.13) 0 6px, transparent 6px 12px) !important;
  background-color:rgba(148,163,184,.10) !important}}

/* 课程块：真圆角卡片 */
td.cls{{position:relative;cursor:pointer;padding:5px 4px 5px 9px;
  -webkit-tap-highlight-color:transparent;background-clip:padding-box;
  border-radius:14px;background-clip:border-box;
  border-right-color:transparent;border-bottom-color:transparent;
  transition:transform .26s cubic-bezier(.34,1.35,.5,1), filter .26s ease, box-shadow .26s ease;
  box-shadow:0 2px 8px rgba(20,40,80,.10), inset 0 1px 0 rgba(255,255,255,.55)}}
td.cls .cname{{line-height:1.30;font-size:12.5px;word-break:break-all}}
/* 课程块内只显示「课名」；时间/地点/教师全部收进弹窗。
   隐藏不影响弹窗：JS 用 querySelectorAll('.cinfo') 读取，display:none 的元素照样能取到文本。 */
td.cls .cinfo{{display:none !important}}
/* 右下角轻提示：可点开看详情 */
td.cls::after{{content:'';position:absolute;right:6px;bottom:5px;width:0;height:0;
  border-left:4px solid transparent;border-bottom:4px solid rgba(15,23,42,.20)}}
td.cls::before{{content:'';position:absolute;left:5px;top:6px;bottom:6px;width:4px;
  background:var(--tc,#3b82f6);border-radius:999px}}
td.cls:active{{opacity:.82}}
.cname{{font-weight:800;font-size:12px;color:#0b1220;line-height:1.32;padding-left:6px}}
.cinfo{{font-size:10.5px;color:#475569;margin-top:2px;line-height:1.45;padding-left:6px}}

/* ── 今天列：强对比高亮 ── */
th.today{{background:linear-gradient(135deg,#1d4ed8,#3b82f6) !important;
  position:relative}}
th.today .todayTag{{display:block;margin:3px auto 0;width:82%;font-size:9px;font-weight:800;
  color:#fff;background:rgba(255,255,255,.34);border-radius:999px;padding:2px 0;
  letter-spacing:1px;box-shadow:0 1px 3px rgba(0,0,0,.14)}}
/* 原蓝色列高亮已移除 —— 整列聚焦改由悬浮液态毛玻璃圆角框承担（见 .kbColGlass） */
td.today:not(.cls){{background:transparent !important;box-shadow:none}}
/* 当天课程块：保留课程本色并加深一点点🤏，同时微放大 —— 不再被蓝色冲淡 */
td.today.cls{{
  filter:saturate(1.28) brightness(.93);
  transform:scale(1.05);
  /* 必须高于玻璃框(z-index:5)：卡片浮在玻璃之上，课名才不会被 backdrop-filter 糊掉 */
  z-index:6;
  box-shadow:0 10px 26px rgba(20,40,80,.26), inset 0 1px 0 rgba(255,255,255,.6);
  transition:transform .26s cubic-bezier(.34,1.35,.5,1), filter .26s ease, box-shadow .26s ease;
}}
td.today.cls .cname{{font-weight:800}}
td.today.cls::before{{width:5px;left:5px}}
tr td.today:not(.cls):first-of-type{{box-shadow:none}}
/* 今天列第一个/最后一个格子的上下封口 */
table tr:first-child th.today{{border-top-left-radius:0}}
td.today{{font-weight:700}}

/* ── 正在上课 ── */
/* z-index 必须 > 玻璃框(5)：正在上课的卡片要浮在液态玻璃之上，
   否则会被 backdrop-filter 糊掉、看起来像整张消失。
   （原值 2 会被同等特异性的 td.today.cls{{z-index:6}} 之后的覆盖逻辑压到玻璃下面） */
td.cls.now{{z-index:7}}
td.cls.now::after{{content:'';position:absolute;inset:2px;border-radius:12px;
  border:2.5px solid #f43f5e;pointer-events:none;animation:kbPulse 1.6s ease-in-out infinite}}
@keyframes kbPulse{{
  0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(244,63,94,.35)}}
  50%{{opacity:.75;box-shadow:0 0 0 5px rgba(244,63,94,0)}}
}}
.nowBadge{{margin:5px 5px 0 7px;font-size:9.5px;font-weight:800;color:#fff;
  background:linear-gradient(135deg,#f43f5e,#fb7185);border-radius:999px;
  padding:3px 0;letter-spacing:.5px;box-shadow:0 2px 7px rgba(244,63,94,.4)}}
/* ── 选中列：上层悬浮液态毛玻璃圆角框（替代原蓝色高亮）── */
.kbColGlass{{position:absolute;z-index:5;pointer-events:none;
  border-radius:22px;opacity:0;overflow:hidden;
  /* 冷调色相渐变：白底卡片上也能看出玻璃的厚度与折射，不再是纯白一片 */
  background:linear-gradient(135deg,
    rgba(255,255,255,.70) 0%,
    rgba(236,244,255,.48) 34%,
    rgba(206,228,255,.36) 58%,
    rgba(255,255,255,.54) 100%);
  -webkit-backdrop-filter:blur(14px) saturate(165%) brightness(1.05);
  backdrop-filter:blur(14px) saturate(165%) brightness(1.05);
  border:1px solid rgba(255,255,255,.88);
  /* 悬浮投影 + 镜片描边（上缘高光、下缘暗边 = 玻璃厚度）+ 冷调内辉 */
  box-shadow:0 16px 36px rgba(31,38,135,.22),
             0 3px 10px rgba(15,23,42,.10),
             inset 0 1.6px 1px rgba(255,255,255,.95),
             inset 0 -1.6px 1px rgba(148,163,184,.36),
             inset 1.6px 0 1px rgba(255,255,255,.58),
             inset -1.6px 0 1px rgba(148,163,184,.26),
             inset 0 0 14px rgba(186,216,255,.36);
  transition:opacity .4s ease}}
/* 曲面高光：左上椭圆镜面反射 */
.kbColGlass::before{{content:'';position:absolute;top:-20%;left:-12%;width:80%;height:54%;
  background:radial-gradient(ellipse at 32% 32%,
    rgba(255,255,255,.98) 0%, rgba(255,255,255,.44) 42%, rgba(255,255,255,0) 70%);
  border-radius:50%;filter:blur(9px);pointer-events:none}}
/* 底部折射光 + 虹彩色散（右下偏冷蓝） */
.kbColGlass::after{{content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(ellipse at 80% 112%, rgba(178,210,255,.58) 0%, rgba(178,210,255,0) 58%),
             linear-gradient(118deg, rgba(255,255,255,0) 52%, rgba(198,222,255,.32) 76%, rgba(255,255,255,.12) 100%);
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.38)}}
.kbColGlass.on{{opacity:1;animation:liquidPop .46s cubic-bezier(.34,1.56,.64,1)}}
@keyframes liquidPop{{
  0%{{transform:scale(.92);border-radius:30px;opacity:0}}
  55%{{transform:scale(1.035);border-radius:18px}}
  78%{{transform:scale(.995);border-radius:24px}}
  100%{{transform:scale(1);border-radius:22px;opacity:1}}}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .kbColGlass{{background:linear-gradient(135deg,
    rgba(255,255,255,.90) 0%, rgba(226,238,255,.82) 45%, rgba(204,224,255,.78) 100%)}}
}}


.empty{{text-align:center;color:#64748b;padding:44px 0;font-size:15px}}
/* 假期调休提示条 */
.hdTip{{margin:0 0 8px;padding:7px 12px;border-radius:14px;font-size:12px;font-weight:700;
  text-align:center;color:#92400e;
  background:linear-gradient(135deg,rgba(254,240,138,.92),rgba(253,230,138,.88));
  border:1px solid rgba(251,191,36,.55);
  box-shadow:0 4px 12px rgba(180,120,20,.14)}}
.hdTip.hdFull{{color:#9a3412;
  background:linear-gradient(135deg,rgba(254,215,170,.94),rgba(253,186,116,.9));
  border:1px solid rgba(251,146,60,.55)}}
.hdNote{{margin:0 0 8px;padding:6px 12px;border-radius:12px;font-size:11.5px;
  text-align:center;color:#1e40af;background:rgba(219,234,254,.85);
  border:1px solid rgba(147,197,253,.55)}}
.pane{{display:none}}

/* 弹窗：更圆润 */
.kbMask{{position:absolute;left:0;right:0;background:rgba(15,25,45,.45);z-index:200;
  display:none;align-items:center;justify-content:center;padding:20px;
  -webkit-backdrop-filter:blur(5px);backdrop-filter:blur(5px)}}
.kbMask.show{{display:flex}}
.kbCard{{border-radius:26px;width:100%;max-width:340px;overflow:hidden;
  animation:kbPop .22s cubic-bezier(.2,.9,.3,1.2);
  background:rgba(255,255,255,.88);
  -webkit-backdrop-filter:blur(26px) saturate(180%);backdrop-filter:blur(26px) saturate(180%);
  border:1px solid rgba(255,255,255,.9);
  box-shadow:0 22px 54px rgba(20,40,80,.26), inset 0 1px 0 rgba(255,255,255,.95)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .kbCard{{background:#fff}}
}}
@keyframes kbPop{{from{{transform:scale(.92);opacity:0}}to{{transform:scale(1);opacity:1}}}}
.kbHead{{padding:20px 18px 16px;color:#fff;display:flex;align-items:center;gap:12px}}
.kbIcon{{width:48px;height:48px;border-radius:16px;background:rgba(255,255,255,.28);color:#fff;
  font-size:21px;font-weight:800;display:flex;align-items:center;justify-content:center;flex:none}}
.kbName{{flex:1;font-size:18px;font-weight:800;line-height:1.35;word-break:break-all}}
.kbClose{{width:32px;height:32px;border-radius:999px;background:rgba(255,255,255,.32);color:#fff;
  font-size:20px;line-height:32px;text-align:center;cursor:pointer;flex:none}}
.kbClose:active{{background:rgba(255,255,255,.55);transform:scale(.9)}}
.kbBody{{padding:12px 18px 18px;max-height:60vh;overflow-y:auto;-webkit-overflow-scrolling:touch}}
body.kbLock{{overflow:hidden}}
.kbRow{{display:flex;align-items:flex-start;padding:11px 0;
  border-bottom:1px solid rgba(148,163,184,.22);font-size:14px;line-height:1.55}}
.kbRow:last-child{{border-bottom:none}}
.kbRow .k{{width:86px;flex:none;color:#94a3b8;font-size:13px}}
.kbRow .v{{flex:1;color:#1a3a6b;font-weight:700;word-break:break-all}}
.kbRow .ki{{font-size:15px;margin-right:3px}}

/* 上课地点：置顶高亮大卡片，一眼可见 */
.kbRoom{{display:flex;align-items:center;gap:11px;margin:2px 0 13px;padding:12px;
  border-radius:18px;border:1.5px solid;
  box-shadow:0 8px 22px rgba(20,40,80,.14), inset 0 1px 0 rgba(255,255,255,.92);
  animation:rmIn .34s cubic-bezier(.34,1.4,.5,1)}}
@keyframes rmIn{{from{{opacity:0;transform:translateY(-6px) scale(.96)}}to{{opacity:1;transform:none}}}}
.kbRoom .rmIcon{{width:38px;height:38px;border-radius:12px;flex:none;display:flex;
  align-items:center;justify-content:center;font-size:19px;
  box-shadow:0 3px 10px rgba(20,40,80,.22);animation:rmPulse 2.2s ease-in-out infinite}}
@keyframes rmPulse{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.09)}}}}
.kbRoom .rmMain{{flex:1;min-width:0}}
.kbRoom .rmLabel{{font-size:10.5px;font-weight:800;letter-spacing:1.2px;color:#64748b;margin-bottom:1px}}
.kbRoom .rmCode{{font-size:23px;font-weight:900;line-height:1.14;letter-spacing:.3px;word-break:break-all}}
.kbRoom .rmSub{{font-size:11.5px;color:#475569;margin-top:3px;line-height:1.35;word-break:break-all}}
.kbRoom .rmCopy{{flex:none;font-size:11px;font-weight:800;color:#fff;padding:6px 11px;
  border-radius:999px;background:rgba(15,23,42,.32);cursor:pointer;-webkit-tap-highlight-color:transparent}}
.kbRoom .rmCopy:active{{transform:scale(.9);background:rgba(15,23,42,.5)}}
/* 其余信息弱化，衬托地点 */
.kbRow.dim .v{{font-weight:600;color:#334155}}

@media(min-width:720px){{
  body{{max-width:920px;margin:0 auto}}
  .cname{{font-size:14px}} .cinfo{{font-size:12px}}
  .time{{width:66px}} th,td{{font-size:13px}}
  :root{{--tw:66px}}
}}
@media(prefers-color-scheme:dark){{
  body{{background-color:#0b1220;color:#e2e8f0;
    background-image:
      radial-gradient(760px 460px at 8% -6%, rgba(99,102,241,.30), transparent 62%),
      radial-gradient(680px 420px at 96% 4%, rgba(236,72,153,.20), transparent 60%),
      radial-gradient(720px 480px at 46% 104%, rgba(14,165,233,.24), transparent 62%)}}
  h1{{color:#f1f5f9}}
  .sub{{color:#94a3b8}}
  .kbGlass{{background:rgba(30,41,59,.80);border-color:rgba(148,163,184,.22)}}
  .kbCard{{background:rgba(30,41,59,.88);border-color:rgba(148,163,184,.22)}}
  .kbRoom{{box-shadow:0 8px 22px rgba(0,0,0,.34)}}
  .kbRoom .rmLabel{{color:#94a3b8}} .kbRoom .rmSub{{color:#cbd5e1}}
  .kbRoom .rmCopy{{background:rgba(255,255,255,.22)}}
  .kbRow.dim .v{{color:#cbd5e1}}
  .time{{background:rgba(30,41,59,.85)}} .time .sec-no{{color:#e2e8f0}}
  .cname{{color:#f1f5f9}} .cinfo{{color:#94a3b8}}
  .kbRow .v{{color:#cfe0f5}} .kbRow .k{{color:#8fa0b3}}
  .kbRow{{border-bottom-color:rgba(148,163,184,.2)}}
  td.today{{background:rgba(59,130,246,.24) !important}}
  .dock{{background:rgba(30,41,59,.72);border-color:rgba(148,163,184,.24)}}
  .dockItem{{color:#94a3b8}}
  .dockItem.on{{color:#fff}}
  .dockLabel{{color:#94a3b8}}
  .opt{{background:rgba(51,65,85,.85);color:#cfe0f5}}
  .legend span{{background:rgba(30,41,59,.7);color:#cbd5e1;border-color:rgba(148,163,184,.22)}}
  tr.seg-am td:not(.cls):not(.time){{background:rgba(120,80,20,.22)}}
  tr.seg-pm td:not(.cls):not(.time){{background:rgba(130,70,25,.22)}}
  tr.seg-nt td:not(.cls):not(.time){{background:rgba(60,70,140,.26)}}
}}
</style></head><body>
<h1>我的课表</h1><div class="sub">{esc(semester)} · 共 {MAX_WEEK} 周</div>
<div style="text-align:center"><span class="pick">
<button class="btn" id="wkBtn" onclick="toggleDd(event)">第 {current_week} 周<span class="caret">▼</span></button>
<div class="ddpanel" id="ddPanel"><div class="gtitle">选择周次</div><div class="ddgrid">{opts}</div></div>
</span></div>
<div class="legend">
  <span><i style="background:#f59e0b"></i>上午 1-4节</span>
  <span><i style="background:#f97316"></i>下午 5-8节</span>
  <span><i style="background:#6366f1"></i>晚上 9-10节</span>
</div>
{dock_html}
<div id="panes">{''.join(panes)}</div>
<div class="sub" style="margin-top:12px">数据来源：学校接口 · 更新于 {now} · 托管 GitHub Pages</div>
<div class="kbMask" id="kbMask"><div class="kbCard"><div class="kbHead"><div class="kbIcon" id="kbIcon"></div><div class="kbName" id="kbName"></div><div class="kbClose" id="kbClose">&times;</div></div><div class="kbBody" id="kbBody"></div></div></div>
<script>
/* 放假区间 [起Y,起M,起D, 止Y,止M,止D]，用于 Dock 上标记「放假」。
   必须放在脚本最顶部：showWeek() 在脚本前半段就会被立即调用，
   若 KB_HOL 定义在后面，var 提升只会声明不赋值 → undefined.length 抛错，
   会中断整个初始化（连课程点击绑定都不会执行）。 */
var KB_HOL = {_hol_js};
function showWeek(n) {{
  var panes = document.querySelectorAll('.pane');
  for (var i = 0; i < panes.length; i++) {{
    panes[i].style.display = (panes[i].getAttribute('data-wk') === String(n)) ? 'block' : 'none';
  }}
  document.getElementById('wkBtn').innerHTML = '第 ' + n + ' 周<span class="caret">▼</span>';
  var opts = document.querySelectorAll('.opt');
  for (var j = 0; j < opts.length; j++) {{
    opts[j].className = 'opt' + ((parseInt(opts[j].getAttribute('data-wk')) === n) ? ' on' : '');
  }}
  document.getElementById('ddPanel').classList.remove('show');
  KB_WK = n;
  var lb = document.getElementById('dockLabel');
  if (lb) lb.textContent = '第 ' + n + ' 周';
  kbDockDates(n);
  kbMarkNow();
}}
function toggleDd(e) {{
  e.stopPropagation();
  var p = document.getElementById('ddPanel'), b = document.getElementById('wkBtn');
  var on = p.classList.toggle('show');
  if (b) b.classList.toggle('open', on);
}}
function pick(e, n) {{
  e.stopPropagation();
  showWeek(n);
  var b = document.getElementById('wkBtn'); if (b) b.classList.remove('open');
  var p = document.getElementById('ddPanel');
  if (p) p.classList.remove('show');
  if (b) {{ b.style.animation = 'none'; void b.offsetWidth; b.style.animation = 'wkPop .42s cubic-bezier(.34,1.5,.5,1)'; }}
}}
document.addEventListener('click', function() {{
  document.getElementById('ddPanel').classList.remove('show');
  var b = document.getElementById('wkBtn'); if (b) b.classList.remove('open');
}});
showWeek({current_week});

/* ── 课程详情弹窗 ── */
var KB_DAYS = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日'];
var KB_INFO_LABELS = ['时间', '教室', '教师'];
var kbLast = 0, kbSX = 0, kbSY = 0;

function kbEsc(s) {{
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}
/* 课程底色是浅色（如 #f3e8fd 淡紫），直接做头部背景会导致白字看不清。
   这里保持色相，把亮度压到指定值，得到适合白色文字的深色。 */
function kbTint(hex, L) {{
  var c = String(hex || '').replace('#', '');
  if (c.length === 3) {{ c = c[0]+c[0]+c[1]+c[1]+c[2]+c[2]; }}
  if (c.length !== 6) {{ return '#5a8de1'; }}
  var r = parseInt(c.substr(0,2),16)/255,
      g = parseInt(c.substr(2,2),16)/255,
      b = parseInt(c.substr(4,2),16)/255;
  var mx = Math.max(r,g,b), mn = Math.min(r,g,b), d = mx-mn;
  var l = (mx+mn)/2, s = 0, hh = 0;
  if (d !== 0) {{
    s = l > 0.5 ? d/(2-mx-mn) : d/(mx+mn);
    if (mx === r) {{ hh = ((g-b)/d + (g<b?6:0)); }}
    else if (mx === g) {{ hh = (b-r)/d + 2; }}
    else {{ hh = (r-g)/d + 4; }}
    hh /= 6;
  }}
  if (L == null) {{ L = 0.46; }}
  var S = Math.max(0.40, Math.min(0.88, s * 1.7));
  function h2(p, q, t) {{
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1/6) return p + (q-p)*6*t;
    if (t < 1/2) return q;
    if (t < 2/3) return p + (q-p)*(2/3-t)*6;
    return p;
  }}
  var q = L < 0.5 ? L*(1+S) : L+S-L*S;
  var p = 2*L - q;
  function to(x) {{ var v = Math.round(Math.max(0,Math.min(1,x))*255).toString(16); return v.length<2 ? '0'+v : v; }}
  return '#' + to(h2(p,q,hh+1/3)) + to(h2(p,q,hh)) + to(h2(p,q,hh-1/3));
}}

function kbRow(icon, k, v, cls) {{
  if (v === '' || v == null) return '';
  return '<div class="kbRow' + (cls ? ' ' + cls : '') + '"><div class="k"><span class="ki">' + icon +
         '</span>' + kbEsc(k) + '</div><div class="v">' + kbEsc(v) + '</div></div>';
}}
/* 上课地点：大号高亮卡片（课程主色），房间号超大字 + 复制按钮 */
function kbRoomCard(text, tint) {{
  if (!text) return '';
  var parts = String(text).split('·');
  var bld = (parts[0] || '').trim();
  var room = (parts.slice(1).join('·') || '').trim();
  if (!room) {{ room = bld; bld = ''; }}
  var m = /([A-Za-z]{{1,4}}[－-]?\\d{{1,4}}(?:[－-]\\d{{1,4}})?)/.exec(room);
  var code = m ? m[1] : room;
  var rest = m ? room.replace(m[1], '') : '';
  rest = rest.replace(/^[（(\\s　]+/, '').replace(/[）)\\s　]+$/, '');
  var deep = kbTint(tint, 0.32);
  var lite1 = kbTint(tint, 0.95), lite2 = kbTint(tint, 0.87);
  var h = '<div class="kbRoom" style="background:linear-gradient(135deg,' + lite1 + ',' + lite2 +
          ');border-color:' + kbTint(tint, 0.70) + '">';
  h += '<div class="rmIcon" style="background:' + deep + '">📍</div>';
  h += '<div class="rmMain">';
  h += '<div class="rmLabel">上课地点</div>';
  h += '<div class="rmCode" style="color:' + deep + '">' + kbEsc(code) + '</div>';
  var sub = (bld ? bld : '') + (bld && rest ? ' · ' : '') + rest;
  if (sub) h += '<div class="rmSub">' + kbEsc(sub) + '</div>';
  h += '</div>';
  h += '<div class="rmCopy" data-room="' + kbEsc(code) + '" onclick="kbCopyRoom(event,this)">复制</div>';
  h += '</div>';
  return h;
}}
function kbCopyRoom(e, el) {{
  e.stopPropagation();
  var t = el.getAttribute('data-room') || '';
  var done = function() {{
    var old = el.textContent; el.textContent = '已复制';
    setTimeout(function() {{ el.textContent = old; }}, 1400);
  }};
  try {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(t).then(done, function() {{ kbFallbackCopy(t); done(); }});
    }} else {{ kbFallbackCopy(t); done(); }}
  }} catch (err) {{ kbFallbackCopy(t); done(); }}
}}
function kbFallbackCopy(t) {{
  try {{
    var ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }} catch (e) {{}}
}}

/* 移动端可点性：直接给每个格子绑定 click+touchend，
   不依赖 document 委托（移动浏览器对非交互元素不冒泡 click） */
function kbBind(td) {{
  td.style.cursor = 'pointer';
  td.setAttribute('role', 'button');
  td.setAttribute('tabindex', '0');
  td.addEventListener('touchstart', function(e) {{
    var t = e.touches[0];
    if (t) {{ kbSX = t.clientX; kbSY = t.clientY; }}
  }}, {{ passive: true }});
  td.addEventListener('touchend', function(e) {{
    var t = e.changedTouches[0];
    if (t && (Math.abs(t.clientX - kbSX) > 10 || Math.abs(t.clientY - kbSY) > 10)) return;
    var now = Date.now();
    if (now - kbLast < 400) return;
    kbLast = now;
    if (e.stopPropagation) e.stopPropagation();
    if (e.preventDefault) e.preventDefault();
    kbOpen(td);
  }});
  td.addEventListener('click', function(e) {{
    var now = Date.now();
    if (now - kbLast < 400) return;
    kbLast = now;
    if (e.stopPropagation) e.stopPropagation();
    kbOpen(td);
  }});
}}
/* 用「网格占位」还原真实星期/节次（表格含 rowspan，按索引会错位） */
function kbAnnotate() {{
  var panes = document.querySelectorAll('.pane');
  for (var p = 0; p < panes.length; p++) {{
    var pane = panes[p];
    var wk = pane.getAttribute('data-wk');
    var tb = pane.querySelector('table');
    if (!tb) continue;
    var trs = tb.querySelectorAll('tr');
    var grid = [], ri = 0;
    for (var i = 0; i < trs.length; i++) {{
      var cells = trs[i].children;
      if (!cells.length || cells[0].tagName === 'TH') continue;
      if (!grid[ri]) grid[ri] = [];
      var ci = 0;
      for (var j = 0; j < cells.length; j++) {{
        var td = cells[j];
        while (grid[ri] && grid[ri][ci]) ci++;
        var rs = parseInt(td.getAttribute('rowspan') || '1', 10);
        var cs = parseInt(td.getAttribute('colspan') || '1', 10);
        for (var r = ri; r < ri + rs; r++) {{
          if (!grid[r]) grid[r] = [];
          for (var c = ci; c < ci + cs; c++) grid[r][c] = true;
        }}
        if (td.className && String(td.className).indexOf('cls') >= 0) {{
          var sec = ri + 1;
          td.setAttribute('data-wk', wk == null ? '' : wk);
          td.setAttribute('data-day', KB_DAYS[ci] || '');
          td.setAttribute('data-sec', String(sec));
          td.setAttribute('data-span', String(rs));
          td.setAttribute('data-sectext', rs > 1 ? ('第' + sec + '-' + (sec + rs - 1) + '节') : ('第' + sec + '节'));
          kbBind(td);
        }}
        ci += cs;
      }}
      ri++;
    }}
  }}
}}
function kbOpen(td) {{
  var nm = td.querySelector('.cname');
  var name = nm ? nm.textContent.trim() : '课程';
  document.getElementById('kbName').textContent = name;
  document.getElementById('kbIcon').textContent = name.charAt(0) || '课';
  var raw = td.getAttribute('data-tint') || '#5a8de1';
  var head = document.querySelector('.kbHead');
  head.style.background = 'linear-gradient(135deg,' + kbTint(raw, 0.52) + ',' + kbTint(raw, 0.38) + ')';
  var infos = td.querySelectorAll('.cinfo');
  var ICONS = ['🕐', '🏫', '👤'];
  var roomIdx = -1;
  for (var ri = 0; ri < KB_INFO_LABELS.length; ri++) {{
    if (KB_INFO_LABELS[ri] === '教室') {{ roomIdx = ri; break; }}
  }}
  var h = '';
  /* 上课地点置顶为高亮大卡片 */
  if (roomIdx >= 0 && infos[roomIdx]) {{
    h += kbRoomCard(infos[roomIdx].textContent.trim(), raw);
  }}
  h += kbRow('🗓️', '周次', td.getAttribute('data-wk') ? ('第 ' + td.getAttribute('data-wk') + ' 周') : '', 'dim');
  h += kbRow('📅', '星期', td.getAttribute('data-day'), 'dim');
  h += kbRow('⏰', '节次', td.getAttribute('data-sectext'), 'dim');
  for (var i = 0; i < infos.length; i++) {{
    if (i === roomIdx) continue;   /* 教室已置顶，此处不再重复 */
    h += kbRow(ICONS[i] || '📌', KB_INFO_LABELS[i] || ('信息' + (i + 1)),
               infos[i].textContent.trim(), (KB_INFO_LABELS[i] === '教师') ? 'dim' : '');
  }}
  document.getElementById('kbBody').innerHTML = h;
  var mask = document.getElementById('kbMask');
  kbPlace(mask);
  mask.classList.add('show');
  document.body.classList.add('kbLock');
}}
/* absolute + 滚动偏移覆盖当前视口（部分移动 WebView 的 fixed 有缺陷） */
function kbPlace(mask) {{
  var sy = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0;
  var sx = window.pageXOffset || document.documentElement.scrollLeft || document.body.scrollLeft || 0;
  var vh = window.innerHeight || document.documentElement.clientHeight || 600;
  var vw = window.innerWidth || document.documentElement.clientWidth || 360;
  mask.style.top = sy + 'px';
  mask.style.left = sx + 'px';
  mask.style.width = vw + 'px';
  mask.style.height = vh + 'px';
}}
function kbClose() {{
  document.getElementById('kbMask').classList.remove('show');
  document.body.classList.remove('kbLock');
}}
window.addEventListener('resize', function() {{
  var mask = document.getElementById('kbMask');
  if (mask && mask.classList.contains('show')) kbPlace(mask);
}});
document.getElementById('kbClose').onclick = function(e) {{ e.stopPropagation(); kbClose(); }};
document.getElementById('kbMask').onclick = function(e) {{ if (e.target === this) kbClose(); }};
document.addEventListener('keydown', function(e) {{ if (e.key === 'Escape') kbClose(); }});
kbAnnotate();

/* ── Dock 日期条 + 今天列高亮 + 当前课节脉动 ── */
var KB_MANUAL = false;   /* 用户是否手动选过某天 */
var KB_FOCUS = null;     /* 当前聚焦的列 1..6 */
var KB_WK = {current_week};   /* 当前显示周次 */

function kbMin(s) {{
  var m = /^(\\d{{1,2}}):(\\d{{2}})$/.exec((s || '').trim());
  if (!m) return -1;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}}
function kbRealCol() {{
  var js = new Date().getDay();            /* 0=周日 .. 6=周六 */
  return (js === 0) ? 7 : js;              /* 1=周一 .. 6=周六；周日=7（无列） */
}}
/* 依据该周周一日期推算某天的 M/D */
function kbDateOf(ymd, n) {{
  if (!ymd) return '';
  var p = String(ymd).split('-');
  if (p.length < 3) return '';
  var d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
  d.setDate(d.getDate() + n);
  return (d.getMonth() + 1) + '/' + d.getDate();
}}
/* 该日期是否落在放假区间 */
function kbIsHol(y, m, d) {{
  if (typeof KB_HOL === 'undefined' || !KB_HOL) return false;
  var t = new Date(y, m - 1, d).getTime();
  for (var i = 0; i < KB_HOL.length; i++) {{
    var a = new Date(KB_HOL[i][0], KB_HOL[i][1] - 1, KB_HOL[i][2]).getTime();
    var b = new Date(KB_HOL[i][3], KB_HOL[i][4] - 1, KB_HOL[i][5]).getTime();
    if (t >= a && t <= b) return true;
  }}
  return false;
}}
/* 由该周周一日期 + 偏移，得到完整 [y, m, d] */
function kbYmd(ymd, n) {{
  var p = String(ymd || '').split('-');
  if (p.length < 3) return null;
  var d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
  d.setDate(d.getDate() + n);
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()];
}}
/* 切周时刷新 Dock 上的日期，并把放假那天标成「放假」 */
function kbDockDates(wk) {{
  var pane = document.querySelector('.pane[data-wk="' + wk + '"]');
  var mon = pane ? pane.getAttribute('data-mon') : '';
  var items = document.querySelectorAll('.dockItem');
  for (var i = 0; i < items.length; i++) {{
    var c = parseInt(items[i].getAttribute('data-col'), 10) || 1;
    var dt = kbDateOf(mon, c - 1);
    var ymd = kbYmd(mon, c - 1);
    var hol = ymd ? kbIsHol(ymd[0], ymd[1], ymd[2]) : false;
    var it = items[i].querySelector('i');
    if (it) it.textContent = hol ? '放假' : (dt || '');
    items[i].classList.toggle('holDay', hol);
  }}
}}
function kbSetThumb(col) {{
  var th = document.getElementById('dockThumb');
  if (!th) return;
  if (col < 1 || col > 7) {{ th.classList.remove('on'); return; }}
  th.classList.add('on');
  th.style.left = 'calc(var(--tw) + 4px + (100% - var(--tw) - 8px) * ' + (col - 1) + ' / 7)';
}}
function kbClearMark() {{
  var cls = ['now', 'today'], i, j;
  for (j = 0; j < cls.length; j++) {{
    var ns = document.querySelectorAll('.' + cls[j]);
    for (i = 0; i < ns.length; i++) ns[i].classList.remove(cls[j]);
  }}
  var bs = document.querySelectorAll('.nowBadge, .todayTag');
  for (i = 0; i < bs.length; i++) {{
    if (bs[i].parentNode) bs[i].parentNode.removeChild(bs[i]);
  }}
}}
function kbCurPane() {{
  return document.querySelector('.pane[data-wk="' + KB_WK + '"]');
}}
/* 本周中「日期等于今天」的列号(1..7)；本周不含今天则返回 -1。
   不能用 new Date().getDay() —— 那只在看本周时才对，
   翻到别的周会把同星期几误判成今天，导致所有周都标上「正在上课」。 */
function kbTodayCol() {{
  var pane = kbCurPane();
  if (!pane) return -1;
  var mon = pane.getAttribute('data-mon');
  var d = new Date();
  var today = (d.getMonth() + 1) + '/' + d.getDate();
  for (var c = 1; c <= 7; c++) {{
    if (kbDateOf(mon, c - 1) === today) return c;
  }}
  return -1;
}}
function kbMarkNow() {{
  kbClearMark();
  var pane = kbCurPane();
  var real = kbTodayCol();          /* 本周里的真实今天；非本周为 -1 */
  var weekHasToday = (real >= 1);
  if (!KB_MANUAL) KB_FOCUS = (real >= 1 && real <= 7) ? real : null;

  /* 1) 聚焦列高亮 —— 只作用于当前周面板 */
  if (KB_FOCUS && pane) {{
    var ns = pane.querySelectorAll('th[data-col="' + KB_FOCUS + '"], td[data-col="' + KB_FOCUS + '"]');
    for (var i = 0; i < ns.length; i++) ns[i].classList.add('today');
  }}

  /* 2) 当前课节 —— 仅当本周确实包含今天，且只在当前周面板内查找 */
  if (weekHasToday && pane) {{
    var d = new Date();
    var nowMin = d.getHours() * 60 + d.getMinutes();
    var cs = pane.querySelectorAll('td.cls[data-t0][data-col="' + real + '"]');
    for (var k = 0; k < cs.length; k++) {{
      var a = kbMin(cs[k].getAttribute('data-t0'));
      var b = kbMin(cs[k].getAttribute('data-t1'));
      if (a < 0 || b < 0) continue;
      if (nowMin >= a && nowMin <= b) {{
        cs[k].classList.add('now');
        var sp = document.createElement('div');
        sp.className = 'nowBadge';
        sp.textContent = '正在上课';
        cs[k].appendChild(sp);
      }}
    }}
  }}

  /* 3) 同步 Dock 选中态 + 今天圆点（今天仅在本周出现） */
  var items = document.querySelectorAll('.dockItem');
  for (var q = 0; q < items.length; q++) {{
    var c = parseInt(items[q].getAttribute('data-col'), 10);
    var isT = weekHasToday && (c === real);
    /* 用 classList 增量切换，保留 kbDockDates 打上的 holDay 标记 */
    items[q].classList.toggle('on', c === KB_FOCUS);
    items[q].classList.toggle('isToday', isT);
    var dt = items[q].querySelector('.dot');
    if (dt) dt.textContent = isT ? '今天' : '';
  }}
  kbSetThumb(KB_FOCUS || 0);
  /* 玻璃框：先定位一次，放大动画(0.26s)结束后再校正一次几何 */
  kbGlassFrame(pane, KB_FOCUS);
  setTimeout(function () {{ kbGlassFrame(kbCurPane(), KB_FOCUS); }}, 340);
}}
/* 点击 Dock 项：手动聚焦某一天 */

/* ── 选中列：液态玻璃框 ──
   悬浮在整列之上（z-index:5），课程卡片 z-index:6 浮在玻璃之上，
   所以课名文字不会被 backdrop-filter 糊掉。 */
var KB_FRAME_KEY = '';
function kbFrameRect(pane, col) {{
  var box = pane ? pane.querySelector('.kbGlass') : null;
  if (!box || !col || col < 1 || col > 7) return null;
  var cells = pane.querySelectorAll('td[data-col="' + col + '"]');
  if (!cells.length) return null;
  var br = box.getBoundingClientRect();
  var t = 1e9, b = -1e9, l = 1e9, r = -1e9, n = 0;
  for (var i = 0; i < cells.length; i++) {{
    var rc = cells[i].getBoundingClientRect();
    if (!rc.width && !rc.height) continue;
    if (rc.top < t) t = rc.top;
    if (rc.bottom > b) b = rc.bottom;
    if (rc.left < l) l = rc.left;
    if (rc.right > r) r = rc.right;
    n++;
  }}
  if (!n || t >= b || l >= r) return null;
  /* 外扩 2px：玻璃比列本身略大一圈，悬浮感更明显 */
  return {{ top: Math.round(t - br.top - 2), left: Math.round(l - br.left - 2),
           w: Math.round(r - l + 4), h: Math.round(b - t + 4) }};
}}
function kbGlassFrame(pane, col) {{
  if (!pane) return;
  var box = pane.querySelector('.kbGlass');
  if (!box) return;
  /* 清掉其它周面板里可能残留的玻璃框（切周时旧面板被隐藏，不会自己消失） */
  var olds = document.querySelectorAll('.kbColGlass');
  for (var q = 0; q < olds.length; q++) {{
    if (olds[q].parentNode !== box && olds[q].parentNode) olds[q].parentNode.removeChild(olds[q]);
  }}
  var rc = kbFrameRect(pane, col);
  var el = box.querySelector('.kbColGlass');
  if (!rc) {{ if (el) el.parentNode.removeChild(el); KB_FRAME_KEY = ''; return; }}
  if (!el) {{
    el = document.createElement('div');
    el.className = 'kbColGlass';
    box.appendChild(el);
  }}
  el.style.top = rc.top + 'px';
  el.style.left = rc.left + 'px';
  el.style.width = rc.w + 'px';
  el.style.height = rc.h + 'px';
  /* 只在「换列 / 换周」时重播弹性弹出动画；
     每 60s 的 kbMarkNow 与 resize 只更新几何，不会反复弹 */
  var colKey = (pane.getAttribute('data-wk') || '') + '|' + col;
  if (colKey !== KB_FRAME_KEY) {{
    KB_FRAME_KEY = colKey;
    el.classList.remove('on'); void el.offsetWidth; el.classList.add('on');
  }} else if (!el.classList.contains('on')) {{
    el.classList.add('on');
  }}
}}
window.addEventListener('resize', function () {{ kbGlassFrame(kbCurPane(), KB_FOCUS); }});
function setFocusDay(col) {{
  KB_MANUAL = true;
  KB_FOCUS = col;
  kbMarkNow();
}}
kbDockDates(KB_WK);
kbMarkNow();
setInterval(kbMarkNow, 60000);
</script>
</body></html>"""


def gh_api(method, path, body=None, timeout=30):
    """GitHub API 用系统 curl 发送（路由器的 Python 缺 https 支持，curl 自带）

    ⚠ 关键点：页面 HTML 转 base64 后约 130KB+。
       Linux 对「单个命令行参数」有 MAX_ARG_STRLEN = 128KB 的硬限制，
       OpenWrt 等小内存设备还会受 ARG_MAX 总量限制，
       把这么大的串塞进 argv 会直接抛 OSError [Errno 7] Argument list too long。
       这里改成：先把 body 写入临时文件，再用 curl 的 `@文件` 语法读取，
       完全绕开 argv 长度限制（管道 stdin 在部分精简 curl 上不可靠，故用文件）。
    """
    import tempfile
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method,
           "-H", f"Authorization: token {GH_PAT}",
           "-H", "Accept: application/vnd.github+json",
           "-H", "User-Agent: kebiao-updater"]
    tmp_path = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(prefix="kbgh_", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@" + tmp_path]
    cmd.append(f"https://api.github.com{path}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout + 5)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ★ 这里必须有返回值：曾漏掉 return，导致调用方拿到 None，
    #   cur.get("sha") 抛 'NoneType' object has no attribute 'get'。
    raw = (p.stdout or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("curl 失败(exit=%s): %s | %s"
                           % (p.returncode, err[:200], raw[:200]))
    if not raw:
        raise RuntimeError("GitHub 返回空（网络不通或被拒）")
    try:
        return json.loads(raw)
    except Exception:
        raise RuntimeError("GitHub 返回非 JSON: %s" % raw[:240])

def write_status(info):
    """把本次运行结果写入仓库的 data/kebiao_status.json，便于随时核查云端是否真的在工作。

    主页 index.html 只在内容有变化时才推送，所以单看提交历史无法判断云端是否成功拉取。
    这个文件每次运行都会更新（含时间戳），可作为云端心跳。
    """
    try:
        import datetime as _dt
        info = dict(info or {})
        info["ts"] = (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(info, ensure_ascii=False, indent=1)
        cur = None
        try:
            cur = gh_api("GET", f"/repos/{GH_REPO}/contents/data/kebiao_status.json")
        except Exception:
            cur = None
        body = {"message": "status " + info["ts"],
                "content": base64.b64encode(payload.encode("utf-8")).decode()}
        if cur and cur.get("sha"):
            body["sha"] = cur["sha"]
        gh_api("PUT", f"/repos/{GH_REPO}/contents/data/kebiao_status.json", body)
        return True
    except Exception as e:
        print("WARN 状态写入失败:", e)
        return False


def push_to_github(html):
    cur = gh_api("GET", f"/repos/{GH_REPO}/contents/{GH_PAGE}")
    if not isinstance(cur, dict):
        raise RuntimeError("读取远端 index.html 失败：gh_api 返回 %r" % (cur,))
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
    _t0 = time.time()
    _stat = {"ok": False, "stage": "init"}
    try:
        _stat["stage"] = "login"
        token = login_and_get_token()
        _stat["login"] = True

        _stat["stage"] = "fetch"
        weeks, semester, cur_wk = load_all_weeks(token)
        _stat["semester"] = semester
        _stat["current_week"] = cur_wk
        _stat["courses_total"] = sum(len(v) for v in weeks.values())
        _stat["weeks_with_courses"] = sum(1 for v in weeks.values() if v)
        _stat["per_week"] = {k: len(v) for k, v in weeks.items()}
        # 若全部周都没课，视为拉取异常（学校接口多半没返回数据）
        if _stat["courses_total"] == 0:
            _stat["warn"] = "所有周均无课程，请检查学校接口"

        _stat["stage"] = "render"
        html = render_page(weeks, semester, cur_wk)
        _stat["html_bytes"] = len(html)
        _stat["holiday_rules"] = bool(HOLIDAY_RULES.get("enabled"))

        _stat["stage"] = "push"
        result = push_to_github(html)
        _stat["push"] = result
        _stat["ok"] = True
        _stat["secs"] = round(time.time() - _t0, 1)
        write_status(_stat)
        print("OK", result, f"(默认第{cur_wk}周)")
    except Exception as e:
        _stat["error"] = str(e)[:300]
        _stat["secs"] = round(time.time() - _t0, 1)
        write_status(_stat)
        print("ERR", e)
        sys.exit(1)
