#!/usr/bin/env bash
# 结束护眼锁屏助手（macOS 优先 unload LaunchAgent，再清理本应用路径的进程）
# 用法: ./stop_eye_care.sh
# 说明: 默认保留 ~/Library/LaunchAgents/net.chinadong.eye-care.plist，
#       以便下次登录仍可自启；若需关闭登录自启，加 --disable-autostart
#       或在菜单栏取消「开机自动启动」。
#
# 进程匹配包括绝对路径，以及在本目录用 `python3 eye_care.py` 这种相对路径启动的进程。
# 不会结束其它目录里同名脚本。

set -eo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="net.chinadong.eye-care"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
APP_SUPPORT_SCRIPT="$HOME/Library/Application Support/eye-care-lock/eye_care.py"
DISABLE_AUTOSTART=0

process_cwd() {
  local pid="$1"
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/ { sub(/^n/, ""); print; exit }'
}

is_our_eye_care_pid() {
  local pid="$1" args cwd token resolved
  args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ -z "$args" ]] && return 1
  case "$args" in
    *"${APP_SUPPORT_SCRIPT}"*) return 0 ;;
    *"${ROOT}/eye_care.py"*) return 0 ;;
  esac
  token=""
  # shellcheck disable=SC2086
  for word in $args; do
    case "$word" in
      eye_care.py|./eye_care.py|*/eye_care.py) token="$word" ;;
    esac
  done
  [[ -z "$token" ]] && return 1
  if [[ "$token" == /* ]]; then
    resolved="$token"
  else
    cwd="$(process_cwd "$pid")"
    [[ -z "$cwd" ]] && return 1
    resolved="$(cd "$cwd" 2>/dev/null && pwd)/${token#./}"
  fi
  [[ "$resolved" == "$APP_SUPPORT_SCRIPT" || "$resolved" == "$ROOT/eye_care.py" ]]
}

collect_our_pids() {
  local pid
  for pid in $(pgrep -f "[e]ye_care.py" 2>/dev/null || true); do
    [[ -z "$pid" ]] && continue
    if is_our_eye_care_pid "$pid"; then
      printf '%s\n' "$pid"
    fi
  done
}

run_stop() {
  local arg
  DISABLE_AUTOSTART=0
  for arg in "$@"; do
    if [[ "$arg" == "--disable-autostart" ]]; then
      DISABLE_AUTOSTART=1
    fi
  done

  if [[ "$(uname -s)" == "Darwin" ]]; then
    local uid domain
    uid="$(id -u)"
    domain="gui/${uid}"
    launchctl bootout "${domain}/${LABEL}" >/dev/null 2>&1 || true
    if [[ -f "$PLIST" ]]; then
      launchctl bootout "$domain" "$PLIST" >/dev/null 2>&1 || true
      launchctl unload "$PLIST" >/dev/null 2>&1 || true
    fi
    echo "已请求 launchctl bootout: $LABEL"
  fi

  local pid
  local -a pids=()
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    pids+=("$pid")
  done < <(collect_our_pids | sort -u)

  if [[ ${#pids[@]} -gt 0 ]]; then
    for pid in "${pids[@]}"; do
      echo "正在结束本应用 PID $pid ..."
      kill "$pid" 2>/dev/null || true
    done
    sleep 1
    pids=()
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      pids+=("$pid")
    done < <(collect_our_pids | sort -u)
    for pid in "${pids[@]}"; do
      echo "强制结束本应用 PID $pid ..."
      kill -9 "$pid" 2>/dev/null || true
    done
  elif [[ "$(uname -s)" != "Darwin" ]]; then
    echo "未发现本应用路径下的 eye_care.py 进程。"
  fi

  if [[ "$DISABLE_AUTOSTART" -eq 1 && -f "$PLIST" ]]; then
    rm -f "$PLIST"
    echo "已删除 LaunchAgent plist，登录自启已关闭: $PLIST"
  elif [[ "$(uname -s)" == "Darwin" && -f "$PLIST" ]]; then
    echo "登录自启 plist 仍保留（下次登录会自启）: $PLIST"
    echo "若要关闭自启: $0 --disable-autostart  或菜单取消「开机自动启动」"
  fi

  echo "已请求退出。"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_stop "$@"
fi
