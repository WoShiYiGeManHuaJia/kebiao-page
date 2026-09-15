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


def table_html(parsed):
    """由某周课程列表渲染出表格 HTML"""
    cell, start = {}, {}
    for i, p in enumerate(parsed):
        for sec in p["secs"]:
            cell[(p["day"], sec)] = i
        if p["secs"]:
            start[(p["day"], p["secs"][0])] = i
    rows = []
    prev_seg = None
    for sec in range(1, 11):
        seg_key, seg_name = seg_of(sec)
        seg_start = " seg-start" if seg_key != prev_seg else ""
        td = (f'<td class="time s-{seg_key}{seg_start}">'
              f'<div class="sec-no">第{sec}节</div>'
              f'<div class="sec-tag">{seg_name}</div></td>')
        prev_seg = seg_key
        for d in range(1, 7):
            if (d, sec) in start:
                p = parsed[start[(d, sec)]]
                rs = max(len(p["secs"]), 1)
                color = COLORS[start[(d, sec)] % len(COLORS)]
                tint = COLOR_TINTS.get(color, "#5a8de1")
                td += (f'<td rowspan="{rs}" class="cls" style="background:{color};--tc:{tint}" data-tint="{tint}"><div class="cname">{esc(p["name"])}</div>'
                       f'<div class="cinfo">{esc(p["time"])}</div>'
                       f'<div class="cinfo">{esc(p["bld"])}·{esc(p["room"])}</div>'
                       f'<div class="cinfo">{esc(p["teacher"])}</div></td>')
            elif (d, sec) not in cell:
                td += "<td></td>"
        rows.append(f'<tr class="seg-{seg_key}">{td}</tr>')
    header = "".join(f'<th data-col="{d}">{DAY_NAMES[d]}</th>' for d in range(1, 7))
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
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
  background:#f5f7fa;padding:14px;color:#222;-webkit-text-size-adjust:100%}}
h1{{font-size:20px;text-align:center;margin:6px 0 2px;color:#1e293b}}
.sub{{text-align:center;color:#888;font-size:13px;margin-bottom:10px}}

/* 周次选择 */
.pick{{text-align:center;margin:12px 0;position:relative;display:inline-block}}
.pick .btn{{font-size:14px;padding:7px 16px;border:none;border-radius:14px;
  background:linear-gradient(135deg,#5a8de1,#7fb3f0);color:#fff;font-weight:600;
  outline:none;box-shadow:0 3px 8px rgba(90,141,225,.32);cursor:pointer;display:inline-block}}
.pick .btn:active{{transform:scale(.97)}}
.ddpanel{{position:absolute;top:calc(100% + 8px);left:50%;transform:translateX(-50%);
  background:#fff;border-radius:14px;box-shadow:0 8px 24px rgba(30,60,110,.18);
  padding:10px;z-index:99;display:none;width:290px}}
.ddpanel.show{{display:block}}
.ddpanel .gtitle{{font-size:12px;color:#8a97ab;text-align:center;margin-bottom:8px;font-weight:600}}
.ddgrid{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}}
.opt{{font-size:13px;padding:6px 0;border:none;border-radius:9px;background:#eef3fb;
  color:#1a3a6b;font-weight:600;cursor:pointer;text-align:center;transition:background .15s,color .15s}}
.opt:hover{{background:#5a8de1;color:#fff}}
.opt.on{{background:linear-gradient(135deg,#5a8de1,#7fb3f0);color:#fff;box-shadow:0 2px 6px rgba(90,141,225,.35)}}

/* 时段图例 */
.legend{{display:flex;justify-content:center;gap:14px;margin:10px 0 2px;flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:5px;font-size:12px;color:#64748b}}
.legend i{{width:10px;height:10px;border-radius:3px;display:inline-block}}

/* 表格 */
table{{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;margin:0 auto;
  background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(30,60,110,.08)}}
th,td{{border:1px solid #e2e8f0;padding:5px 3px;vertical-align:middle;text-align:center;font-size:12px}}
th{{background:#2b5aa0;color:#fff;font-weight:600;font-size:12px;padding:7px 2px}}

/* 时间列：按时段着色（左侧色条 + 文字色） */
.time{{width:52px;background:#f8fafc;padding:4px 2px}}
.time .sec-no{{font-size:11px;font-weight:700;color:#334155}}
.time .sec-tag{{font-size:10px;margin-top:1px;font-weight:700}}
.time.s-am{{background:#fffbeb;box-shadow:inset 3px 0 0 #f59e0b}}
.time.s-am .sec-tag{{color:#f59e0b}}
.time.s-pm{{background:#fff7ed;box-shadow:inset 3px 0 0 #f97316}}
.time.s-pm .sec-tag{{color:#f97316}}
.time.s-nt{{background:#eef2ff;box-shadow:inset 3px 0 0 #6366f1}}
.time.s-nt .sec-tag{{color:#6366f1}}

/* 时段分隔：首行粗线 + 空格子染时段底色 */
tr.seg-start td{{border-top:2px solid #94a3b8}}
tr.seg-am td:not(.cls){{background:#fffbeb}}
tr.seg-pm td:not(.cls){{background:#fff7ed}}
tr.seg-nt td:not(.cls){{background:#eef2ff}}

/* 课程块：左侧同色深条，强化辨识 */
td.cls{{position:relative;cursor:pointer;transition:transform .12s,box-shadow .12s;
  -webkit-tap-highlight-color:transparent;padding:6px 3px}}
td.cls::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:var(--tc,#3b82f6);border-radius:2px 0 0 2px}}
td.cls:active{{transform:scale(.97);box-shadow:inset 0 0 0 2px rgba(90,141,225,.45)}}
.cname{{font-weight:700;font-size:12px;color:#1e293b;line-height:1.3;padding-left:5px}}
.cinfo{{font-size:10.5px;color:#475569;margin-top:2px;line-height:1.45;padding-left:5px}}
.empty{{text-align:center;color:#999;padding:40px 0;font-size:15px}}
.pane{{display:none}}

/* 课程详情弹窗 */
.kbMask{{position:absolute;left:0;right:0;background:rgba(15,25,45,.48);z-index:200;
  display:none;align-items:center;justify-content:center;padding:20px}}
.kbMask.show{{display:flex}}
.kbCard{{background:#fff;border-radius:16px;width:100%;max-width:340px;
  box-shadow:0 12px 34px rgba(20,40,80,.3);overflow:hidden;animation:kbPop .18s ease-out}}
@keyframes kbPop{{from{{transform:scale(.93);opacity:0}}to{{transform:scale(1);opacity:1}}}}
.kbHead{{padding:18px 16px 15px;color:#fff;display:flex;align-items:center;gap:12px;
  border-bottom:1px solid rgba(255,255,255,.22)}}
.kbIcon{{width:46px;height:46px;border-radius:14px;background:rgba(255,255,255,.24);color:#fff;
  font-size:20px;font-weight:800;display:flex;align-items:center;justify-content:center;flex:none;
  text-shadow:0 1px 3px rgba(0,0,0,.2)}}
.kbName{{flex:1;font-size:18px;font-weight:700;line-height:1.35;word-break:break-all}}
.kbClose{{width:30px;height:30px;border-radius:50%;background:rgba(255,255,255,.28);color:#fff;
  font-size:20px;line-height:30px;text-align:center;cursor:pointer;flex:none;
  transition:background .15s,transform .15s}}
.kbClose:active{{background:rgba(255,255,255,.5);transform:scale(.9)}}
.kbBody{{padding:12px 16px 16px;max-height:60vh;overflow-y:auto;-webkit-overflow-scrolling:touch}}
body.kbLock{{overflow:hidden}}
.kbRow{{display:flex;align-items:flex-start;padding:11px 0;border-bottom:1px solid #eef1f6;
  font-size:14px;line-height:1.55}}
.kbRow:last-child{{border-bottom:none}}
.kbRow .k{{width:86px;flex:none;color:#98a4b6;font-size:13px}}
.kbRow .v{{flex:1;color:#1a3a6b;font-weight:600;word-break:break-all}}
.kbRow .ki{{font-size:15px;margin-right:3px}}

@media(min-width:720px){{
  body{{max-width:900px;margin:0 auto}}
  .cname{{font-size:14px}}
  .cinfo{{font-size:12px}}
  .time{{width:64px}}
}}
@media(prefers-color-scheme:dark){{
  .kbCard{{background:#22303f}}
  .kbRow{{border-bottom-color:#33424f}}
  .kbRow .v{{color:#cfe0f5}}
  .kbRow .k{{color:#8fa0b3}}
}}
</style></head><body>
<h1>我的课表</h1><div class="sub">{esc(semester)} · 共 {MAX_WEEK} 周</div>
<div style="text-align:center"><span class="pick">
<button class="btn" id="wkBtn" onclick="toggleDd(event)">第 {current_week} 周 ▾</button>
<div class="ddpanel" id="ddPanel"><div class="gtitle">选择周次</div><div class="ddgrid">{opts}</div></div>
</span></div>
<div class="legend">
  <span><i style="background:#f59e0b"></i>上午 1-4节</span>
  <span><i style="background:#f97316"></i>下午 5-8节</span>
  <span><i style="background:#6366f1"></i>晚上 9-10节</span>
</div>
<div id="panes">{''.join(panes)}</div>
<div class="sub" style="margin-top:12px">数据来源：学校接口 · 更新于 {now} · 托管 GitHub Pages</div>
<div class="kbMask" id="kbMask"><div class="kbCard"><div class="kbHead"><div class="kbIcon" id="kbIcon"></div><div class="kbName" id="kbName"></div><div class="kbClose" id="kbClose">&times;</div></div><div class="kbBody" id="kbBody"></div></div></div>
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

function kbRow(icon, k, v) {{
  if (v === '' || v == null) return '';
  return '<div class="kbRow"><div class="k"><span class="ki">' + icon +
         '</span>' + kbEsc(k) + '</div><div class="v">' + kbEsc(v) + '</div></div>';
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
  var h = '';
  h += kbRow('🗓️', '周次', td.getAttribute('data-wk') ? ('第 ' + td.getAttribute('data-wk') + ' 周') : '');
  h += kbRow('📅', '星期', td.getAttribute('data-day'));
  h += kbRow('⏰', '节次', td.getAttribute('data-sectext'));
  for (var i = 0; i < infos.length; i++) {{
    h += kbRow(ICONS[i] || '📌', KB_INFO_LABELS[i] || ('信息' + (i + 1)), infos[i].textContent.trim());
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
