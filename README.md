# voice-ai-collaborator

Meet の会議を **Amical**（ローカル文字起こし）で議事録化し、**すべてローカルで完結**して

1. **文字起こしを raw のまま保存**（`data/sources/`）
2. **ローカルLLMで整形ノート化**（`data/wiki/` に要約・決定事項・ネクストアクション）
3. **ネクストアクションはローカルのチェックリスト**として保持（GitHub 等へ**起票しない**）
4. **（任意）調査付き包括議事録**: 論点を並列調査し、**llm-wiki を引用**（＋任意でWeb）して AI が回答・判断を付ける

する個人検証プロジェクト。外部送信・API 従量課金ゼロ。
データの持ち方は **llm-wiki スタイルの二層構造（raw 不変＋wiki 整形）** を採用している。

> はじめて読む人へ: 各ソフトが何の役割かは **[docs/glossary.md](./docs/glossary.md)** の早見表を最初に見てください。
> このローカルAIの原則は **[SOUL.md](./SOUL.md)**、手順・書式は **[AGENT.md](./AGENT.md)**（毎回読み込む前提）。

## アーキテクチャ

```
[Google Meet / マイク]
      │ 音声
      ▼
Amical（ローカル）  ── Whisper 文字起こし。結果は内部DB(amical.db)に保存（★フォルダには出さない）
      │
      ▼
scripts/amical-export.py   ── amical.db → data/sources/transcripts/*.md（＋録音を recordings/ へ退避）
      │ raw・不変
      ▼
scripts/ingest.sh (or n8n 02)  ── redact→mask_paths→compile（SOUL/AGENT読込）→wikilint
      │
      ▼
data/wiki/meetings/<日付>-<会議>.md  （整形ノート：概要／決定事項／ネクストアクション／論点／関連）
```

> ⚠️ Amical と n8n は**自動では繋がらない**。Amical は文字起こしを `amical.db`（SQLite）に貯め、
> 録音は `$TMPDIR`（一時・自動削除）に置く。`scripts/amical-export.py` が DB からフォルダへ橋渡しする。詳細は [docs/amical-config.md](./docs/amical-config.md)。

**入口=Amical（音声→文字）／頭脳=LM Studio（文字→整形）／配管=n8n（自動化）／保管=data/（llm-wiki型二層）**

## ディレクトリ構成

```
voice-ai-collaborator/
├── SOUL.md                 # ローカルAIの魂・原則（毎回最初に読む）
├── AGENT.md                # 運用マニュアル・書式（llm-wiki 規約の移植）
├── docker-compose.yml      # n8n（ホスト側 LM Studio に接続）
├── .env.example            # 環境変数サンプル（→ .env にコピー。.env は非追跡）
├── data/                   # ★中身はすべて git 非追跡（会議データは機密混入しうる）
│   ├── inbox/              # 未整理の取り込み待ち・判断保留
│   ├── sources/            # raw 原文（無加工・不変）
│   │   ├── recordings/     # 音声録音
│   │   └── transcripts/    # 文字起こし
│   └── wiki/               # 構造化・整形済みノート
│       ├── index.md        # 会議まとめカタログ
│       ├── meetings/       # 会議ごとのまとめ
│       └── topics/         # 横断トピック（任意）
├── n8n/workflows/          # インポート用ワークフロー JSON
│   ├── 00-smoke-llm.json           # n8n→LM Studio 疎通
│   ├── 01-file-trigger-detect.json # （参考）Local File Trigger 版。※このビルドは非対応→02を使う
│   └── 02-auto-compile.json        # Schedule + Execute Command で新規文字起こしを自動整形
├── scripts/
│   ├── check-env.sh        # 環境一括チェック
│   ├── check-llm.sh        # LM Studio/Ollama 疎通＋モデル確認
│   ├── amical-export.py    # Amical DB(amical.db) → data/sources/transcripts/（＋録音退避）
│   ├── pipeline.sh         # 取り込み一括（export→redactゲート→compile→wikilint[→調査]）。全トリガーの共通入口
│   ├── schedule.sh         # トリガー切替（watch/hourly/manual/off [research]）
│   ├── research-note.py    # 調査付き包括議事録（論点抽出→並列でllm-wiki検索/Web→AI合成・引用付き）
│   ├── research.sh         # 上記の単発版（redactゲート付き）
│   ├── ingest.sh           # 取り込みゲート単発版: redact→mask_paths→整形→wikilint（llm-wiki移植）
│   ├── compile-note.py     # 文字起こし→整形ノート（ホスト実行・python・冪等）
│   └── compile-note.mjs    # 同上（n8nコンテナ内 node で実行・Execute Command用）
├── launchd/                # launchd テンプレ（watch=イベント駆動 / hourly=定期）。実パスは含まない
├── tools/                  # llm-wiki（ナレッジベース） から移植（パス既定のみ調整）
│   ├── redact.py           # 機微情報(鍵/トークン/PII)マスキング（HIGHで取り込み停止）
│   ├── mask_paths.py       # 実パス /Users/<user> を $HOME 表記へ
│   ├── wikilint.py         # Markdown 描画チェック（Obsidian/Quartz）
│   ├── wikicheck.py        # 鮮度・ズレ・人間ゲートの点検（読み取り専用・提案のみ）
│   └── file_to_md.py       # PDF/Office → Markdown（要 markitdown）
└── docs/
    ├── glossary.md         # ソフトの役割・LM Studio エンドポイント早見表
    ├── workflows.md        # Task1〜3 のノード単位構築レシピ
    └── amical-config.md    # Amical 側の設定メモ
```

> ⚠️ `data/` の **中身は git に乗らない**（`.gitignore` 済み、構造の `.gitkeep` だけ追跡）。
> 会議データには顧客機密・個人情報が混入しうるため。

## ダッシュボード（司令塔UI）

会議 → Amical文字起こし → export → 整形 → 包括議事録 の**各段の状態を1画面で俯瞰**し、
アプリ起動やパイプライン実行を**ボタンで**行えるローカルUI。

```bash
bash scripts/dashboard.command          # サーバー起動＋ブラウザで開く（ダブルクリックでも可）
open http://127.0.0.1:8787/             # 既に常駐していれば直接開く
```

- **表示**: LM Studio / Amical / n8n / 自動トリガー(launchd) の稼働状態、取り込みフローの俯瞰
  （総録音・包括済・未整形・空の件数）、日毎テーブル、録音ごとの段階（DB内→export済→整形済→包括済✓）。
- **操作ボタン**: 全部起動（LM Studio/Amical/n8n）／モデル読込(warmup)／今すぐ取り込み実行／議事録フォルダを開く／健全性チェック。
- **ログイン時に自動常駐**（推奨）: `bash scripts/dashboard.command --install-autostart`
  （解除は `--uninstall-autostart`）。以後は PC 起動後いつでも `http://127.0.0.1:8787/` で開ける。
- **デスクトップから起動**: `scripts/dashboard.command` のエイリアスをデスクトップに置けば、アイコンのダブルクリックで開く。

> 🔒 セキュリティ: サーバーは **127.0.0.1 のみ bind**（LAN不可）。ボタンが叩けるのは `dashboard/server.py` の
> **固定許可リストのアクションだけ**（任意コマンド実行不可）。会議本文は表示せず先頭要約のみ。全データはローカル。

## セットアップ

### 前提

- macOS + Docker Desktop（稼働中）/ docker compose v2 以上
- 統合メモリ 32GB 以上推奨。検証端末: Apple M5 Pro / 64GB / macOS 26.6 → 余裕

### 1. ローカル LLM（LM Studio）

LM Studio を起動 → モデルをロード → **Developer → Local Server を ON**（Port 1234）。

```bash
bash scripts/check-llm.sh   # 疎通＋ロード済みモデル＋チャット補完を確認
```

既定モデルは `google/gemma-4-e4b`（高速・JSON安定）。エンドポイントの選択肢は [docs/glossary.md](./docs/glossary.md)。
Ollama を使う場合は `.env` の `LLM_BASE_URL=http://localhost:11434/v1`・`LLM_MODEL=qwen2.5:7b` 等に変更。

### 2. いちばん簡単な使い方（n8n なしでも動く）

文字起こしを `data/sources/transcripts/` に置いて、**取り込みゲート**を通す:

```bash
bash scripts/ingest.sh data/sources/transcripts/<会議>.md
#  redact(機微情報) → mask_paths(実パス) → compile(整形) → wikilint(描画) を一括
#  → data/wiki/meetings/<日付>-<会議>.md を生成、index に追記、sources 本文は無変更
```

ゲート無しで整形だけしたいなら `python3 scripts/compile-note.py <会議>.md`。

### 3. n8n で自動化する場合

```bash
cp .env.example .env
docker compose up -d
open http://localhost:5678       # 初回はローカルアカウント作成
bash scripts/check-env.sh        # Docker / LLM / n8n / スペックを一括確認
```

そのあとの n8n 操作（Assistantのスキップ／ワークフローのインポート／`00`疎通／`01`フォルダ監視／Task配線）は
**[docs/n8n-setup.md](./docs/n8n-setup.md)** に手順化。Task1〜3 のノード設計は [docs/workflows.md](./docs/workflows.md)。

## 検証チェックリスト（進捗）

- [x] マシンスペック確認（M5 Pro / 64GB）
- [x] Docker / compose 稼働確認
- [x] プロジェクト雛形・compose・スクリプト作成、git 初回コミット
- [x] n8n Docker 起動確認（v2.40.7 / healthz 200）
- [x] LM Studio 疎通（ホスト＋n8nコンテナ→:1234 の両経路）
- [x] **文字起こし→整形ノート（compile-note.py）をローカルLLMだけで実証**（gemma-4-e4b）
- [x] データ構造を llm-wiki 型（sources/wiki/inbox）へ再編、SOUL.md/AGENT.md 整備
- [x] llm-wiki tool（redact/mask_paths/wikilint/file_to_md）を `tools/` へ移植、`ingest.sh` ゲート化・検証
- [x] n8n ダッシュボード起動・ローカルアカウント作成、設定手順を `docs/n8n-setup.md` 化
- [x] `00` を n8n UI でインポート実行 → n8n→LM Studio 疎通成功（Workflow executed successfully）
- [x] トリガー方式を確定: `localFileTrigger` は当ビルド非対応 → **`02` Schedule+Execute Command** に切替、`docker exec` で検証済み（新規整形・冪等スキップ動作OK）
- [x] **Amical 連携の解明＋橋渡し**: Amical はDB(amical.db)保存・録音は$TMPDIR と判明。`scripts/amical-export.py` 新設し、実録音3件を transcripts/→ingest→wiki まで実データで実証
- [x] **柔軟なトリガー基盤**: `pipeline.sh`（冪等）＋`schedule.sh`（watch/hourly/manual/off）＋launchdテンプレ。**watch(イベント駆動)を有効化・launchd発火まで検証**。カレンダー連携もフックで差し込み可（`docs/scheduling.md`）
- [ ] Meet のシステム音声取り込み（BlackHole 等）の要否判断 ← **B: いまここ**
- [ ] （任意）`02` を n8n で Active 化 / Task2 追加

## セキュリティ方針

- **会議データは外に出さない**: `data/` は git 非追跡。LLM 解析はローカル LM Studio/Ollama のみ。
- **取り込みゲート**: `scripts/ingest.sh` が redact（機微情報）→ mask_paths（実パス）を検査。HIGH な秘密を検出したら整形せず停止（llm-wiki の入力ポリシーを移植）。
- **外部起票しない**: ネクストアクションはローカルの Markdown チェックリスト（GitHub 等へ送らない）。
- **llm-wiki（ナレッジベース） へ自動反映しない**: 生成物は `data/wiki/` まで。ナレッジベース Vault への ingest は人が `llm-wiki` スキルで判断（SOUL 原則3）。
- 原則の正典は [SOUL.md](./SOUL.md) / [AGENT.md](./AGENT.md)、取り込み手順は [AGENT.md の取り込みフック](./AGENT.md)。
