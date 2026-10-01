#!/usr/bin/env bash
# dashboard.command — ダブルクリックで Amical 司令塔ダッシュボードを開く。
# （デスクトップにこのファイルのエイリアスを置けば、アイコンのダブルクリックで起動できる）
#
#   1) ダッシュボードのローカルサーバー(127.0.0.1:8787)を起動（既に起動していれば再利用）
#   2) 既定ブラウザでダッシュボードを開く
#
# サーバーは会議データを表示するが 127.0.0.1 限定・許可アクションのみ実行（server.py 参照）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8787
LOGDIR="$ROOT/data/.logs"; mkdir -p "$LOGDIR"
LA="$HOME/Library/LaunchAgents"; PLIST="$LA/ai.amical.dashboard.plist"

# --- ログイン時の自動常駐（任意）---
case "${1:-}" in
  --install-autostart)
    mkdir -p "$LA"
    sed "s|__PROJECT__|$ROOT|g" "$ROOT/launchd/ai.amical.dashboard.plist.template" > "$PLIST"
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" && echo "✔ ログイン時にダッシュボードを自動常駐します（$PLIST）"
    exit 0 ;;
  --uninstall-autostart)
    launchctl unload "$PLIST" 2>/dev/null || true; rm -f "$PLIST"
    echo "✔ 自動常駐を解除しました（手動起動はこのファイルのダブルクリックで可）"; exit 0 ;;
esac

# 既に起動済みか（/api/status が応答するか）
if ! curl -s --max-time 2 "http://127.0.0.1:${PORT}/api/status" >/dev/null 2>&1; then
  echo "ダッシュボードサーバーを起動します…"
  nohup python3 "$ROOT/dashboard/server.py" --port "$PORT" >"$LOGDIR/dashboard.log" 2>&1 &
  # 起動待ち（最大10秒）
  for i in $(seq 1 20); do
    curl -s --max-time 1 "http://127.0.0.1:${PORT}/api/status" >/dev/null 2>&1 && break
    sleep 0.5
  done
else
  echo "ダッシュボードは既に起動中です。"
fi

open "http://127.0.0.1:${PORT}/"
echo "→ http://127.0.0.1:${PORT}/  （このウィンドウは閉じてOK。サーバーは動き続けます）"
