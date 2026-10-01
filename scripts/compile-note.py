#!/usr/bin/env python3
"""compile-note.py — 文字起こし(sources) を整形ノート(wiki) にコンパイルするローカルツール.

llm-wiki の `ingest` 動詞のローカル移植。
- SOUL.md / AGENT.md を毎回システムプロンプトに読み込む（＝ローカルAIが常に原則・書式を持った状態）
- LLM は母艦の LM Studio / Ollama のみ（会議データを外部に出さない）
- sources 本文は読むだけ・改変しない。出力は data/wiki/meetings/ に新規作成

使い方:
  python3 scripts/compile-note.py data/sources/transcripts/sample-meeting.md
  python3 scripts/compile-note.py <transcript> --title "定例MTG" --date 2026-09-29
環境変数: LLM_BASE_URL(既定 http://localhost:1234/v1) / LLM_MODEL(既定 google/gemma-4-e4b)
"""
import argparse, datetime, json, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/").replace("host.docker.internal", "localhost")
MODEL = os.environ.get("COMPILE_MODEL") or os.environ.get("LLM_MODEL", "google/gemma-4-e4b")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def slugify(name):
    s = re.sub(r"\.[^.]+$", "", os.path.basename(name))
    s = re.sub(r"[^\w぀-ヿ一-鿿-]+", "-", s).strip("-")
    return s or "note"


def strip_output(text):
    """reasoning の混入やコードフェンスを除去して本文だけにする。"""
    text = text.strip()
    # ```markdown ... ``` / ```md ... ``` のフェンス除去
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def call_llm(system, user, max_tokens=4000):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False, "temperature": 0.2, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    return data["choices"][0]["message"].get("content", "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="data/sources/transcripts/ 配下の文字起こしファイル")
    ap.add_argument("--title", default=None)
    ap.add_argument("--date", default=None, help="会議日 YYYY-MM-DD（既定: 今日）")
    ap.add_argument("--dry-run", action="store_true", help="ファイルに書かず標準出力に表示")
    ap.add_argument("--force", action="store_true", help="整形ノートが既にあっても作り直す")
    args = ap.parse_args()

    tpath = args.transcript
    if not os.path.isfile(tpath):
        sys.exit(f"文字起こしが見つかりません: {tpath}")

    today = datetime.date.today().isoformat()
    mdate = args.date or today
    src_name = re.sub(r"\.[^.]+$", "", os.path.basename(tpath))
    title = args.title or f"{src_name} {mdate}"
    base_slug = slugify(tpath)
    # base が既に日付始まりなら二重日付を避ける
    slug = base_slug if re.match(r"\d{4}-\d{2}-\d{2}", base_slug) else f"{mdate}-{base_slug}"

    # 冪等: 整形ノートが既にあれば LLM を呼ばずスキップ（--force で作り直し）
    out_dir = os.path.join(ROOT, "data", "wiki", "meetings")
    out_path = os.path.join(out_dir, f"{slug}.md")
    if os.path.exists(out_path) and not args.dry_run and not args.force:
        print(f"skip（既存）: {os.path.relpath(out_path, ROOT)}")
        return

    soul = read(os.path.join(ROOT, "SOUL.md"))
    agent = read(os.path.join(ROOT, "AGENT.md"))
    transcript = read(tpath)

    system = (
        soul + "\n\n" + agent +
        "\n\n---\n今から文字起こし1件を『整形ノート』にコンパイルする。"
        "AGENT.md の『整形ノートの書式』に厳密に従い、frontmatter とセクションを持つ"
        "Markdown ノート**だけ**を出力する（前後の説明・コードフェンス・思考は出力しない）。日本語で書く。"
    )
    user = (
        f"会議日: {mdate}\n作成日: {today}\nタイトル: {title}\n"
        f"ソースファイル名（wikilink 用・拡張子なし）: {src_name}\n\n"
        f"=== 文字起こし本文 ===\n{transcript}"
    )

    print(f"[compile] model={MODEL} base={BASE}", file=sys.stderr)
    note = strip_output(call_llm(system, user))

    # frontmatter が無ければ最低限を補う（安全側）
    if not note.startswith("---"):
        fm = (f"---\ntitle: \"{title}\"\ntype: meeting-note\ndate: {mdate}\n"
              f"created: {today}\nupdated: {today}\nsource: \"[[{src_name}]]\"\n---\n\n")
        note = fm + note

    if args.dry_run:
        print(note)
        return

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(note if note.endswith("\n") else note + "\n")
    print(f"✔ 整形ノート作成: {os.path.relpath(out_path, ROOT)}")

    # index.md に1行追記（無ければ作成）
    index_path = os.path.join(ROOT, "data", "wiki", "index.md")
    if not os.path.isfile(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("---\ntitle: \"会議まとめ Index\"\ntype: index\n"
                    f"created: {today}\nupdated: {today}\n---\n\n# 会議まとめ カタログ\n\n"
                    "| 日付 | ノート | ソース |\n|---|---|---|\n")
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(f"| {mdate} | [[{slug}]] | [[{src_name}]] |\n")
    print(f"✔ index 追記: {os.path.relpath(index_path, ROOT)}")
    print("※ sources 本文は未変更（raw 不変の原則）。")


if __name__ == "__main__":
    main()
