#!/usr/bin/env python3
"""amical-export.py — Amical の SQLite DB から文字起こしを取り出し、data/sources/transcripts/ へ .md 保存する橋渡し。

Amical はフォルダにファイルを吐かず、文字起こしを `~/Library/Application Support/Amical/amical.db`
（transcriptions テーブル）に貯める。本ツールがそれを raw の .md として書き出し、
以後の ingest（redact/mask_paths→compile）に載せられるようにする。

- 読み取り専用（DB は temp にコピーして開く。Amical 稼働中でも安全）
- 冪等: 既に書き出し済み（同じ amical_id の .md がある）ならスキップ
- 録音音声(.wav)は既定でコピーしない（$TMPDIR にあり一時的）。--keep-audio で data/sources/recordings/ へ退避

使い方:
  python3 scripts/amical-export.py              # 新規文字起こしを .md 書き出し
  python3 scripts/amical-export.py --keep-audio # 音声も（残っていれば）退避
  python3 scripts/amical-export.py --db <path>  # DB パスを明示
"""
import argparse, datetime, os, shutil, sqlite3, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.expanduser("~/Library/Application Support/Amical/amical.db")
OUT_DIR = os.path.join(ROOT, "data", "sources", "transcripts")
REC_DIR = os.path.join(ROOT, "data", "sources", "recordings")


def open_ro_copy(db_path):
    """WAL を含む現在状態を安全に読むため db(+wal+shm) を temp にコピーして開く。"""
    if not os.path.isfile(db_path):
        sys.exit(f"amical.db が見つかりません: {db_path}")
    tmp = tempfile.mkdtemp(prefix="amical_ro_")
    base = os.path.join(tmp, "amical.db")
    shutil.copy2(db_path, base)
    for suf in ("-wal", "-shm"):
        if os.path.exists(db_path + suf):
            shutil.copy2(db_path + suf, base + suf)
    con = sqlite3.connect(base)
    con.row_factory = sqlite3.Row
    return con, tmp


def iso(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds")
    except Exception:
        return str(ts)


def date_of(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--keep-audio", action="store_true", help="録音wavを data/sources/recordings/ へ退避")
    args = ap.parse_args()

    con, tmp = open_ro_copy(args.db)
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = con.execute(
        "SELECT id, text, created_at, timestamp, speech_model, formatting_model, "
        "audio_file, audio_duration_ms, detected_language "
        "FROM transcriptions WHERE text IS NOT NULL AND trim(text) <> '' "
        "ORDER BY created_at ASC"
    ).fetchall()

    created = skipped = audio_saved = 0
    for r in rows:
        tid = str(r["id"])
        ts = r["created_at"] or r["timestamp"]
        d = date_of(ts)
        out = os.path.join(OUT_DIR, f"{d}-amical-{tid}.md")
        if os.path.exists(out):
            skipped += 1
            continue

        audio = r["audio_file"] or ""
        audio_note = audio
        if args.keep_audio and audio and os.path.exists(os.path.expanduser(audio)):
            os.makedirs(REC_DIR, exist_ok=True)
            dst = os.path.join(REC_DIR, f"{d}-amical-{tid}" + os.path.splitext(audio)[1])
            try:
                shutil.copy2(os.path.expanduser(audio), dst)
                audio_note = os.path.relpath(dst, ROOT)
                audio_saved += 1
            except OSError:
                audio_note = f"{audio}（コピー失敗）"
        elif audio:
            audio_note = f"{audio}（$TMPDIR・一時ファイル。現存しない可能性）"

        fm = (
            "---\n"
            f"source: amical\n"
            f"amical_id: {tid}\n"
            f"recorded_at: {iso(ts)}\n"
            f"speech_model: {r['speech_model'] or ''}\n"
            f"detected_language: {r['detected_language'] or ''}\n"
            f"audio_duration_ms: {r['audio_duration_ms'] or ''}\n"
            f"audio_file: \"{audio_note}\"\n"
            "---\n\n"
            f"# Amical 文字起こし {d}（id={tid}）\n\n"
            f"{r['text'].strip()}\n"
        )
        with open(out, "w", encoding="utf-8") as f:
            f.write(fm)
        created += 1
        print(f"✔ {os.path.relpath(out, ROOT)}")

    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\ndone: created={created} skipped={skipped} audio_saved={audio_saved} total={len(rows)}")
    if created:
        print("次: bash scripts/ingest.sh <上記ファイル>  で redact→mask_paths→整形")


if __name__ == "__main__":
    main()
