# AGENT.md — 運用マニュアル（ローカルAIの手順書）

> [SOUL.md](./SOUL.md)（魂・原則）を読んだ前提で、**具体的な手順・書式**を定める。
> ローカルAI（n8n のシステムプロンプト経由）も、Claude Code もこれに従う。
> llm-wiki スキルの規約をこのローカル環境向けに移植したもの。

## ディレクトリ構造（llm-wiki 二層構造の移植）

```
data/
├── inbox/                 # 未整理の取り込み待ち（Amical 出力の初期受け・判断保留）
├── sources/              # raw 原文（無加工・不変。SOUL 原則1）
│   ├── recordings/       # 音声録音（.m4a / .wav 等）
│   └── transcripts/      # 文字起こし（.md / .txt）
└── wiki/                 # 構造化・整形済みノート
    ├── index.md          # まとめカタログ（新ノート追加のたび更新）
    ├── meetings/         # 会議ごとのまとめノート
    └── topics/           # 会議をまたぐ横断トピック（任意）
```

対応関係: `sources/` ＝ llm-wiki の `sources/<genre>/`、`wiki/` ＝ `wiki/<genre>/`、`inbox/` ＝ `inbox/`。

## 動詞（やること）

| 動詞 | 入力 | 出力 | 実体 |
|---|---|---|---|
| `ingest` | `data/sources/transcripts/<file>` | `data/wiki/meetings/<日付>-<slug>.md` | `bash scripts/ingest.sh <file>`（下記フック）。単発は `scripts/compile-note.py`、自動は n8n `02`(`compile-note.mjs`) |
| `query` | 質問 | 回答 | `wiki/index.md` → 該当ノートを読んで答える（原文まで遡る） |
| `research` | `data/sources/transcripts/<file>` | `data/wiki/meetings/<slug>.包括.md` | `bash scripts/research.sh <file>`。論点を並列調査（llm-wiki引用＋任意Web）→AI回答・判断付き包括議事録。Webは明示オプトイン・一般化クエリのみ |
| `check` | `data/` 全体 | レポート | `bash scripts/schedule.sh check`（=`tools/wikicheck.py`）。鮮度・ズレ・人間ゲートの気づきを検出（**読み取り専用・自動修正なし**）。pipeline末尾・`status` にも要約が出る |
| `mask` | sources のファイル | マスク後 | `tools/redact.py`（機微情報）/ `tools/mask_paths.py`（実パス）。外部に出す前・共有前 |

**原則**: `sources/` の本文は絶対に書き換えない（唯一の例外は `redact.py` によるマスキング）。整形・要約は必ず `wiki/` 側に別ファイルで作る。

## 取り込みフック（ingest ゲート）＝ llm-wiki の移植

`bash scripts/ingest.sh <文字起こし>` が“関所”。llm-wiki の **file_to_md → redact → mask_paths → ingest** をこのプロジェクトへ移植したもの。

```
（Amical から）scripts/amical-export.py  … amical.db の transcriptions を data/sources/transcripts/ へ（録音は recordings/ へ）
（PDF/Office なら）tools/file_to_md.py    … Markdown 化して data/inbox へ（変換のみ・無加工）
  ↓ data/sources/transcripts/ に置く
[1] tools/redact.py     … 機微情報スキャン（鍵/トークン/PII）。HIGH検出で停止（exit 1 ゲート）
[2] tools/mask_paths.py … 実パス /Users/<user> を検出（--apply で $HOME 表記へ）
[3] scripts/compile-note.py … SOUL/AGENT を読み込んで整形ノート生成（LLMは母艦のみ）
[4] tools/wikilint.py   … 生成ノートの描画チェック（Obsidian/Quartz 両対応）
```

- **迷ったら止める**: HIGH な秘密が出たら整形せず停止。内容確認のうえ `redact.py --apply`（マスキングは sources 改変の唯一の例外）してから再実行。
- **氏名など文脈依存の個人情報**は正規表現で拾えない → AI/目視レビューを併用（SOUL 原則2・迷ったら inbox 保留）。
- ツール一覧: `tools/{redact.py, mask_paths.py, wikilint.py, file_to_md.py}`（llm-wiki（ナレッジベース） からの移植。パス既定のみ voice-ai-collaborator 向けに調整）。

## 整形ノートの書式（`ingest` の出力）

必ず YAML frontmatter を付ける:

```yaml
---
title: "<会議名> <日付>"
type: meeting-note
date: <会議日 YYYY-MM-DD>
created: <作成日 YYYY-MM-DD>
updated: <更新日 YYYY-MM-DD>
source: "[[<元文字起こしファイル名（拡張子なし）>]]"
---
```

本文セクション（この順を基本形とする）:

1. `## 概要` — 会議の目的と結論を3〜5行。
2. `## 決定事項` — 決まったことを箇条書き。
3. `## ネクストアクション` — **ローカルのチェックリスト**（GitHub 起票はしない）。
   `- [ ] [担当者] <やること>（背景・期限）` 形式。
4. `## 論点・未解決` — 持ち越し・要確認事項。矛盾があればフラグ（下記）。
5. `## 関連` — 関連ノートへの `[[wikilink]]`。
6. `## ソース` — `[[<元文字起こしファイル名>]]` を必ず記載。

### 書式ルール

- 日本語で書く。日付は実時刻（`date +%Y-%m-%d`）を使い、手書きしない。
- ノート間リンクは `[[ファイル名]]`（拡張子なし）。
- ネクストアクションは**この PC 内のチェックリスト**として持つ。外部サービスへ起票しない（SOUL 原則2）。
- 新ノートを作ったら `data/wiki/index.md` にも1行追記する。

## 人間ゲート記法（AIの自律を止める印）

AI の下書きで「人間の確認・承認が要る」箇所には、次の印を本文に残す（Zenn「AI駆動PM」のガードレールを移植）:

- `[人間-確認]` … 内容の正しさを人が見てほしい（固有名詞・数値・機密の検証など）
- `[人間-承認]` … 実行/共有してよいかの承認待ち（共有ナレッジベースへの反映、外部送信など）

`check`（`wikicheck.py`）がこれらと「未解消の矛盾フラグ」「inbox 保留」を検出して知らせる。
SOUL 原則3（書くのは人が決める）の運用装置。AI はこの印を**勝手に外さない**。

## 鮮度・ズレの点検（check）

`check` は次を検出する（自動修正しない・提案のみ）: 未整形の文字起こし / 孤児ノート(source欠落) /
更新が古いノート(既定30日) / 人間確認待ち / index 整合。定期トリガー実行のたびに要約がログに残る。

## 矛盾フラグ（前の議事録と食い違ったら）

黙って上書き・平均化しない。両論併記で人の判断を待つ:

```
> [!warning] 矛盾フラグ（YYYY-MM-DD）
> 既存: 「…」（[[前のノート]]）
> 今回: 「…」（[[今回のソース]]）
> 状態: 未解消（人の判断待ち）
```

## セキュリティ（SOUL 原則2の実装）

- 会議データ（`data/` 配下すべて）は **git 非追跡**（`.gitignore` 済み）。外部に出さない。
- LLM 解析はローカルの LM Studio / Ollama のみ。会議本文を外部 API に送らない。
- 外部に出す情報（もし将来 Web 検索等を足すなら）は、氏名・固有名詞・数値を一般化・マスクしてから。
- 判断に迷うファイルは `data/inbox/` に置いて人に確認する。

## LLM 接続（このローカル環境）

- 既定: LM Studio OpenAI互換 `http://localhost:1234/v1/chat/completions`（コンテナからは `host.docker.internal`）。
- 既定モデル: `google/gemma-4-e4b`（高速・JSON安定）。
- **注意**: ロード済みモデルは reasoning 系。`max_tokens` は 3000 以上にする（思考でトークンを使い切り本文が空になるのを防ぐ）。出力が ```json フェンス付きのことがあるので剥いでからパース。
- エンドポイントの選択肢は [docs/glossary.md](./docs/glossary.md) の表を参照。
