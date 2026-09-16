#!/usr/bin/env bash
# 课表 · 手机一键安装脚本（太虚 / Termux 等 Android Linux 沙箱）
# 用法：bash setup_phone.sh
set -e

DIR="$HOME/kebiao"
mkdir -p "$DIR"
cd "$DIR"

echo "=== 1/4 检查 Python ==="
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，尝试安装…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3
  elif command -v pkg >/dev/null 2>&1; then
    pkg install -y python
  elif command -v apk >/dev/null 2>&1; then
    apk add python3
  else
    echo "无法自动安装，请手动安装 python3 后重跑本脚本"; exit 1
  fi
fi
python3 -V

echo ""
echo "=== 2/4 下载脚本 ==="
ok=0
for u in \
  "https://cdn.jsdelivr.net/gh/WoShiYiGeManHuaJia/kebiao-page@main/update_kebiao_gh.py" \
  "https://raw.githubusercontent.com/WoShiYiGeManHuaJia/kebiao-page/main/update_kebiao_gh.py"; do
  if curl -sS --connect-timeout 10 --max-time 60 -o update_kebiao_gh.py "$u" && [ -s update_kebiao_gh.py ]; then
    echo "  官方脚本 ✅ ($u)"; ok=1; break
  fi
  echo "  失败，换下一个源…"
done
[ "$ok" = 1 ] || { echo "下载 update_kebiao_gh.py 失败，检查网络"; exit 1; }

ok=0
for u in \
  "https://cdn.jsdelivr.net/gh/WoShiYiGeManHuaJia/kebiao-page@main/kebiao_mobile.py" \
  "https://raw.githubusercontent.com/WoShiYiGeManHuaJia/kebiao-page/main/kebiao_mobile.py"; do
  if curl -sS --connect-timeout 10 --max-time 60 -o kebiao_mobile.py "$u" && [ -s kebiao_mobile.py ]; then
    echo "  手机版脚本 ✅"; ok=1; break
  fi
done
[ "$ok" = 1 ] || { echo "下载 kebiao_mobile.py 失败"; exit 1; }

echo ""
echo "=== 3/4 写入学校账号 ==="
cat > "$HOME/.kb_conf" <<'CONF'
USER_NO=202405190231
PWD_ENC=QlRZY0NDcTBDRG5uQnZ4TDROSWFzZz09
SCHOOL_CODE=4711
CONF
echo "  已写入 ~/.kb_conf（USER_NO=2024********）"

echo ""
echo "=== 4/4 首次生成课表 ==="
python3 kebiao_mobile.py || {
  echo "联网生成失败（可能没连校园网/接口不通），稍后可重跑"; exit 1; }

echo ""
echo "=========================================="
echo " 安装完成！查看课表："
echo "   python3 $DIR/kebiao_mobile.py --serve"
echo "   然后手机浏览器打开 http://127.0.0.1:8765/"
echo ""
echo " 刷新课表（联网拉取最新）："
echo "   python3 $DIR/kebiao_mobile.py"
echo ""
echo " 刷新课表（断网用缓存）："
echo "   python3 $DIR/kebiao_mobile.py --offline"
echo "=========================================="
