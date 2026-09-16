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


def table_html(parsed, week_monday=None):
    """由某周课程列表渲染出表格 HTML"""
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for sec in p["secs"]:
            cell[(p["day"], sec)] = i
        if p["secs"]:
            start[(p["day"], p["secs"][0])] = i
    # 列数：默认周一~周六；若本周存在周日课程（调休补课）才扩展到 7 列，
    # 避免无谓地挤压课名宽度（每列仅约 41px）
    max_col = 6
    for p in parsed:
        if int(p.get("day", 0) or 0) >= 7:
            max_col = 7
            break
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
                td += '<td data-col="%d"></td>' % d
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
  background:rgba(255,255,255,.55);
  -webkit-backdrop-filter:blur(24px) saturate(185%);
  backdrop-filter:blur(24px) saturate(185%);
  border:1px solid rgba(255,255,255,.82);
  box-shadow:0 10px 30px rgba(31,38,135,.13), inset 0 1px 0 rgba(255,255,255,.92)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .dock{{background:rgba(255,255,255,.88)}}
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

/* 表格：separate 模式让圆角生效 */
.kbGlass{{border-radius:22px;overflow:hidden;
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

/* 课程块：真圆角卡片 */
td.cls{{position:relative;cursor:pointer;padding:5px 4px 5px 9px;
  -webkit-tap-highlight-color:transparent;background-clip:padding-box;
  border-radius:14px;background-clip:border-box;
  border-right-color:transparent;border-bottom-color:transparent;
  transition:transform .26s cubic-bezier(.34,1.35,.5,1), filter .26s ease, box-shadow .26s ease;
  box-shadow:0 2px 8px rgba(20,40,80,.10), inset 0 1px 0 rgba(255,255,255,.55)}}
td.cls .cname{{line-height:1.32;font-size:12.5px;word-break:break-all}}
td.cls .cinfo{{font-size:10.5px;margin-top:1px;line-height:1.4;word-break:break-all}}
/* 课程块内只显示「课名 + 时间」两行；地点/教师带 cinfo-x 标记，直接隐藏。
   用明确的 class 而不是 :nth-of-type —— 后者依赖子元素顺序，
   一旦模板多插一个 div 就会错位（曾导致时间被误隐藏）。
   弹窗 JS 仍按 .cinfo 全量读取，隐藏不影响详情展示。 */
td.cls .cinfo-x{{display:none !important}}
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
td.today:not(.cls){{background:rgba(59,130,246,.13) !important;
  box-shadow:inset 2px 0 0 rgba(37,99,235,.55), inset -2px 0 0 rgba(37,99,235,.55)}}
/* 当天课程块：保留课程本色并加深一点点🤏，同时微放大 —— 不再被蓝色冲淡 */
td.today.cls{{
  filter:saturate(1.28) brightness(.93);
  transform:scale(1.035);
  z-index:3;
  box-shadow:0 6px 16px rgba(20,40,80,.18), inset 0 1px 0 rgba(255,255,255,.6);
  transition:transform .26s cubic-bezier(.34,1.35,.5,1), filter .26s ease, box-shadow .26s ease;
}}
td.today.cls .cname{{font-weight:800}}
td.today.cls::before{{width:5px;left:5px}}
tr td.today:not(.cls):first-of-type{{box-shadow:inset -2px 0 0 rgba(37,99,235,.55)}}
/* 今天列第一个/最后一个格子的上下封口 */
table tr:first-child th.today{{border-top-left-radius:0}}
td.today{{font-weight:700}}

/* ── 正在上课 ── */
td.cls.now{{z-index:2}}
td.cls.now::after{{content:'';position:absolute;inset:2px;border-radius:12px;
  border:2.5px solid #f43f5e;pointer-events:none;animation:kbPulse 1.6s ease-in-out infinite}}
@keyframes kbPulse{{
  0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(244,63,94,.35)}}
  50%{{opacity:.75;box-shadow:0 0 0 5px rgba(244,63,94,0)}}
}}
.nowBadge{{margin:5px 5px 0 7px;font-size:9.5px;font-weight:800;color:#fff;
  background:linear-gradient(135deg,#f43f5e,#fb7185);border-radius:999px;
  padding:3px 0;letter-spacing:.5px;box-shadow:0 2px 7px rgba(244,63,94,.4)}}

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
  var m = /([A-Za-z]{{1,4}}[－-]?\d{{1,4}}(?:[－-]\d{{1,4}})?)/.exec(room);
  var code = m ? m[1] : room;
  var rest = m ? room.replace(m[1], '') : '';
  rest = rest.replace(/^[（(\s　]+/, '').replace(/[）)\s　]+$/, '');
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
  var m = /^(\d{{1,2}}):(\d{{2}})$/.exec((s || '').trim());
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
/* 切周时刷新 Dock 上的日期 */
function kbDockDates(wk) {{
  var pane = document.querySelector('.pane[data-wk="' + wk + '"]');
  var mon = pane ? pane.getAttribute('data-mon') : '';
  var items = document.querySelectorAll('.dockItem');
  for (var i = 0; i < items.length; i++) {{
    var c = parseInt(items[i].getAttribute('data-col'), 10) || 1;
    var dt = kbDateOf(mon, c - 1);
    var it = items[i].querySelector('i');
    if (it && dt) it.textContent = dt;
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
function kbMarkNow() {{
  kbClearMark();
  var real = kbRealCol();
  if (!KB_MANUAL) KB_FOCUS = (real >= 1 && real <= 6) ? real : null;

  /* 1) 聚焦列高亮 */
  if (KB_FOCUS) {{
    var ns = document.querySelectorAll('th[data-col="' + KB_FOCUS + '"], td[data-col="' + KB_FOCUS + '"]');
    for (var i = 0; i < ns.length; i++) ns[i].classList.add('today');
  }}

  /* 2) 当前课节（仅真实今天） */
  var d = new Date();
  var nowMin = d.getHours() * 60 + d.getMinutes();
  if (real >= 1 && real <= 6) {{
    var cs = document.querySelectorAll('td.cls[data-t0][data-col="' + real + '"]');
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

  /* 3) 同步 Dock 选中态 + 今天圆点 */
  var items = document.querySelectorAll('.dockItem');
  for (var q = 0; q < items.length; q++) {{
    var c = parseInt(items[q].getAttribute('data-col'), 10);
    items[q].className = 'dockItem' + (c === KB_FOCUS ? ' on' : '') + (c === real ? ' isToday' : '');
    var dt = items[q].querySelector('.dot');
    if (dt) dt.textContent = (c === real) ? '今天' : '';
  }}
  kbSetThumb(KB_FOCUS || 0);
}}
/* 点击 Dock 项：手动聚焦某一天 */
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
