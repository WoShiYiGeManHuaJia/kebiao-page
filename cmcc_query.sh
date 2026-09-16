#!/bin/sh
# 中国移动话费/流量查询 —— 在路由器上直接跑（与手机同一网络）
# 用法：sh /root/cmcc_query.sh
# 结果直接打印在屏幕上，你看完截图即可

CK='c=Ph5znU3SDFHcQNiY0w0sZqumxG3cG5T1ghBi7We1+UkgaDhZRT3KIwmMfcehh+JH; cmccssotoken=Ph5znU3SDFHcQNiY0w0sZqumxG3cG5T1ghBi7We1+UkgaDhZRT3KIwmMfcehh+JH@.10086.cn; CmLocation=731|731; CmProvid=hn; defaultloginuser_p=izr73fwOUuimT7R+YElqbvQdIEKrmWCpu49KY4pe7cglQnOlbxDN0nqcpR0yt5wisZCxOLswghEGNJmVqAhv6SMTsRVyT13VInOal6sQlEY+dvBVErR/ksPv5W6XILGzNIChi3gihwmhVzzoGOae/WsG44z9K9sAPKo5vF/bXD2NwSyqpJkEqg/LuT1QHsyO; is_login=true; jsessionid-cmcc=n16F4E76747A3FD05DBD96E348F585264-1; sendflag=20260916190358904861; lgToken=mhrzef8d51334e298e839140085b222a; cvToken=mhrz312b0b7640a4ad845bd0bb1c5c3e; CaptchaCode=FTJTyF; rdmdmd5=6A262275A4E308EFB2C069AC61B969A5; WT_FPC=id=26c0dd1e2f04c31a6591789556620697:lv=1789556625547:ss=1789556620697; gdp_user_id=gioenc-3ca28e0b%2C4aga%2C58gb%2C8776%2Cb10b3316585c; 9e4e5fa7244c6b6e_gdp_session_id=b30ff14a-3b2b-49c1-8c27-f94fa22c14ee'

PHONE='13787464642'
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'

echo "=============================="
echo " 移动话费/流量查询"
echo " 手机号: 137****4642"
echo " 时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================="
echo ""

q () {
  NAME="$1"; URL="$2"
  echo "[$NAME]"
  curl -sS --connect-timeout 12 --max-time 40 \
    -H "User-Agent: $UA" \
    -H "Accept: application/json, text/plain, */*" \
    -H "Accept-Language: zh-CN,zh;q=0.9" \
    -H "Referer: https://touch.10086.cn/i/" \
    -H "X-Requested-With: XMLHttpRequest" \
    -H "Cookie: $CK" \
    "$URL" 2>&1 | head -c 700
  echo ""
  echo ""
}

q "实时话费"   "https://touch.10086.cn/i/v1/fee/real/$PHONE"
q "套餐余量"   "https://touch.10086.cn/i/v1/fee/planbal/$PHONE"
q "流量余额"   "https://touch.10086.cn/i/v1/cust/flowbalance/$PHONE?channel=0705"
q "流量查询"   "https://touch.10086.cn/i/v1/cust/flowqry/$PHONE?month=202609"
q "用户流量"   "https://touch.10086.cn/i/v1/cust/flow/userFlowInfoQry/$PHONE"
q "账户查询"   "https://touch.10086.cn/i/v1/cust/accoutQryReq/$PHONE?bgnMonth=202609&endMonth=202609&channel=0705"

echo "=============================="
echo " 说明："
echo "  retCode=000000 → 成功，data 里是数据"
echo "  retCode=500003 → 登录态失效"
echo "=============================="
