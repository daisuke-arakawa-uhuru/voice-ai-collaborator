#!/usr/bin/env bash
# ingest.sh — 取り込みゲート（llm-wiki の「file_to_md → redact → mask_paths → ingest」を移植）
#
# 会議の文字起こしを整形ノート化する前に、機微情報・実パスを検査する“関所”。
# sources 本文は改変しない（唯一の例外は redact による機微情報マスキングで、それも --apply は人が判断）。
#
# 使い方:
#   bash scripts/ingest.sh data/sources/transcripts/<会議>.md
#     .md/.txt      … redact/mask_paths ゲート → compile-note.py で整形 → wikilint
#     pdf/docx/pptx … file_to_md で data/inbox に Markdown 化して停止（レビュー後に再実行）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/config.env" ] && { set -a; . "$ROOT/config.env"; set +a; }
SRC="${1:-}"
[ -z "$SRC" ] && { echo "使い方: bash scripts/ingest.sh <文字起こしファイル>"; exit 2; }
[ -f "$SRC" ] || { echo "ファイルが見つかりません: $SRC"; exit 2; }

ext="$(echo "${SRC##*.}" | tr '[:upper:]' '[:lower:]')"
case "$ext" in
  md|markdown|txt) : ;;
  *)
    echo "== [file_to_md] .$ext を Markdown 化して data/inbox へ =="
    python3 "$ROOT/tools/file_to_md.py" "$SRC"
    echo "→ data/inbox の md を確認し、data/sources/transcripts/ へ移してから再実行してください。"
    exit 0 ;;
esac

echo "== [1/4 redact] 機微情報スキャン（ドライラン・HIGH検出で停止）=="
if ! python3 "$ROOT/tools/redact.py" "$SRC"; then
  echo
  echo "✗ HIGH 信頼度の秘密（鍵・トークン等）を検出。安全側でここで停止します。"
  echo "  内容を確認し、必要なら次でマスク（sources 改変はマスキングのみ許可）:"
  echo "    python3 tools/redact.py \"$SRC\" --apply"
  echo "  そのうえで再実行してください。"
  exit 1
fi

echo "== [2/4 mask_paths] 実パス（/Users/<user>）スキャン =="
python3 "$ROOT/tools/mask_paths.py" "$SRC"
echo "  （実パスが出た場合は 'python3 tools/mask_paths.py \"$SRC\" --apply' で \$HOME 表記へ）"

echo "== [3/4 compile] 整形ノート生成（ローカルLLM・SOUL/AGENT読込）=="
python3 "$ROOT/scripts/compile-note.py" "$SRC"

echo "== [4/4 wikilint] 生成ノートの描画チェック（Obsidian/Quartz）=="
python3 "$ROOT/tools/wikilint.py" --path wiki || true

echo
echo "✔ ingest 完了: sources 本文は未改変。整形ノートは data/wiki/ に出力しました。"
echo "  氏名など文脈依存の個人情報は正規表現で拾えません。必要なら目視レビューを。"
