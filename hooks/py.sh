#!/bin/sh
# 插件模式的启动器：找一个能用的 Python 3.8+ 跑 hooks/ 下的脚本。
#   sh py.sh guard-branch.py
# 依次试 python3、python、py -3（Windows 启动器）。Windows 商店的 python3 占位符会在版本检查这步失败，跳过。
# 一个都找不到就静默放行（exit 0、无输出）：守卫失效不能把会话卡死。
dir=${0%/*}                      # 不用 dirname：PATH 空着也得能跑
[ "$dir" = "$0" ] && dir=.
script="$dir/$1"
ok='import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'

if command -v python3 >/dev/null 2>&1 && python3 -c "$ok" >/dev/null 2>&1; then
  exec python3 "$script"
fi
if command -v python >/dev/null 2>&1 && python -c "$ok" >/dev/null 2>&1; then
  exec python "$script"
fi
if command -v py >/dev/null 2>&1 && py -3 -c "$ok" >/dev/null 2>&1; then
  exec py -3 "$script"
fi
exit 0
