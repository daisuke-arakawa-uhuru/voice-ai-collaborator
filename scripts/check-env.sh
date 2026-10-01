#!/usr/bin/env bash
# 環境チェック: Docker / compose / ローカル LLM / マシンスペックを確認する。
set -uo pipefail

ok()   { printf "  \033[32m✔\033[0m %s\n" "$1"; }
ng()   { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m▲\033[0m %s\n" "$1"; }

echo "== マシンスペック =="
echo "  chip:   $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
MEM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))
echo "  memory: ${MEM_GB} GB"
if [ "$MEM_GB" -ge 32 ]; then ok "統合メモリ 32GB 以上（Whisper+LLM 同時実行に十分）"; else warn "32GB 未満。モデルサイズを絞るか同時実行に注意"; fi

echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
  ok "docker: $(docker --version)"
  if docker info >/dev/null 2>&1; then ok "docker daemon 稼働中"; else ng "docker daemon 停止中（Docker Desktop を起動）"; fi
  docker compose version >/dev/null 2>&1 && ok "compose: $(docker compose version | head -1)" || ng "docker compose plugin なし"
else
  ng "docker 未インストール"
fi

echo "== ローカル LLM =="
OLLAMA_URL="${LLM_BASE_URL:-http://localhost:11434/v1}"
# .env の host.docker.internal はホストからは localhost に読み替えて確認
HOST_URL=$(echo "$OLLAMA_URL" | sed 's#host.docker.internal#localhost#')
if curl -s --max-time 3 "${HOST_URL}/models" >/dev/null 2>&1 && curl -s --max-time 3 "${HOST_URL}/models" | grep -q '"id"'; then
  ok "LLM API 応答あり: ${HOST_URL}/models"
  echo "    models: $(curl -s --max-time 3 "${HOST_URL}/models" | grep -o '"id":"[^"]*"' | cut -d'"' -f4 | paste -sd', ' -)"
else
  ng "LLM API 応答なし: ${HOST_URL}  → Ollama/LM Studio を起動し、scripts/check-ollama.sh を参照"
fi

echo "== n8n コンテナ =="
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^amical-n8n$'; then
  ok "amical-n8n 稼働中 → http://localhost:5678"
else
  warn "amical-n8n 未起動（docker compose up -d で起動）"
fi
