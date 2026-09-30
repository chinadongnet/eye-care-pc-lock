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
# 优先选用带 tkinter +（可选）AppKit 的解释器，便于 macOS 菜单栏状态项
for candidate in \
  /opt/anaconda3/bin/python3 \
  "$HOME/anaconda3/bin/python3" \
  "$HOME/miniconda3/bin/python3" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)"
do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    if "$candidate" -c "import tkinter" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "未找到带 tkinter 的 python3。请安装 Python 3.11+（建议 Anaconda / python.org / brew install python-tk）。" >&2
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
echo "菜单栏图标或悬浮 HUD 右键可退出；或运行 ./stop_eye_care.sh"
echo "日志: $LOG_FILE"
