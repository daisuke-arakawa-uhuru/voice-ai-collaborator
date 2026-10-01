#!/usr/bin/env bash
# schedule.sh — トリガー方式の切り替え。会議頻度に合わせて柔軟に選べる。
#
#   bash scripts/schedule.sh run              # 今すぐ1回だけ実行（任意タイミング）
#   bash scripts/schedule.sh install watch    # イベント駆動: Amicalに新データができたら取り込む（推奨）
#   bash scripts/schedule.sh install hourly   # 定期: 1時間ごと（会議が少ない運用向け）
#   bash scripts/schedule.sh off              # 自動トリガーを止める（手動 run はいつでも可）
#   bash scripts/schedule.sh status           # 現在の状態
#
# ※ カレンダー連携や任意タイミングは、この 'run' を外部（Shortcuts/gcalcli/cron等）から呼べば実現できる。
# 生成される plist は ~/Library/LaunchAgents（端末ローカル・非共有）。実パスはそこにのみ入る。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$HOME/Library/Application Support/Amical/amical.db"
LA="$HOME/Library/LaunchAgents"
TPL="$ROOT/launchd"
LABELS=(ai.amical.pipeline.watch ai.amical.pipeline.hourly)

render() { sed -e "s|__PROJECT__|$ROOT|g" -e "s|__DB__|$DB|g" "$1" > "$2"; }
unload_all() { for l in "${LABELS[@]}"; do launchctl unload "$LA/$l.plist" 2>/dev/null || true; rm -f "$LA/$l.plist"; done; }

cmd="${1:-status}"; mode="${2:-}"
case "$cmd" in
  run)
    exec bash "$ROOT/scripts/pipeline.sh" ;;
  check|doctor)
    shift || true
    exec python3 "$ROOT/tools/wikicheck.py" "$@" ;;
  install)
    case "$mode" in
      watch|hourly) lbl="ai.amical.pipeline.$mode" ;;
      *) echo "使い方: schedule.sh install watch|hourly [research|research-web]"; exit 2 ;;
    esac
    research="${3:-}"                           # research（ローカル調査） / research-web（Web調査も）
    mkdir -p "$LA" "$ROOT/data/.logs"
    unload_all                                  # 片方だけ有効になるよう両方外してから入れる
    render "$TPL/$lbl.plist.template" "$LA/$lbl.plist"
    if [ "$research" = research ] || [ "$research" = research-web ]; then
      /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:RESEARCH string 1" "$LA/$lbl.plist" 2>/dev/null || true
      [ "$research" = research-web ] && /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:RESEARCH_WEB string 1" "$LA/$lbl.plist" 2>/dev/null || true
    fi
    launchctl load "$LA/$lbl.plist"
    echo "✔ 有効化: $lbl（mode=$mode${research:+, $research}）"
    [ "$mode" = watch ]  && echo "  → Amical に新しい文字起こしができると自動で取り込み・整形します。"
    [ "$mode" = hourly ] && echo "  → 1時間ごとに取り込み・整形します（間隔は plist の StartInterval で調整可）。"
    [ "$research" = research ]     && echo "  → 調査付き包括議事録も生成します（ローカルのみ）。"
    [ "$research" = research-web ] && echo "  → 調査付き包括議事録＋Web調査（一般化クエリのみ外部送信）。"
    ;;
  off|uninstall)
    unload_all
    echo "✔ 自動トリガーを停止しました（bash scripts/schedule.sh run で手動実行はいつでも可）。" ;;
  status)
    echo "== ~/Library/LaunchAgents の登録 =="
    ls "$LA"/ai.amical.pipeline.* 2>/dev/null || echo "  (自動トリガーなし＝手動運用)"
    echo "== launchd 稼働状況 =="
    launchctl list 2>/dev/null | grep amical || echo "  (ロードなし)"
    echo "== 直近ログ（data/.logs/pipeline.log 末尾）=="
    tail -n 3 "$ROOT/data/.logs/pipeline.log" 2>/dev/null || echo "  (まだ実行なし)"
    echo "== 健全性（鮮度・ズレ・人間ゲート）=="
    python3 "$ROOT/tools/wikicheck.py" 2>/dev/null | tail -1 || echo "  (checkできず)"
    echo "  詳細: bash scripts/schedule.sh check"
    ;;
  *)
    echo "使い方: schedule.sh {run | install watch|hourly [research|research-web] | check | off | status}" ;;
esac
