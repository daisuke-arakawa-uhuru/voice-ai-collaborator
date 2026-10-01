#!/usr/bin/env python3
"""wikicheck.py — data/ の健全性・鮮度・ズレ検知（読み取り専用・提案のみ）.

Zenn記事「AI駆動PMの5原則」の "原則5: 鮮度を保つ" と "ガードレール(人間ゲート)" を
voice-ai-collaborator に取り入れたもの。llm-wiki の lint/curiosity と同じく **自動修正はしない**。
検出して知らせるだけ（人間が判断）。

チェック項目:
  1) 未整形の文字起こし   … sources にあるが wiki ノート未生成（ズレ: source→note なし）
  2) 孤児ノート           … wiki ノートの source 文字起こしが sources に無い（ズレ: note→source なし）
  3) 鮮度                 … wiki ノートの updated が N 日以上前（既定30日。stale 候補）
  4) 人間確認待ち         … [人間-確認]/[人間-承認] や 未解消の矛盾フラグ、inbox 非空
  5) index 整合           … index.md のリンク先ノートが実在しない / ノートが index 未掲載

使い方:
  python3 tools/wikicheck.py                 # レポート（常に exit 0）
  python3 tools/wikicheck.py --stale-days 14 # 鮮度しきい値を変更
  python3 tools/wikicheck.py --strict        # 問題があれば exit 1（ゲート用）
"""
import argparse, datetime, glob, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "data", "sources", "transcripts")
NOTE_DIR = os.path.join(ROOT, "data", "wiki", "meetings")
INBOX = os.path.join(ROOT, "data", "inbox")
INDEX = os.path.join(ROOT, "data", "wiki", "index.md")

_tty = sys.stdout.isatty()
C_OK = "\033[32m" if _tty else ""
C_WARN = "\033[33m" if _tty else ""
C_NG = "\033[31m" if _tty else ""
C_END = "\033[0m" if _tty else ""


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*([A-Za-z_]+)\s*:\s*(.*)\s*$", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
    return fm


def basenames(dir_, pat="*.md"):
    if not os.path.isdir(dir_):
        return []
    return [os.path.basename(p)[:-3] for p in glob.glob(os.path.join(dir_, pat))
            if os.path.basename(p) != ".gitkeep"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    today = datetime.date.today()
    transcripts = set(basenames(SRC_DIR))
    notes = [p for p in glob.glob(os.path.join(NOTE_DIR, "*.md"))]

    # wiki ノートの source（[[...]]）を集める
    note_sources = {}   # note_slug -> source_basename
    note_updated = {}   # note_slug -> updated(str)
    for p in notes:
        slug = os.path.basename(p)[:-3]
        fm = frontmatter(read(p))
        src = re.sub(r"^\[\[|\]\]$", "", (fm.get("source", "") or "")).strip()
        note_sources[slug] = src
        note_updated[slug] = fm.get("updated", "")

    sourced = {s for s in note_sources.values() if s}

    issues = 0

    # 1) 未整形の文字起こし（.包括 は補助なので整形ノート(source一致)の有無で判定）
    uncompiled = sorted(t for t in transcripts if t not in sourced)
    print(f"{'■ 1) 未整形の文字起こし':<28} {len(uncompiled)} 件")
    for t in uncompiled:
        print(f"    {C_WARN}未整形{C_END}: {t}  → bash scripts/ingest.sh data/sources/transcripts/{t}.md")
    issues += len(uncompiled)

    # 2) 孤児ノート（source が sources に無い。.包括 は元 source 名を共有するので除外側で吸収）
    orphans = sorted(slug for slug, src in note_sources.items()
                     if src and src not in transcripts)
    print(f"{'■ 2) 孤児ノート(source欠落)':<28} {len(orphans)} 件")
    for slug in orphans:
        print(f"    {C_WARN}孤児{C_END}: {slug}（source [[{note_sources[slug]}]] が sources に無い）")
    # 孤児は「元文字起こしが消えた(14日削除等)」の正常ケースもあるので issues には数えるが注意書き
    issues += len(orphans)

    # 3) 鮮度（updated が古い）
    stale = []
    for slug, up in note_updated.items():
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", up or "")
        if not m:
            continue
        d = datetime.date(int(m[1]), int(m[2]), int(m[3]))
        age = (today - d).days
        if age >= args.stale_days:
            stale.append((age, slug))
    stale.sort(reverse=True)
    print(f"{'■ 3) 鮮度(≥' + str(args.stale_days) + '日 未更新)':<28} {len(stale)} 件")
    for age, slug in stale[:20]:
        print(f"    {C_WARN}stale{C_END}: {slug}（{age}日前）")
    issues += len(stale)

    # 4) 人間確認待ち（[人間-XX] / 未解消の矛盾フラグ / inbox 非空）
    human = []
    for p in notes + glob.glob(os.path.join(SRC_DIR, "*.md")):
        t = read(p)
        rel = os.path.relpath(p, ROOT)
        if "[人間-確認]" in t or "[人間-承認]" in t:
            human.append(f"{rel}（人間ゲート）")
        if re.search(r"矛盾フラグ", t) and re.search(r"未解消", t):
            human.append(f"{rel}（未解消の矛盾フラグ）")
    inbox_items = [os.path.basename(x) for x in glob.glob(os.path.join(INBOX, "*"))
                   if os.path.basename(x) != ".gitkeep"]
    print(f"{'■ 4) 人間確認待ち':<28} {len(human) + len(inbox_items)} 件")
    for h in human:
        print(f"    {C_NG}要確認{C_END}: {h}")
    for i in inbox_items:
        print(f"    {C_WARN}inbox保留{C_END}: {i}")
    issues += len(human) + len(inbox_items)

    # 5) index 整合
    idx = read(INDEX)
    idx_links = set(re.findall(r"\[\[([^\]|]+)\]\]", idx))
    note_slugs = set(note_sources.keys())
    idx_broken = sorted(l for l in idx_links if l not in note_slugs and l not in transcripts)
    idx_missing = sorted(s for s in note_slugs if s not in idx_links and not s.endswith("包括"))
    print(f"{'■ 5) index 整合':<28} 壊れリンク {len(idx_broken)} / 未掲載 {len(idx_missing)} 件")
    for l in idx_broken[:10]:
        print(f"    {C_NG}壊れリンク{C_END}: [[{l}]]（実体なし）")
    for s in idx_missing[:10]:
        print(f"    {C_WARN}未掲載{C_END}: {s}")
    issues += len(idx_broken) + len(idx_missing)

    print()
    if issues == 0:
        print(f"{C_OK}✅ 健全（鮮度・ズレ・人間ゲートとも問題なし）{C_END}")
    else:
        print(f"{C_WARN}⚠ 気づき {issues} 件（自動修正はしません。人間が判断してください）{C_END}")
    return 1 if (args.strict and issues) else 0


if __name__ == "__main__":
    sys.exit(main())
