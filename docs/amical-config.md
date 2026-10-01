# Amical 設定メモ

Amical（ローカル文字起こしアプリ）を、このプロジェクトのパイプライン入口として使うためのメモ。

## Amical の保存の実際（2026-09-29 実機調査）

**重要: Amical はフォルダに .md/.txt を書き出さない。すべて内部の SQLite DB に貯める。**

| もの | 実際の保存先 | 備考 |
|---|---|---|
| 文字起こし本文 | `~/Library/Application Support/Amical/amical.db` の `transcriptions.text` | Electronアプリ。ファイルではなくDB |
| 録音音声(.wav) | `$TMPDIR/amical-audio/audio-*.wav`（`/var/folders/.../T/...`） | **一時フォルダ。macOSがいずれ自動削除**。`transcriptions.audio_file` にパス |
| ノート | 同DBの `notes` テーブル | markdown。本文は Yjs 管理のことがあり `content` が空に見える場合あり |
| Whisperモデル | `~/Library/Application Support/Amical/models/ggml-large-v3-turbo.bin` | Voice Model は Large-v3-Turbo |

→ **フォルダ出力機能・保存先設定は無い**ため、Amical 側の設定だけでは `data/sources/transcripts/` に届かない。
DB から取り出す橋渡し（`scripts/amical-export.py`）が必要（下記）。

> ⚠️ **文字起こしは14日で自動削除**（Amical 詳細設定「履歴の保存期間: 14日間」＝過ぎた履歴を自動削除）。
> 録音wavは `$TMPDIR` でさらに短命。**放置するとDBごと消える**ので、`amical-export.py` は**定期実行が実質必須**
> （録音を残すなら録音直後に `--keep-audio` で退避）。詳細設定の「データの保存場所」は編集不可の表示で、出力先設定ではない。

## 橋渡し: amical-export.py（Amical DB → data/sources/）

```bash
python3 scripts/amical-export.py --keep-audio
#  amical.db の transcriptions を data/sources/transcripts/<日付>-amical-<id>.md へ
#  録音wav（残っていれば）を data/sources/recordings/ へ退避（★一時ファイルなので早めに退避）
#  冪等（既存idはスキップ）。読み取り専用（Amical稼働中でも安全）
```

その後は通常どおり `bash scripts/ingest.sh <上記md>`（redact→mask_paths→整形）に載る。

## 文章整形について

Amical 内蔵の「文章整形Model」(OpenAI互換 `http://localhost:1234/v1`) は Amical UI 内での清書用。
このプロジェクトの整形（要約・決定事項・ネクストアクション）は取り出し後に `compile-note.py`/n8n が担うので、
Amical 側の整形は使っても使わなくてもよい（raw をそのまま `transcriptions.text` から取る）。

## Meet の音声を拾う（B）

**現状（2026-09-29 実機）**: 仮想オーディオデバイスは未導入（`MacBook Pro のマイク/スピーカー`のみ）。
このままだと Amical はマイク＝**自分の声しか録れない**（相手の声はスピーカーから出るだけで録音対象外）。

### なぜ必要か
Google Meet の相手の声・画面共有音は「システム音声」として再生される。マイクはそれを拾わない。
→ **システム音声を"録れる入力"に横流しする仮想デバイス**が要る。定番は **BlackHole**（無料・OSS）。

### セットアップ手順（要 admin・GUI。所要10分）

1. **BlackHole 2ch を導入**（✅ 2026-09-29 導入済み `brew install --cask blackhole-2ch`。`/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver` 確認。**要再起動で有効化**）
   ```bash
   brew install --cask blackhole-2ch   # 導入時に sudo パスワード → 再起動が必要
   ```

2. **集約デバイスを作る（入力用）** — `Audio MIDI 設定.app`（アプリ → ユーティリティ）
   - 左下 `＋` → **「機器セット（Aggregate Device）」を作成**
   - `MacBook Pro のマイク` と `BlackHole 2ch` の両方にチェック → 名前例「Amical入力(Mic+System)」
   - これを **Amical の入力デバイス**に選ぶ（自分の声＋システム音声が混ざって録れる）

3. **相手の声を BlackHole に流しつつ自分でも聞く（出力用）**
   - 同じく `＋` → **「複数出力装置（Multi-Output Device）」を作成**
   - `BlackHole 2ch` と `MacBook Pro のスピーカー`（or ヘッドホン）にチェック → 名前例「聞く+録る」
   - **システム設定 → サウンド → 出力**でこの Multi-Output を選ぶ（or Meet を開いている Chrome の音だけをここに向ける）
   - これで「相手の声を自分は聞きながら、BlackHole 経由で Amical にも入る」

4. Meet で試し録音し、`python3 scripts/amical-export.py` で相手の声も文字化されているか確認。

### 代替手法（将来の選択肢）: Yap（Apple SpeechAnalyzer）
録音/録画の**ファイル**があるなら、Amical + BlackHole の代わりに **Yap**（macOS Tahoe の `SpeechAnalyzer`/`SpeechTranscriber` を使う CLI）で
mp4/mp3 → TXT/SRT を直接生成する手もある（Whisper比 約2.2倍速・ファイル入出力で DB 抽出不要・**動画=全参加者**を扱える）。
リアルタイムでない点がトレードオフ。

### ⚠️ 録音の同意（重要・録音ポリシー）
相手の音声・画面共有には**他者の個人情報・顧客機密**が含まれうる。録音・文字起こしする前に
**参加者の了承を得る**こと。取り込んでも全処理はローカル（`data/` は非共有）だが、`ingest.sh` の
redact ゲートは鍵/トークンしか止められない（氏名・顧客名などは AI/目視レビューが必要）。
機密区分が不明・同意が取れない会議は録らない（安全側）。

## 全体フロー（確定版）

```
Amical（録音→amical.db）
  → scripts/amical-export.py        … DB → data/sources/transcripts/*.md（＋録音を recordings/ へ）
  → scripts/ingest.sh               … redact→mask_paths→compile→wikilint
  → data/wiki/meetings/*.md         … 整形ノート
```

自動化は **ホスト側の launchd**（`scripts/schedule.sh install watch|hourly`）で `pipeline.sh` を回す。
トリガー方式の選択肢は [scheduling.md](./scheduling.md)。n8n の `02` はコンテナ内で amical.db（`~/Library`）に
届かないため、DB からの取り込みはホスト側で走らせる（n8n は任意）。

## TODO（残・実機確認）

- [x] 取り込みの自動化: `pipeline.sh` + `schedule.sh`（watch を有効化済み）。詳細 [scheduling.md](./scheduling.md)
- [ ] **Meet のシステム音声取り込み**: 上記「Meet の音声を拾う（B）」の手順で BlackHole を導入（要 admin）。未導入なら自分の声のみ録れる。
- [ ] 録音を残すか（`--keep-audio`）。$TMPDIR/DB とも自動削除されるので、残すなら watch/hourly を有効にして早めに退避。
