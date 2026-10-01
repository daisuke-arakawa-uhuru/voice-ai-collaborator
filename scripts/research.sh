#!/usr/bin/env bash
# research.sh — 1つの文字起こしから「調査付き・包括議事録」を作る（redact ゲート付き）。
#
#   bash scripts/research.sh data/sources/transcripts/<会議>.md        # ローカルのみ（llm-wiki+LM Studio）
#   bash scripts/research.sh <会議> --web                              # Web調査も（★一般化クエリのみ外部送信）
#
# ローカルが既定。--web は会議本文を送らず、固有名詞を除いた一般化クエリだけを外部検索に出す。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
[ -f "$ROOT/config.env" ] && { set -a; . "$ROOT/config.env"; set +a; }
SRC="${1:-}"; shift || true
[ -z "$SRC" ] && { echo "使い方: bash scripts/research.sh <文字起こし> [--web]"; exit 2; }
[ -f "$SRC" ] || { echo "見つかりません: $SRC"; exit 2; }

echo "== redact ゲート（HIGH秘密検出で停止）=="
if ! python3 tools/redact.py "$SRC" >/dev/null 2>&1; then
  echo "✗ HIGH秘密を検出。tools/redact.py \"$SRC\" で確認し、--apply でマスク後に再実行。"; exit 1
fi
echo "== 調査付き包括議事録を生成 =="
python3 scripts/research-note.py "$SRC" "$@"
