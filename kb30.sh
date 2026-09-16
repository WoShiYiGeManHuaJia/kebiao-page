#!/bin/sh
# 课表半小时自动更新 —— 路由器用
# 用法：
#   单次运行：  sh /root/kb30.sh
#   装定时任务：sh /root/kb30.sh install      （每 30 分钟）
#   看日志：    tail -20 /tmp/kb30.log
#
# 关键：下载走 GitHub API 而不是 jsDelivr —— jsDelivr 有缓存，
#       会拿到旧版脚本（曾导致修好的 bug 反复出现）。

DIR=/root
LOG=/tmp/kb30.log
REPO="WoShiYiGeManHuaJia/kebiao-page"

log() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ---- 安装/卸载定时任务 ----
case "$1" in
  install)
    # 5-23 点每小时整点跑一次；0-4 点（深夜）不跑 —— 那会儿既没人看课表，
    # 又最容易因持续请求触发校园网共享检测。
    ( crontab -l 2>/dev/null | grep -v "kb30.sh" ; echo "0 5-23 * * * sh /root/kb30.sh >> /tmp/kb30.log 2>&1" ) | crontab -
    echo "已安装定时任务（每天 5:00-23:00 每小时一次，共 19 次）："
    crontab -l | grep kb30.sh
    exit 0
    ;;
  uninstall)
    crontab -l 2>/dev/null | grep -v "kb30.sh" | crontab -
    echo "已移除定时任务"
    exit 0
    ;;
esac

cd "$DIR" || exit 1

# ---- 1) 拉最新脚本（经 GitHub API，绕过 CDN 缓存）----
curl -sS --connect-timeout 10 --max-time 60 \
  -H "Accept: application/vnd.github+json" \
  -o /tmp/kb_blob.json \
  "https://api.github.com/repos/$REPO/contents/update_kebiao_gh.py"

if [ ! -s /tmp/kb_blob.json ]; then
  log "✗ 下载失败：GitHub API 无响应（网络问题）"
  exit 1
fi

SIZE=$(python3 - <<'PY'
import json, base64
try:
    d = json.load(open('/tmp/kb_blob.json'))
    if 'content' not in d:
        print("ERR:" + str(d.get('message', '未知'))[:80]); raise SystemExit
    open('/root/update_kebiao_gh.py', 'wb').write(base64.b64decode(d['content']))
    print(d['size'])
except SystemExit:
    raise
except Exception as e:
    print("ERR:" + str(e)[:80])
PY
)

case "$SIZE" in
  ERR:*) log "✗ 解析失败 $SIZE"; exit 1 ;;
  "")    log "✗ 未取到大小"; exit 1 ;;
  *)     log "脚本已更新：$SIZE 字节" ;;
esac

rm -f /tmp/kb_blob.json

# ---- 2) 运行 ----
log "开始拉取课表（约 1-5 分钟）"
OUT=$(python3 update_kebiao_gh.py 2>&1)
RC=$?
echo "$OUT" | tail -12 | tee -a "$LOG"

if echo "$OUT" | grep -q "^OK"; then
  log "✓ 完成"
else
  log "✗ 失败（见上方输出）"
fi
exit $RC
