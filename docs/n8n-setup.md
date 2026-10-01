# n8n セットアップ手順

n8n（`http://localhost:5678`）でワークフローを動かすまでの手順。
前提: `docker compose up -d` で n8n 起動済み、LM Studio が :1234 でモデルをロード済み。

## 0. n8n Assistant は今はスキップでよい

初回に出る「Build and debug faster with the n8n Assistant」画面は、
**ワークフローをAIに作らせる補助機能（Preview）**。私たちのパイプラインは各ノードを直接組むので不要。

- **「Set up later in Settings」を選んで閉じる**（後から Settings でも設定可）。
- どうしても使うなら Provider=Self-hosted、Base URL=`http://127.0.0.1:1234/v1`、**API key= `lm-studio`（空はNG・ダミーでよい）**、Model=`google/gemma-4-e4b`。
  ※ 小さいローカルモデルだと Assistant の生成物は動かないことが多い（警告どおり）。過度に期待しない。

## 1. ワークフローのインポート

1. 左上の **＋（Create Workflow）** で新規ワークフローを開く。
2. キャンバス右上の **⋮（三点メニュー）→ Import from File…**。
3. `voice-ai-collaborator/n8n/workflows/00-smoke-llm.json` を選択。
4. 同様に `01-file-trigger-detect.json` もインポート（別ワークフローとして）。

> ファイル選択ダイアログでプロジェクトの `n8n/workflows/` を開く。コンテナ内 `/workflows` にも同じ物がマウントされているが、**インポートはホスト側のブラウザから**行う。

## 2. `00` で n8n → LM Studio 疎通を確認

1. インポートした「00 - LLM 疎通スモークテスト」を開く。
2. 下部の **Test workflow（Execute workflow）** を押す。
3. 「LM Studio 呼び出し」ノードが緑になり、出力に `n8n→LM Studio 疎通OK`（に類する応答）が出れば成功。
   - 赤くなる場合は下の「困ったとき」を参照。

## 3. フォルダ監視 →（重要）`01` ではなく `02` を使う

> ⚠️ **重要（既定の除外ノード）**: 最新の n8n は既定で
> `nodes.exclude = ["n8n-nodes-base.executeCommand","n8n-nodes-base.localFileTrigger"]`
> をセキュリティ除外する（`Unrecognized node type` / ノードが「?」表示の原因）。
> このプロジェクトは compose に **`NODES_EXCLUDE=[]`** を設定して有効化済み（ローカル単一ユーザーの検証環境のため）。
> 反映には `docker compose up -d` での再作成が必要。`readWriteFile` は元から除外対象外。
>
> Docker-on-Mac のファイル監視は不安定なので、トリガーは Local File Trigger より
> **`scheduleTrigger`（定期ポーリング）＋ `executeCommand`（`02`）** を推奨。こちらは `docker exec` 検証済み。

`02 - 議事録ポーリング→整形ノート自動生成` の中身:

```
1分ごとに確認(Schedule Trigger)
  → 新規文字起こしを整形(Execute Command)
       node /project/scripts/compile-note.mjs --all
```

`compile-note.mjs` はコンテナ内 node で動く整形ツール（`compile-note.py` の移植）:
- `SOUL.md`/`AGENT.md` を読み込み、`/data/sources/transcripts/*.md` の**まだ整形していない分だけ**を LLM で整形。
- 出力は `/data/wiki/meetings/<日付>-<会議>.md`、`index.md` に追記。**出力が既にあればスキップ（冪等）**。
- LLM は `host.docker.internal:1234`（母艦 LM Studio）。sources は読むだけ・不変。

### 動かし方

1. `02-auto-compile.json` をインポート。
2. テスト用に文字起こしを1つ置く:
   ```bash
   echo "# テスト会議 $(date)" > ~/workspaces/voice-ai-collaborator/data/sources/transcripts/test-$(date +%s).md
   ```
3. 実行して確認する。**Schedule Trigger は「Execute workflow」ボタンだと"トリガーだけ"実行される**（下流の Execute Command まで走らない・数百ms で終わる）ので、次のどちらかで下流まで流す:
   - **（推奨）Activate（有効化）する** — 右上の有効化トグル（`Publish`/`Active`）を ON。1分ごとに**フル実行**され、新規文字起こしが自動で整形される。
   - **Execute Command ノードを直接実行** — 「新規文字起こしを整形」ノードにカーソルを合わせ、ノードの ▶（Execute step）を押す。トリガー出力を入力に、その場でコマンド（LLM 呼び出し・約15秒）が走る。
4. `data/wiki/meetings/` に新しい整形ノートが増えれば成功（既存分は冪等 skip）。
   ```bash
   ls ~/workspaces/voice-ai-collaborator/data/wiki/meetings/
   ```

> 補足: n8n CLI（`n8n execute --id`）は "Execute Workflow Trigger" ノードが必要で、Schedule Trigger のワークフローには使えない（`Missing node to start execution`）。テストは UI の Activate かノード直接実行で行う。

> Execute Command はコンテナ内で走る。`node` はコンテナに同梱、スクリプト/SOUL/AGENT は compose のマウントで `/project` に見えている（設定済み）。

### n8n を介さない最短ルート（おすすめの併用）

ホストで直接叩くのが一番手軽（python 版。LM Studio さえ起動していれば n8n 不要）:
```bash
python3 scripts/compile-note.py data/sources/transcripts/<会議>.md
```

## 4. Task2（追加調査）を足す場合

`docs/workflows.md` の Task2 を参照。会議本文を外部に出さない前提で、
`02` の後段に「疑問抽出 → ローカル LLM 回答」を足す。**外部起票はしない**。

## 困ったとき（Troubleshooting）

| 症状 | 原因 / 対処 |
|---|---|
| Assistant/モデル接続で `OpenAI API key is missing` | LM Studio にキーは無い。**API key 欄にダミー（`lm-studio`）を入れる**。空はSDKが弾く |
| HTTP ノードが `ECONNREFUSED` / つながらない | URL が `localhost` になっていないか。**コンテナからは `host.docker.internal:1234`**（`00` は設定済み） |
| LLM 応答が空 | reasoning モデルが思考でトークンを使い切り。**`max_tokens` を 3000 以上**に。または `google/gemma-4-e4b` を使う |
| `model not found` | LM Studio でそのモデルが**ロードされていない**。アプリでロード、または Body の `model` をロード済み名に合わせる |
| `Unrecognized node type: localFileTrigger`/`executeCommand` / ノードが「?」 | 既定の `nodes.exclude` でブロック。compose の **`NODES_EXCLUDE=[]`** で解除→`docker compose up -d` で再作成。ブラウザを再読込 |
| `02` で何も生成されない | 未整形の文字起こしが無い（既に全部整形済み＝冪等でskip）。新しい `.md` を `data/sources/transcripts/` に置いて再実行 |
| Execute Command が失敗 | `docker exec amical-n8n node /project/scripts/compile-note.mjs --all` を手で叩いてエラーを見る。LM Studio 起動とモデルロードを確認 |
| ポート 5678 に繋がらない | `docker compose ps` で `amical-n8n` が Up か確認。落ちていれば `docker compose up -d` |
