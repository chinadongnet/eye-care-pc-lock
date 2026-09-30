#!/usr/bin/env bash
# 结束护眼锁屏助手（macOS 优先 unload LaunchAgent，再清理残留进程）
# 用法: ./stop_eye_care.sh
# 说明: 默认保留 ~/Library/LaunchAgents/net.chinadong.eye-care.plist，
#       以便下次登录仍可自启；若需关闭登录自启，加 --disable-autostart
#       或在菜单栏取消「开机自动启动」。

set -eo pipefail

LABEL="net.chinadong.eye-care"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DISABLE_AUTOSTART=0

for arg in "$@"; do
  if [[ "$arg" == "--disable-autostart" ]]; then
    DISABLE_AUTOSTART=1
  fi
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  uid="$(id -u)"
  domain="gui/${uid}"
  launchctl bootout "${domain}/${LABEL}" >/dev/null 2>&1 || true
  if [[ -f "$PLIST" ]]; then
    launchctl bootout "$domain" "$PLIST" >/dev/null 2>&1 || true
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
  fi
  echo "已请求 launchctl bootout: $LABEL"
fi

PIDS="$(pgrep -f "[e]ye_care.py" || true)"
if [[ -n "$PIDS" ]]; then
  echo "$PIDS" | while read -r pid; do
    [[ -z "$pid" ]] && continue
    echo "正在结束 PID $pid ..."
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  LEFT="$(pgrep -f "[e]ye_care.py" || true)"
  if [[ -n "$LEFT" ]]; then
    echo "$LEFT" | while read -r pid; do
      [[ -z "$pid" ]] && continue
      echo "强制结束 PID $pid ..."
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
elif [[ "$(uname -s)" != "Darwin" ]]; then
  echo "未发现正在运行的 eye_care.py 进程。"
fi

if [[ "$DISABLE_AUTOSTART" -eq 1 && -f "$PLIST" ]]; then
  rm -f "$PLIST"
  echo "已删除 LaunchAgent plist，登录自启已关闭: $PLIST"
elif [[ "$(uname -s)" == "Darwin" && -f "$PLIST" ]]; then
  echo "登录自启 plist 仍保留（下次登录会自启）: $PLIST"
  echo "若要关闭自启: $0 --disable-autostart  或菜单取消「开机自动启动」"
fi

echo "已请求退出。"
