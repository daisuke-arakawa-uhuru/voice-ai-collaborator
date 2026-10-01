#!/usr/bin/env bash
# pipeline.sh — Amical → 整形ノート の一括処理。すべてのトリガー（手動/イベント/定期/カレンダー）の共通入口。
#
#   1) amical-export.py  : amical.db → data/sources/transcripts/（＋録音を recordings/ へ退避）
#   2) 各文字起こしを redact ゲート（HIGH秘密はスキップ）→ compile-note.py（冪等：既存はスキップ）
#   3) wikilint（描画チェック）
#
# 冪等なので何回・どの間隔で呼んでも安全。全処理ローカル（外部送信なし）。
# ログは data/.logs/pipeline.log（gitignore 済みの data 配下）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# 端末ごとのローカル設定（LLM_MODEL 等）。あれば読み込んで子プロセスに継承。
[ -f "$ROOT/config.env" ] && { set -a; . "$ROOT/config.env"; set +a; }
LOG_DIR="$ROOT/data/.logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pipeline.log"
ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

# 多重起動防止（前の実行が走っていたら抜ける）
LOCK="$LOG_DIR/pipeline.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  log "別のpipelineが実行中のためスキップ"; exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

log "pipeline start"

# 1) Amical DB → transcripts（＋録音退避）
if ! python3 scripts/amical-export.py --keep-audio >>"$LOG" 2>&1; then
  log "amical-export 警告（DBが無い/ロック等）。続行"
fi

# 2) redact ゲート → compile（冪等）
shopt -s nullglob
compiled=0; secret=0; skipped=0; failed=0
for f in data/sources/transcripts/*.md; do
  if ! python3 tools/redact.py "$f" >/dev/null 2>&1; then
    log "[skip:secret] $f （HIGH秘密検出→手動レビューへ）"; secret=$((secret+1)); continue
  fi
  out="$(python3 scripts/compile-note.py "$f" 2>>"$LOG")" || { log "[warn] compile失敗: $f"; failed=$((failed+1)); continue; }
  if echo "$out" | grep -q "整形ノート作成"; then log "compiled: $f"; compiled=$((compiled+1));
  else skipped=$((skipped+1)); fi
  # 任意: 調査付き包括議事録（RESEARCH=1）。web はさらに RESEARCH_WEB=1（一般化クエリのみ外部送信）
  if [ "${RESEARCH:-0}" = "1" ]; then
    webflag=""; [ "${RESEARCH_WEB:-0}" = "1" ] && webflag="--web"
    if python3 scripts/research-note.py "$f" $webflag >>"$LOG" 2>&1; then log "researched: $f (web=${RESEARCH_WEB:-0})"; else log "[warn] research失敗: $f"; fi
  fi
done

# 3) 描画チェック
python3 tools/wikilint.py --path wiki >/dev/null 2>&1 || true

# 4) 健全性・鮮度・ズレの気づき（読み取り専用・自動修正なし）をログに残す
health="$(python3 tools/wikicheck.py 2>/dev/null | tail -1)"
[ -n "$health" ] && log "health: $health"

log "pipeline done: compiled=$compiled skipped=$skipped secret=$secret failed=$failed"
