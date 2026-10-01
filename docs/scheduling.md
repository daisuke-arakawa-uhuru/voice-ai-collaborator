# トリガー方式（いつ取り込むか）

会議の頻度に合わせて、取り込み（Amical DB → 整形ノート）の起動タイミングを柔軟に選べる。
実体はすべて **`scripts/pipeline.sh`**（冪等・全ローカル）を呼ぶだけ。切り替えは `scripts/schedule.sh`。

> ⚠️ Amical の文字起こしは **14日で自動削除**（録音wavは `$TMPDIR` でさらに短命）。
> 会議のたびに取りこぼさないよう、下のどれかは有効にしておくのが安全。

## 選べるモード

| モード | いつ動く | 向いている人 | コマンド |
|---|---|---|---|
| **watch（イベント駆動）** | Amical に新しい文字起こしができた瞬間 | 会議が不定期。**推奨** | `bash scripts/schedule.sh install watch` |
| **hourly（定期）** | 1時間ごと（間隔調整可） | ゆるく回したい | `bash scripts/schedule.sh install hourly` |
| **manual（任意タイミング）** | 自分で叩いたときだけ | 手動派 | `bash scripts/schedule.sh run` |
| **off** | 自動は止める | 一時停止 | `bash scripts/schedule.sh off` |
| 状態確認 | — | — | `bash scripts/schedule.sh status` |
| 健全性チェック | — | — | `bash scripts/schedule.sh check`（鮮度・ズレ・人間ゲート。読み取り専用） |

- watch と hourly は排他（install すると他方は自動で外れる）。
- watch/hourly を有効にしていても、`run` でいつでも即時実行できる。
- 仕組み: macOS の launchd。plist は `~/Library/LaunchAgents/ai.amical.pipeline.*`（端末ローカル・非共有）。
  リポジトリには実パスを含まないテンプレ（`launchd/*.template`）だけを置き、install 時に実パスへ展開する。

## 動いているか確認する（どこを見るか）

```bash
bash scripts/schedule.sh status                 # トリガー登録・直近ログ・健全性の要約
launchctl list | grep amical.pipeline           # 常駐エージェントと「最後の終了コード」
tail -f data/.logs/pipeline.log                 # 実行のたびに start / compiled / researched / done が出る
tail data/.logs/launchd.err.log                 # launchd 経由実行のエラー
bash scripts/check-llm.sh                        # LM Studio が :1234 で起動＆モデルロード済みか
```

- **pipeline.log に今日の行が無い** → トリガーが発火していない or LM Studio 未起動。`schedule.sh run` で手動実行して切り分ける。
- **watch の取りこぼし対策**: Amical は DB を開いたまま SQLite(WAL) に書くため、`WatchPaths` が発火しないことがある。
  そこで watch の plist には **`StartInterval`（30分ごと）の安全網**を併設してある（イベント＋定期のどちらかで必ず実行）。
  それでも急ぎなら会議直後に `bash scripts/schedule.sh run`。
- **LM Studio が動いた形跡がない** ときは、まず pipeline がそもそも走ったか（pipeline.log）を見る。pipeline が走って初めて LM Studio を叩く。

## カレンダー予定から起動したい場合

`scripts/schedule.sh run`（＝ pipeline）を外部から呼べばよいので、次のような連携ができる:

- **macOS ショートカット / カレンダーの「Automation」**: 予定開始/終了時に「シェルスクリプトを実行」で `bash <PROJECT>/scripts/schedule.sh run`。
- **gcalcli + cron**: Google カレンダーの MTG 予定を gcalcli で取得し、終了後に `run` を叩く小さなラッパを cron 登録。
- どの経路でも pipeline は冪等なので、二重に叩かれても安全。

（カレンダー連携そのものの実装は未同梱。上記フックで差し込める設計にしてある。）

## 調査付き包括議事録（任意）

会議の論点を並列調査し、**llm-wiki を引用**（＋任意で Web）して AI が回答・判断を付けた「包括議事録」も作れる。
実体は `scripts/research-note.py`（`data/wiki/meetings/<会議>.包括.md` を生成、冪等）。

| やり方 | コマンド |
|---|---|
| 単発（ローカルのみ） | `bash scripts/research.sh data/sources/transcripts/<会議>.md` |
| 単発（Web調査も） | `bash scripts/research.sh <会議> --web` |
| pipeline に含める（ローカル） | `RESEARCH=1 bash scripts/pipeline.sh` |
| 自動化に含める（ローカル） | `bash scripts/schedule.sh install watch research` |
| 自動化に含める（Web も） | `bash scripts/schedule.sh install watch research-web` |

> 🛑 **Web 調査（`--web` / `research-web`）は既定 OFF・明示オプトイン**。外部に出すのは
> **固有名詞を除いた一般化クエリのみ**（会議本文は送らない）。それでも外部送信が発生するので、
> SOUL 原則2（全ローカル）を意図的に一部緩める判断になる。顧客機密・個人情報を含む会議では使わない（安全側）。
> ローカルのみ（llm-wiki＋LM Studio）なら外部送信ゼロ。
>
> 注: 回答本文は AI 合成。`llm-wiki 引用` 行は vault の実 grep ヒットだが、回答文中の記述・ページ名は
> 検証が必要（ローカルLLMが実在しないページ名を書くことがある）。最終判断は人間が行う。

## pipeline が中でやること

```
scripts/pipeline.sh
  1) scripts/amical-export.py --keep-audio   … amical.db → data/sources/transcripts/（録音は recordings/ へ）
  2) 各文字起こし: tools/redact.py（HIGH秘密はスキップ）→ scripts/compile-note.py（既存はスキップ＝冪等）
  3) tools/wikilint.py                        … 生成ノートの描画チェック
  ログ: data/.logs/pipeline.log / 多重起動はロックで防止
```

すべてローカル完結（外部送信なし）。機微情報（鍵・トークン）が混じる文字起こしは整形せずスキップし、
手動レビューに回す（`data/sources/transcripts/` に残る）。
