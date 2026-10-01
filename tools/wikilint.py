#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wikilint — llm-wiki の Markdown 互換性チェッカ / 自動修正ツール

Obsidian は寛容だが Quartz 5 (remark/GFM) で描画が壊れるパターンを検出し、
安全なものは自動修正する。公開(ビルド)前チェックとして常用する想定。

使い方:
  python3 tools/wikilint.py               # 全体を検査（問題があれば exit 1）
  python3 tools/wikilint.py --fix         # 安全な項目を自動修正してから再検査
  python3 tools/wikilint.py --path wiki   # 対象を絞る（既定は wiki と sources）
  python3 tools/wikilint.py --quiet       # サマリのみ

検出ルール（いずれも Quartz/GFM で確実に描画が壊れる・★=--fixで自動修正可）:
  ★ table-wikilink-pipe   : 表セル内 [[対象|別名]] の未エスケープ | → [[対象\\|別名]]
                            （崩れで生じた末尾の空列もヘッダ/区切りから復元）
  ★ table-no-blank-before : 段落直後に貼り付いた表の前へ空行を挿入

自動修正は「パイプのエスケープ」「空行挿入」「崩れ列の復元」のみで、
本文・情報は変更しない。sources/ も対象（不変原則の整形例外）。

対象外（誤検知が多く非破壊のため見ない）: 行頭 `#タグ`（CommonMarkでは通常テキスト）、
コード内に例示された ``` によるフェンス数の見かけ上の不一致。
"""
import re, os, sys, glob, argparse

# lint 対象ルート = voice-ai-collaborator の data/（wiki と sources を含む）。
# ※ llm-wiki（ナレッジベース） の WIKI_ROOT / KB_VAULT_ROOT env は本物の vault を指すため使わない。
#   このプロジェクト専用に AMICAL_DATA_ROOT を見て、無ければ tools の2つ上 = プロジェクト直下 /data。
VAULT = os.environ.get("AMICAL_DATA_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
)

# ---- table helpers ---------------------------------------------------------
def is_delim(l):
    s = l.strip()
    if not s or set(s) - set('|:- \t'):
        return False
    return '-' in s and '|' in s

def split_cells(row):
    s = row.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return re.split(r'(?<!\\)\|', s)

def find_tables(lines):
    """yield (header_idx, delim_idx, body_end_exclusive)"""
    i, n, inc = 0, len(lines), False
    while i < n:
        if re.match(r'^\s*(```|~~~)', lines[i]):
            inc = not inc; i += 1; continue
        if not inc and is_delim(lines[i]) and i > 0 and '|' in lines[i - 1]:
            hdr = i - 1
            j = i + 1
            while j < n and lines[j].strip() and '|' in lines[j] and not lines[j].strip().startswith('```'):
                j += 1
            yield (hdr, i, j)
            i = j; continue
        i += 1

def normalize_wikilinks(line):
    """[[target|alias]] -> [[target\\|alias]] (strip padding around pipe)."""
    def repl(m):
        inner = m.group(0)[2:-2].replace('\\|', '|')
        if '|' in inner:
            parts = [p.strip() for p in inner.split('|')]
            return '[[' + '\\|'.join(parts) + ']]'
        return m.group(0)
    return re.sub(r'\[\[.*?\]\]', repl, line)

# ---- checks ----------------------------------------------------------------
def check_file(path):
    """return list of (rule, line_no, msg)"""
    out = []
    lines = open(path, encoding='utf-8').read().split('\n')
    for hdr, dl, end in find_tables(lines):
        prev = lines[hdr - 1] if hdr > 0 else ""
        if hdr > 0 and prev.strip() and not is_delim(prev) and '|' not in prev \
           and not prev.lstrip().startswith(('#', '>')):
            out.append(("table-no-blank-before", hdr + 1, f"前行: {prev.strip()[:40]}"))
        for r in [hdr] + list(range(dl + 1, end)):
            for m in re.finditer(r'\[\[.*?\]\]', lines[r]):
                if re.search(r'(?<!\\)\|', m.group(0)):
                    out.append(("table-wikilink-pipe", r + 1, m.group(0)[:50]))
    return out

# ---- fixes -----------------------------------------------------------------
def fix_file(path):
    """apply safe fixes in place; return number of changes."""
    text = open(path, encoding='utf-8').read()
    lines = text.split('\n')
    # 1) escape pipes + restore mangled trailing-empty header columns
    for hdr, dl, end in list(find_tables(lines)):
        for r in [hdr] + list(range(dl + 1, end)):
            lines[r] = normalize_wikilinks(lines[r])
        body = list(range(dl + 1, end))
        if body:
            tc = max(len(split_cells(lines[r])) for r in body)
            hc = len(split_cells(lines[hdr]))
            if hc > tc:
                hcells = split_cells(lines[hdr])
                if all(c.strip() == '' for c in hcells[tc:]):
                    lines[hdr] = '| ' + ' | '.join(c.strip() for c in hcells[:tc]) + ' |'
                    lines[dl] = '|' + '|'.join(['---'] * tc) + '|'
    # 2) insert blank line before glued tables (recompute; insert bottom-up)
    def needs_blank(ls, h):
        if h <= 0:
            return False
        p = ls[h - 1]
        return bool(p.strip()) and not is_delim(p) and '|' not in p \
            and not p.lstrip().startswith(('#', '>'))
    hdrs = [h for (h, d, e) in find_tables(lines) if needs_blank(lines, h)]
    for h in sorted(hdrs, reverse=True):
        lines.insert(h, '')
    new = '\n'.join(lines)
    if new != text:
        open(path, 'w', encoding='utf-8').write(new)
        return 1
    return 0

# ---- main ------------------------------------------------------------------
def collect(paths):
    files = []
    for base in paths:
        root = os.path.join(VAULT, base)
        if base == ".":
            files += glob.glob(os.path.join(VAULT, "*.md"))  # vault 直下のみ
        elif os.path.isdir(root):
            files += glob.glob(os.path.join(root, "**", "*.md"), recursive=True)
        elif root.endswith('.md') and os.path.exists(root):
            files.append(root)
    return sorted(set(files))

def main():
    ap = argparse.ArgumentParser(description="llm-wiki Markdown 互換性チェッカ")
    ap.add_argument("--fix", action="store_true", help="安全な項目を自動修正")
    ap.add_argument("--path", action="append", help="対象パス（複数可、既定: . wiki sources）")
    ap.add_argument("--quiet", action="store_true", help="サマリのみ")
    args = ap.parse_args()
    paths = args.path or [".", "wiki", "sources"]
    files = collect(paths)

    if args.fix:
        changed = sum(fix_file(p) for p in files)
        print(f"[--fix] {changed} ファイルを修正しました\n")

    from collections import Counter, defaultdict
    cnt = Counter(); byrule = defaultdict(list)
    for p in files:
        rel = os.path.relpath(p, VAULT)
        for rule, ln, msg in check_file(p):
            cnt[rule] += 1
            byrule[rule].append((rel, ln, msg))

    breakers = ("table-wikilink-pipe", "table-no-blank-before")
    print(f"検査対象: {len(files)} files ({', '.join(paths)})")
    for rule in breakers:
        items = byrule.get(rule, [])
        mark = "❌" if items else "✅"
        print(f"{mark} {rule}: {len(items)} 件")
        if not args.quiet:
            for rel, ln, msg in items[:30]:
                print(f"    {rel}:{ln}  {msg}")
            if len(items) > 30:
                print(f"    ...(+{len(items) - 30} more)")

    n_breakers = sum(cnt[r] for r in breakers)
    print()
    if n_breakers:
        print(f"描画破壊 {n_breakers} 件。`python3 tools/wikilint.py --fix` で自動修正できます。")
        sys.exit(1)
    print("描画破壊なし（Obsidian / Quartz 両対応）。")
    sys.exit(0)

if __name__ == "__main__":
    main()
