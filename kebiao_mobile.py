#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表 · 手机精简版（供 太虚/Termux 等手机 Linux 沙箱使用）

与原版 update_kebiao_gh.py 的区别：
  - 只依赖 Python 3 标准库，无需 pip 安装任何包
  - 不推送 GitHub（不需要 PAT），直接生成 index.html 到本地
  - 自带 --offline 离线模式：用已有的课表数据重新渲染（断网/接口挂了也能看）
  - 生成后可直接用 python3 -m http.server 在手机浏览器打开

用法：
  python3 kebiao_mobile.py            # 联网拉取最新课表
  python3 kebiao_mobile.py --offline  # 用本地缓存重新渲染（不联网）
  python3 kebiao_mobile.py --serve    # 生成后顺便起一个本地网页服务
"""

import os
import sys
import json
import time
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))

# 手机版不需要推送 GitHub，先塞占位值绕过官方脚本启动时的校验
# （用 setdefault：用户若已配好真实环境变量/配置文件，不会被覆盖）
os.environ.setdefault("GH_TOKEN", "mobile-local-no-push")
os.environ.setdefault("KB_USER_NO", "000000")
os.environ.setdefault("KB_PWD_ENC", "0000")
os.environ.setdefault("KB_SCHOOL_CODE", "0000")

# 加载同目录下的官方脚本，复用它的登录 / 拉取 / 渲染逻辑
sys.path.insert(0, HERE)
try:
    import update_kebiao_gh as kb
except Exception as e:
    sys.exit("找不到 update_kebiao_gh.py：请把两个文件放在同一目录\n" + str(e))

OUT_HTML = os.path.join(HERE, "index.html")
CACHE = os.path.join(HERE, "weeks_cache.json")


def load_offline():
    """从缓存渲染（不联网）。"""
    if not os.path.exists(CACHE):
        return None
    with open(CACHE, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("weeks"), d.get("semester"), d.get("current_week")


def save_cache(weeks, semester, cur_wk):
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"weeks": weeks, "semester": semester,
                   "current_week": cur_wk,
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="用本地缓存渲染，不联网")
    ap.add_argument("--serve", action="store_true", help="生成后启动本地网页服务")
    ap.add_argument("--port", type=int, default=8765, help="网页服务端口，默认 8765")
    args = ap.parse_args()

    t0 = time.time()
    offline_used = False

    if args.offline:
        got = load_offline()
        if not got:
            sys.exit("没有缓存（weeks_cache.json），离线模式无法渲染。先联网跑一次。")
        weeks, semester, cur_wk = got
        offline_used = True
        print("离线模式：使用缓存渲染")
    else:
        print("登录学校接口…")
        try:
            token = kb.login_and_get_token()
        except Exception as e:
            print("登录失败:", e)
            got = load_offline()
            if got:
                print("  → 回退到缓存渲染（课表可能不是最新的）")
                weeks, semester, cur_wk = got
                offline_used = True
            else:
                sys.exit("登录失败且无缓存，无法生成课表。")
        else:
            print("登录成功，拉取 20 周课表…（约 10-60 秒）")
            weeks, semester, cur_wk = kb.load_all_weeks(token)
            save_cache(weeks, semester, cur_wk)
            print("已缓存，下次可用 --offline 离线渲染")

    total = sum(len(v) for v in weeks.values())
    if total == 0:
        print("⚠ 警告：所有周都没有课程，学校接口可能没返回数据")
    print(f"课程总数：{total} 门 / 共 {len(weeks)} 周，当前第 {cur_wk} 周")

    html = kb.render_page(weeks, semester, cur_wk)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ 已生成 {OUT_HTML}（{len(html)} 字节）")
    print(f"   耗时 {round(time.time() - t0, 1)} 秒"
          + ("（离线缓存）" if offline_used else "（联网最新）"))

    if args.serve:
        import http.server
        import socketserver

        class H(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

        os.chdir(HERE)
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.port), H) as httpd:
            print(f"\n手机浏览器打开：http://127.0.0.1:{args.port}/")
            print("（同一 WiFi 下其他设备可用手机 IP 访问；Ctrl+C 停止）")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n已停止")


if __name__ == "__main__":
    main()
