#!/usr/bin/env bash
# 护眼锁屏助手 — macOS / Unix 启动脚本
# 用法:
#   ./start_eye_care.sh                  # LaunchAgent 后台启动（无 Terminal）并开启登录自启
#   ./start_eye_care.sh --once --break-seconds 5
#   ./start_eye_care.sh --show-console   # 前台（当前终端）
#   ./start_eye_care.sh --interval 15 --break-seconds 20
#
# macOS 默认通过 ~/Library/LaunchAgents/net.chinadong.eye-care.plist
# 由 launchd 托管，不弹出 Terminal；与菜单「开机自动启动」共用同一 Label。

set -eo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LABEL="net.chinadong.eye-care"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_FILE="$ROOT/eye_care.log"

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

xml_escape() {
  # shellcheck disable=SC2001
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

kill_stragglers() {
  local pids
  pids="$(pgrep -f "[e]ye_care.py" || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "$pids" | while read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  pids="$(pgrep -f "[e]ye_care.py" || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | while read -r pid; do
      [[ -z "$pid" ]] && continue
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
}

launchagent_bootout() {
  local uid domain
  uid="$(id -u)"
  domain="gui/${uid}"
  launchctl bootout "${domain}/${LABEL}" >/dev/null 2>&1 || true
  launchctl bootout "$domain" "$PLIST" >/dev/null 2>&1 || true
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
}

write_launchagent_plist() {
  local arg_xml="" a esc
  mkdir -p "$(dirname "$PLIST")"
  arg_xml="$(printf '        <string>%s</string>\n        <string>%s</string>\n' \
    "$(xml_escape "$PYTHON")" "$(xml_escape "$ROOT/eye_care.py")")"
  # bash 3.2：空数组勿直接展开
  if [[ ${#FILTERED[@]} -gt 0 ]]; then
    for a in "${FILTERED[@]}"; do
      esc="$(xml_escape "$a")"
      arg_xml+="        <string>${esc}</string>"$'\n'
    done
  fi
  cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
${arg_xml}    </array>
    <key>WorkingDirectory</key>
    <string>$(xml_escape "$ROOT")</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$(xml_escape "$LOG_FILE")</string>
    <key>StandardErrorPath</key>
    <string>$(xml_escape "$LOG_FILE")</string>
</dict>
</plist>
PLIST
}

launchagent_bootstrap() {
  local uid domain
  uid="$(id -u)"
  domain="gui/${uid}"
  if ! launchctl bootstrap "$domain" "$PLIST" 2>/tmp/eye_care_launchctl.err; then
    # 旧系统回退 load
    if ! launchctl load "$PLIST" 2>>/tmp/eye_care_launchctl.err; then
      echo "launchctl 加载失败：" >&2
      cat /tmp/eye_care_launchctl.err >&2 || true
      return 1
    fi
  fi
  return 0
}

if [[ "$SHOW_CONSOLE" -eq 1 ]]; then
  if [[ ${#FILTERED[@]} -eq 0 ]]; then
    exec "$PYTHON" "$ROOT/eye_care.py"
  fi
  exec "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}"
fi

# --- 后台：优先 LaunchAgent（无 Terminal 窗口）---
if [[ "$(uname -s)" == "Darwin" ]]; then
  launchagent_bootout
  kill_stragglers
  write_launchagent_plist
  if ! launchagent_bootstrap; then
    echo "LaunchAgent 启动失败，回退 nohup…" >&2
    if [[ ${#FILTERED[@]} -eq 0 ]]; then
      nohup "$PYTHON" "$ROOT/eye_care.py" >>"$LOG_FILE" 2>&1 &
    else
      nohup "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}" >>"$LOG_FILE" 2>&1 &
    fi
    echo "已用 nohup 后台启动 (PID $!)。日志: $LOG_FILE"
    exit 0
  fi
  # 等待进程起来
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if pgrep -f "[e]ye_care.py" >/dev/null 2>&1; then
      break
    fi
    sleep 0.3
  done
  if pgrep -f "[e]ye_care.py" >/dev/null 2>&1; then
    echo "已通过 LaunchAgent 后台启动（无 Terminal）。"
    echo "Label: $LABEL"
    echo "plist: $PLIST"
    echo "登录自启: 已启用（RunAtLoad）"
    echo "停止: ./stop_eye_care.sh  或  launchctl bootout gui/\$(id -u)/$LABEL"
    echo "日志: $LOG_FILE"
    exit 0
  fi
  echo "LaunchAgent 已加载但未检测到进程，请查看日志: $LOG_FILE" >&2
  exit 1
fi

# 非 macOS：nohup 后台
if [[ ${#FILTERED[@]} -eq 0 ]]; then
  nohup "$PYTHON" "$ROOT/eye_care.py" >>"$LOG_FILE" 2>&1 &
else
  nohup "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}" >>"$LOG_FILE" 2>&1 &
fi
echo "已在后台启动护眼锁屏助手 (PID $!)。"
echo "运行 ./stop_eye_care.sh 可结束；日志: $LOG_FILE"
