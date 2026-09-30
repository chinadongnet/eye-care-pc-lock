#!/usr/bin/env bash
# 护眼锁屏助手 — macOS / Unix 启动脚本
# 用法:
#   ./start_eye_care.sh
#   ./start_eye_care.sh --once --break-seconds 5
#   ./start_eye_care.sh --show-console
#   ./start_eye_care.sh --interval 15 --break-seconds 20

set -eo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "未找到 python3。请安装 Python 3.11+（建议 python.org 或 brew install python-tk）。" >&2
  exit 1
fi

SHOW_CONSOLE=0
# 过滤 --show-console；其余原样传给 eye_care.py（兼容 macOS 自带 bash 3.2）
FILTERED=()
for arg in "$@"; do
  if [[ "$arg" == "--show-console" ]]; then
    SHOW_CONSOLE=1
  else
    FILTERED+=("$arg")
  fi
done

LOG_FILE="$ROOT/eye_care.log"

if [[ "$SHOW_CONSOLE" -eq 1 ]]; then
  if [[ $# -eq 0 || ( $# -eq 1 && "$1" == "--show-console" ) ]]; then
    exec "$PYTHON" "$ROOT/eye_care.py"
  fi
  # 有额外参数时：去掉 --show-console 再 exec
  exec "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]+"${FILTERED[@]}"}"
fi

# 后台启动
if [[ ${#FILTERED[@]} -eq 0 ]]; then
  nohup "$PYTHON" "$ROOT/eye_care.py" >>"$LOG_FILE" 2>&1 &
else
  nohup "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}" >>"$LOG_FILE" 2>&1 &
fi
echo "已在后台启动护眼锁屏助手 (PID $!)。"
echo "悬浮倒计时 HUD 右键可退出；或运行 ./stop_eye_care.sh"
echo "日志: $LOG_FILE"
