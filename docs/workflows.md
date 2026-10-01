# ワークフロー構築レシピ（n8n）

3つのタスクを n8n 上でどう組むかのノード単位手順。`00`/`01` はインポート可能な JSON を同梱（`n8n/workflows/`）。Task1〜3 は本書のレシピに沿って UI で組み上げる。

> **方針（2026-09-29 変更）**: **GitHub 等への外部起票はしない。すべてローカル完結。**
> ネクストアクションはローカルの Markdown チェックリストとして `data/wiki/` に保存する（SOUL 原則2）。
> 整形は `scripts/compile-note.py`（`SOUL.md`/`AGENT.md` を読み込む llm-wiki `ingest` のローカル移植）と同じ書式に揃える。
> n8n を使わず**スクリプト単体でも** `python3 scripts/compile-note.py <文字起こし>` で ingest できる。

インポート方法: n8n UI 右上 → **Import from File** → `n8n/workflows/*.json` を選択。

---

## 前提: 共通の LLM 呼び出し（Ollama / LM Studio）

HTTP Request ノードで OpenAI 互換エンドポイントを叩くのが最も安定（LangChain 系ノードのバージョン差異を避けられる）。

- **URL**: `http://host.docker.internal:1234/v1/chat/completions`（LM Studio。Ollama は `:11434`）
- **Method**: POST / **Body**: JSON
- **Body 例**:
  ```json
  {
    "model": "google/gemma-4-e4b",
    "messages": [{ "role": "user", "content": "={{ $json.prompt }}" }],
    "stream": false,
    "temperature": 0.1,
    "max_tokens": 3000
  }
  ```
- 応答本文は `{{ $json.choices[0].message.content }}` で取り出す。

> ⚠️ **LM Studio のモデル選定と落とし穴（2026-09-29 実測）**
> - ロード済みモデルは **reasoning 系**（`message.reasoning_content` に思考が入り、`reasoning_tokens` を消費）。**`max_tokens` を小さくすると思考で使い切って `content` が空になる**（3000 以上を推奨）。
> - `qwen/qwen3.8-27b` は思考が長く低速（長文抽出で 120 秒超もあり）。**`google/gemma-4-e4b` が高速（サンプル約 17 秒）で JSON 抽出も安定**したため既定に採用。
> - `content` は **` ```json ... ``` ` のコードフェンス付きで返ることがある**。Code ノードでフェンスを剥いでから `JSON.parse` する（パース失敗時はリトライ or `needs-review` へ）。

---

## Task1: ネクストアクションの抽出（ローカル・チェックリスト化）

```
議事録フォルダ監視(Local File Trigger)
  → ファイル読込(Read/Write Files from Disk)
  → テキスト抽出(Extract From File: text)
  → LLMでタスク抽出(HTTP Request → LM Studio)   ※JSON配列で出力させる
  → JSONパース(Code)
  → Markdownチェックリストに整形(Code)
  → ローカル保存(Read/Write Files from Disk: write → data/wiki/meetings/<日付>-tasks.md)
```

1. **トリガー**: このビルドは Local File Trigger 非対応。**Schedule Trigger（1分ごと）**で `data/sources/transcripts` を見に行く（`02` を流用）。
2. **Read/Write Files from Disk**（read）: `fileSelector = /data/sources/transcripts/*.md`。
3. **Extract From File**: operation=`Text`（バイナリ→文字列）。
4. **HTTP Request（LM Studio）**: プロンプト例:
   > 以下の議事録からネクストアクションを抽出し、JSON 配列のみで出力。各要素は
   > `{"title":"<40字以内>","body":"<担当者・期限・背景>","assignee":"<任意>"}`。
   > 該当なしなら `[]`。前後の説明文は出力しない。固有名詞・数値は一般化する。
   > 議事録:\n```\n{{ $json.data }}\n```
5. **Code**: フェンスを剥いでから配列を items 化する。
   ```js
   let s = $input.first().json.choices[0].message.content.trim();
   if (s.startsWith('```')) s = s.replace(/^```[a-z]*\n?/,'').replace(/```$/,'').trim();
   return JSON.parse(s).map(x => ({ json: x }));   // パース失敗時は try/catch でリトライ or needs-review
   ```
6. **Markdown 化 & 保存**: 各アクションを `- [ ] [担当] タイトル（背景）` に整形し、
   `data/wiki/meetings/<日付>-tasks.md` へ Read/Write Files ノードで書き出す（**外部起票しない**）。

> ⚠️ **セキュリティ**: ネクストアクションはローカルの Markdown に残すだけ。GitHub 等の外部サービスへは送らない。
> 将来どうしても外部起票が要る場合でも、LLM に「タスク要約のみ・固有名詞や数値は一般化」と指示し、外部に出る情報を最小化する（SOUL 原則2）。

---

## Task2: 疑問点の追加調査

```
（Task1と同じ入力）
  → LLMで疑問抽出(HTTP Request)     ※「調べるべき論点」を配列で
  → 分岐(IF: Web検索が要るか)
      ├─ 要る: Web検索(HTTP Request: Brave/DuckDuckGo) → LLMで要約
      └─ 不要: LLMで解説生成
  → 調査結果をまとめる(Set)
```

- Web 検索を挟む場合、**検索クエリにも生の固有名詞を載せない**（一般化した語で検索）。会議本文は送らない。
- 出力は Task3 の入力（wiki ノートの素材）として次段に渡す。

---

## Task3: 整形ノートを wiki に保存（llm-wiki `ingest` のローカル移植）

```
（議事録 + Task2の調査結果）
  → LLMでノート整形(HTTP Request)   ※SOUL.md/AGENT.md をシステムプロンプトに載せる
  → ファイル書き出し(Read/Write Files from Disk: write)
      出力先: data/wiki/meetings/<日付>-<会議名>.md
  → index 追記
```

このタスクは **`scripts/compile-note.mjs`（＝`02`）と同じ処理**。`02` がそのまま Task3 の実体。
n8n コンテナには python が無いため、Execute Command からは **node 版**を呼ぶ:

```
Execute Command: node /project/scripts/compile-note.mjs --all
```

- **システムプロンプトに `SOUL.md` + `AGENT.md` を必ず載せる**（ローカルAIが原則・書式を常に持った状態でコンパイルする）。
- 出力書式は `AGENT.md`「整形ノートの書式」に厳密に従う（frontmatter + 概要/決定事項/ネクストアクション/論点/関連/ソース + `[[wikilink]]`）。
- `sources/` の本文は読むだけ・**改変しない**。

> 🛑 **統制方針との整合**: 「書くタイミングは人が決める・自動増殖しない」（SOUL 原則3）。
> 自動で走らせる場合も、生成物は `data/wiki/` にとどめ、**元のナレッジベース（`$KB_VAULT_ROOT`）へは自動で書き込まない**。
> 元のナレッジベース への反映が要るときは、人が改めて `llm-wiki` スキルで ingest を判断する。

---

## トリガの方式（このビルドでの結論）

- **Local File Trigger は使えない**（`Unrecognized node type`）。→ **Schedule Trigger（ポーリング）＋ Execute Command** が本命（`02`）。
- Docker for Mac のファイル監視は元々不安定なので、ポーリングの方が堅牢。1分間隔で十分。
- リアルタイム性が要るなら **Webhook 方式**も可: Webhook ノード＋ Amical 側（or 監視スクリプト）から `curl -X POST http://localhost:5678/webhook/xxx`。
- n8n を介さず、**ホストで `python3 scripts/compile-note.py <文字起こし>`** を直接叩くのが最短。
