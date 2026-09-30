#!/usr/bin/env bash
# 结束护眼锁屏助手进程（按命令行匹配 eye_care.py）
# 用法: ./stop_eye_care.sh

set -eo pipefail

PIDS="$(pgrep -f "[e]ye_care.py" || true)"
if [[ -z "$PIDS" ]]; then
  echo "未发现正在运行的 eye_care.py 进程。"
  exit 0
fi

echo "$PIDS" | while read -r pid; do
  [[ -z "$pid" ]] && continue
  echo "正在结束 PID $pid ..."
  kill "$pid" 2>/dev/null || true
done

# 给优雅退出一点时间，再强制
sleep 1
LEFT="$(pgrep -f "[e]ye_care.py" || true)"
if [[ -n "$LEFT" ]]; then
  echo "$LEFT" | while read -r pid; do
    [[ -z "$pid" ]] && continue
    echo "强制结束 PID $pid ..."
    kill -9 "$pid" 2>/dev/null || true
  done
fi
echo "已请求退出。"
