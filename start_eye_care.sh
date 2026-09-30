#!/usr/bin/env bash
# 护眼锁屏助手 — macOS / Unix 启动脚本
# 用法:
#   ./start_eye_care.sh
#   ./start_eye_care.sh --once --break-seconds 5
#   ./start_eye_care.sh --show-console
#   ./start_eye_care.sh --interval 15 --break-seconds 20

set -euo pipefail
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
ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--show-console" ]]; then
    SHOW_CONSOLE=1
  else
    ARGS+=("$arg")
  fi
done

if [[ "$SHOW_CONSOLE" -eq 1 ]]; then
  exec "$PYTHON" "$ROOT/eye_care.py" "${ARGS[@]}"
fi

# 后台启动；日志写到 /tmp，避免占用终端
LOG_FILE="${TMPDIR:-/tmp}/eye_care.log"
nohup "$PYTHON" "$ROOT/eye_care.py" "${ARGS[@]}" >>"$LOG_FILE" 2>&1 &
echo "已在后台启动护眼锁屏助手 (PID $!)。"
echo "悬浮倒计时 HUD 右键可退出；或运行 ./stop_eye_care.sh"
echo "日志: $LOG_FILE"
