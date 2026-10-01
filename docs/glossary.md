# 用語・ソフトウェア解説

このプロジェクトで出てくるソフトが「何のために・どの役割で」使われているかの早見表。
初めて読む人はまずここを見れば全体像がつかめる。

## 登場ソフトウェアの役割

| ソフト | 種別 | このプロジェクトでの役割 | なぜ使うのか（採用理由） |
|---|---|---|---|
| **Amical** | ローカル文字起こしアプリ | 会議音声を聞き取り、テキスト（議事録）に変換する入口 | Meet の音声をローカルで文字起こしでき、Whisper＋ローカルLLMを内蔵。クラウドに音声を送らずに済む |
| **Whisper** | 音声認識モデル（OpenAI） | Amical の中で動く「聞き取りエンジン」。音声→文字 | 高精度な多言語音声認識。`Large-v3-Turbo` は精度と速度のバランスが良い |
| **LM Studio** | ローカルLLM実行アプリ | 文字起こしの整形・要約・アクション抽出を行う「頭脳」。OpenAI互換APIをローカルに立てる | GUIでモデルのロード/切替が楽。OpenAI互換APIなので既存ツールから差し替えやすい。**今回採用** |
| **Ollama** | ローカルLLM実行ツール（CLI） | LM Studio の代替。同じくローカルでLLMを動かしOpenAI互換APIを提供 | CLI/サービス運用向き。`ollama pull` でモデル管理。今回は使わないが差し替え可能 |
| **n8n** | ワークフロー自動化ツール（OSS） | 「文字起こしが増えた→整形→保存」の一連を自動でつなぐ**配管・司令塔** | GUIでノードを繋ぐだけで自動化を組める。ファイル監視・HTTP・分岐・スケジュールが揃い、LLMノードのURLをローカルに向けられる |
| **Docker / docker compose** | コンテナ実行基盤 | n8n を隔離環境で起動・管理する土台 | 「どのPCでも同じ状態で n8n が動く」を保証。インストール汚染がなく、up/down が一発 |
| **host.docker.internal** | Docker の特殊ホスト名 | コンテナ（n8n）から母艦PCの LM Studio(:1234) に接続するための名前 | コンテナ内の `localhost` は母艦ではないため。これで「コンテナ→ホストのLLM」を繋ぐ |
| **Obsidian / llm-wiki** | ナレッジ管理（Markdown Vault） | 整形済みノートの保存形式・思想のモデル。raw不変＋wiki整形の二層構造を借用 | 既にllm-wiki スタイルのナレッジベース のノウハウ（frontmatter・wikilink・sources/wiki分離）をローカルに横展開できる |

## データの流れ（どこで何が起きるか）

```
会議音声
  │  ← Amical が Whisper で聞き取り
文字起こしテキスト  →  data/sources/transcripts/ に保存（raw・無加工・不変）
  │  ← n8n が検知し、LM Studio に「整形して」と依頼
整形ノート          →  data/wiki/meetings/ に保存（要約・アクション・wikilink）
```

- **入口 = Amical**（音声→文字）／ **頭脳 = LM Studio**（文字→整形）／ **配管 = n8n**（自動でつなぐ）／ **保管 = data/（llm-wiki型二層）**。

## LM Studio のエンドポイント早見表（2026-09-29 実測）

同じ :1234 でも複数のAPIがある。用途で使い分ける。

| エンドポイント | 形式 | 実挙動 | 使いどころ |
|---|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat Completions 互換 | ✅ 200 | **本プロジェクト既定**。n8n・Ollama とも互換で移植しやすい。`max_tokens` 指定可（reasoningモデルは3000以上） |
| `POST /api/v1/chat` | LM Studio 新チャットAPI（`system_prompt`/`input`） | ✅ 200 | 応答が `output:[{type:"reasoning"},{type:"message"}]` と**思考と本文を分離**。max_tokens枯渇問題を避けたい時に有利（`max_tokens`キーは非対応） |
| `POST /v1/responses` | OpenAI Responses API | ✗ 000（этой版で無効） | 将来LM Studioが対応したらツール呼び出し用に。現状は使わない |
| `GET /api/v0/models` | LM Studio ネイティブREST（beta） | ✅ 200 | ロード済みモデルの詳細確認 |
| `GET /v1/models` | OpenAI 互換 | ✅ 200 | モデル一覧取得（スクリプトの疎通確認で使用） |

### `/api/v1/chat` の応答例（思考と本文が分離される）

```json
{
  "output": [
    { "type": "reasoning", "content": "…思考…" },
    { "type": "message",   "content": "…ユーザーに返す本文…" }
  ],
  "stats": { "tokens_per_second": 65.7, "reasoning_output_tokens": 29 }
}
```

本文の取り出しは `output.find(o => o.type === "message").content`。
`/v1/chat/completions` の場合は `choices[0].message.content`（reasoning は `message.reasoning_content` に別途入る）。

### API キーの扱い（よくある詰まり）

**LM Studio に API キーは存在しない**（ローカルサーバーなので発行画面もない）。
ただし n8n や OpenAI SDK 系のクライアントは「キー欄が空だとエラー」にするため、
**ダミー文字列（例 `lm-studio`）を入れる**。実測で任意のキーでも HTTP 200（LM Studio は中身を検証しない）。
詳しい n8n の手順は [n8n-setup.md](./n8n-setup.md)。
