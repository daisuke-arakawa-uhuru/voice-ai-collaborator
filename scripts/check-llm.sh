#!/usr/bin/env bash
# ローカル LLM（LM Studio / Ollama）の疎通確認 + チャット補完テスト。
# デフォルトは LM Studio(:1234)。LLM_BASE_URL / LLM_MODEL で上書き可。
set -uo pipefail

BASE="${LLM_BASE_URL:-http://localhost:1234/v1}"
BASE="${BASE%/}"                       # 末尾スラッシュ除去
HOST_BASE=$(echo "$BASE" | sed 's#host.docker.internal#localhost#')

echo "== LLM エンドポイント: ${HOST_BASE} =="
if ! curl -s --max-time 3 "${HOST_BASE}/models" >/dev/null 2>&1; then
  cat <<EOF
  ✗ 応答なし。
    - LM Studio: アプリ → Developer → Local Server を起動（Port 1234）し、モデルをロード
    - Ollama:    'ollama serve' or Ollama.app 起動後、LLM_BASE_URL=http://localhost:11434/v1
EOF
  exit 1
fi

echo "  ✔ 応答あり。ロード済みモデル:"
curl -s --max-time 3 "${HOST_BASE}/models" | python3 -c "import sys,json;[print('    -',m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null

MODEL="${LLM_MODEL:-$(curl -s --max-time 3 "${HOST_BASE}/models" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)}"
echo "== チャット補完テスト（model=${MODEL}）=="
RESP=$(curl -s --max-time 90 "${HOST_BASE}/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"日本語で『疎通OK』とだけ返して\"}],\"stream\":false}")
echo "  応答: $(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null || echo "パース失敗: $RESP")"
