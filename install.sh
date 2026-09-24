#!/usr/bin/env sh
# 兼容旧习惯的壳：全部逻辑在 install.py。Windows 请直接 python install.py。
dir=$(cd "$(dirname "$0")" && pwd)
for p in python3 python; do
  if command -v "$p" >/dev/null 2>&1; then exec "$p" "$dir/install.py" "$@"; fi
done
echo "✗ 找不到 python3 / python" >&2
exit 1
