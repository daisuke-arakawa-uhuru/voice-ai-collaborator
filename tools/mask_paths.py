#!/usr/bin/env python3
"""
mask_paths.py — 作業端末のユーザー名を含む絶対パスを環境変数参照へマスキングする。

共有 wiki に `/Users/<username>/...` のような実パスが残ると、PC のユーザー名が漏洩する。
本ツールは実パスを環境変数（$WIKI_ROOT / $KB_VAULT_ROOT / $HOME）へ置換し、
どの端末・セッションでも env 経由でパスを辿れる状態にする。

置換規則（長い接頭辞から順に適用）:
  1. $WIKI_ROOT           = <vault>/llm-wiki           -> "$WIKI_ROOT"
  2. $KB_VAULT_ROOT = <vault>                    -> "$KB_VAULT_ROOT"
  3. $REPO_ROOT (= $HOME/repository)                    -> "$REPO_ROOT"
  4. $HOME                                              -> "$HOME"

現在の端末の値は環境変数から取得（未設定なら $HOME から推定）。他端末でも同様に動く。

使い方:
  python3 tools/mask_paths.py [パス ...]        # ドライラン（既定: llm-wiki 配下 + vault直下md）
  python3 tools/mask_paths.py --apply [パス ...] # 置換を適用
  python3 tools/mask_paths.py --show-user        # 検出したユーザー名を確認

注意:
  - env 定義行（例: `KB_VAULT_ROOT = /Users/.../ai-wiki`）を含むファイル（CLAUDE.md 等）は
    循環置換になるため既定の対象から外し、手動で「.zshrc 参照」に書き換えること。
  - 置換は意味を保つ匿名化（sources 不変原則のマスキング例外に該当）。
"""
import os
import re
import sys

HOME = os.environ.get("HOME") or os.path.expanduser("~")
USER = os.path.basename(HOME)
# このツールが属する voice-ai-collaborator のルート（表示・既定ターゲットの基準）
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.environ.get("KB_VAULT_ROOT", os.path.join(HOME, "workspaces", "ai-wiki"))
WIKI = os.environ.get("WIKI_ROOT", os.path.join(VAULT, "llm-wiki"))
REPO = os.environ.get("REPO_ROOT", os.path.join(HOME, "repository"))

# (実パス, 置換後) を長い順に。WIKI は VAULT の子なので先に置換する。
# リポジトリは env `$REPO_ROOT`（.zshrc で `export REPO_ROOT="$HOME/repository"`）へ寄せる。
SUBS = [
    (WIKI, "$WIKI_ROOT"),
    (VAULT, "$KB_VAULT_ROOT"),
    (REPO, "$REPO_ROOT"),
    (HOME, "$HOME"),
]

# 既定の対象 = voice-ai-collaborator の data 配下（sources / wiki / inbox）
DEFAULT_TARGETS = [
    os.path.join(PROJECT, "data", "sources"),
    os.path.join(PROJECT, "data", "wiki"),
    os.path.join(PROJECT, "data", "inbox"),
]


def mask_text(text):
    n = 0
    for real, repl in SUBS:
        if real and real in text:
            n += text.count(real)
            text = text.replace(real, repl)
    return text, n


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f.endswith(".md"):
                        out.append(os.path.join(root, f))
        elif os.path.isfile(p):
            out.append(p)
    return sorted(set(out))


def main():
    args = [a for a in sys.argv[1:] if a != "--apply" and a != "--show-user"]
    apply = "--apply" in sys.argv
    if "--show-user" in sys.argv:
        print(f"HOME={HOME}\nUSER={USER}\nVAULT={VAULT}\nWIKI={WIKI}\nREPO={REPO}")
        return
    targets = collect(args if args else DEFAULT_TARGETS)

    total_files, total_hits = 0, 0
    changed = []
    for f in targets:
        try:
            with open(f, "rb") as fh:
                orig = fh.read().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        masked, n = mask_text(orig)
        if n > 0:
            total_files += 1
            total_hits += n
            changed.append((f, n))
            if apply and masked != orig:
                with open(f, "wb") as fh:
                    fh.write(masked.encode("utf-8"))
    mode = "APPLIED" if apply else "DRY-RUN"
    print(f"[{mode}] 対象 {len(targets)} ファイル / 置換のあった {total_files} ファイル / 置換 {total_hits} 箇所")
    for f, n in changed:
        rel = os.path.relpath(f, PROJECT)
        print(f"  {n:3d}  {rel}")
    # 残存チェック（bare username）
    if apply:
        leftover = []
        for f in targets:
            try:
                t = open(f, "rb").read().decode("utf-8")
            except Exception:
                continue
            if re.search(r"/Users/" + re.escape(USER) + r"\b", t) or (f"/{USER}/" in t):
                leftover.append(os.path.relpath(f, PROJECT))
        if leftover:
            print("  ⚠️ 置換後もユーザー名パスが残存:", ", ".join(leftover))
        else:
            print("  ✅ ユーザー名パスの残存なし")


if __name__ == "__main__":
    main()
