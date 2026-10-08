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
# 若安装目录在 Desktop/Documents/Downloads（TCC 受限），会同步到
# ~/Library/Application Support/eye-care-lock 再由 LaunchAgent 运行。

set -eo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LABEL="net.chinadong.eye-care"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SERVICE_DIR="$ROOT"
LOG_FILE="$ROOT/eye_care.log"

PYTHON=""
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
FILTERED=()
for arg in "$@"; do
  if [[ "$arg" == "--show-console" ]]; then
    SHOW_CONSOLE=1
  else
    FILTERED+=("$arg")
  fi
done

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

# Desktop/Documents/Downloads 受 TCC 保护，launchd 无法 posix_spawn 读取其中脚本
ensure_service_dir() {
  SERVICE_DIR="$ROOT"
  case "$ROOT" in
    "$HOME/Desktop"|"$HOME/Desktop"/*|"$HOME/Documents"|"$HOME/Documents"/*|"$HOME/Downloads"|"$HOME/Downloads"/*)
      SERVICE_DIR="$HOME/Library/Application Support/eye-care-lock"
      mkdir -p "$SERVICE_DIR"
      cp -f "$ROOT/eye_care.py" "$SERVICE_DIR/eye_care.py"
      if [[ -f "$ROOT/config.json" ]]; then
        cp -f "$ROOT/config.json" "$SERVICE_DIR/config.json"
      fi
      LOG_FILE="$SERVICE_DIR/eye_care.log"
      echo "提示: 安装目录在 TCC 受限路径，LaunchAgent 将从以下位置运行："
      echo "  $SERVICE_DIR"
      ;;
  esac
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
  local arg_xml="" a esc script
  script="$SERVICE_DIR/eye_care.py"
  mkdir -p "$(dirname "$PLIST")"
  arg_xml="$(printf '        <string>%s</string>\n        <string>%s</string>\n' \
    "$(xml_escape "$PYTHON")" "$(xml_escape "$script")")"
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
    <string>$(xml_escape "$SERVICE_DIR")</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
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
    if ! launchctl load "$PLIST" 2>>/tmp/eye_care_launchctl.err; then
      echo "launchctl 加载失败：" >&2
      cat /tmp/eye_care_launchctl.err >&2 || true
      return 1
    fi
  fi
  return 0
}

start_nohup() {
  # nohup 从用户脚本目录启动（保留 Desktop 上的 config）；无 Terminal
  if [[ ${#FILTERED[@]} -eq 0 ]]; then
    nohup "$PYTHON" "$ROOT/eye_care.py" >>"$ROOT/eye_care.log" 2>&1 &
  else
    nohup "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}" >>"$ROOT/eye_care.log" 2>&1 &
  fi
  echo "已用 nohup 后台启动 (PID $!)。无 Terminal 窗口。"
  echo "日志: $ROOT/eye_care.log"
}

if [[ "$SHOW_CONSOLE" -eq 1 ]]; then
  if [[ ${#FILTERED[@]} -eq 0 ]]; then
    exec "$PYTHON" "$ROOT/eye_care.py"
  fi
  exec "$PYTHON" "$ROOT/eye_care.py" "${FILTERED[@]}"
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  ensure_service_dir
  launchagent_bootout
  kill_stragglers
  write_launchagent_plist
  if launchagent_bootstrap; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      if pgrep -f "[e]ye_care.py" >/dev/null 2>&1; then
        echo "已通过 LaunchAgent 后台启动（无 Terminal）。"
        echo "Label: $LABEL"
        echo "plist: $PLIST"
        echo "运行目录: $SERVICE_DIR"
        echo "登录自启: 已启用（RunAtLoad）"
        echo "停止: ./stop_eye_care.sh"
        echo "日志: $LOG_FILE"
        exit 0
      fi
      sleep 0.25
    done
    echo "LaunchAgent 已加载但进程未起来（可能受 TCC 限制），回退 nohup…" >&2
  else
    echo "LaunchAgent 启动失败，回退 nohup…" >&2
  fi
  start_nohup
  # 即使回退 nohup，仍保留 plist，便于下次登录尝试自启
  exit 0
fi

LOG_FILE="$ROOT/eye_care.log"
start_nohup
