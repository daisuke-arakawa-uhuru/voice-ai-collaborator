#!/usr/bin/env python3
"""ingest 前処理: PDF / Office 文書等を Markdown へ変換して llm-wiki/inbox/ に置く。

変換した md は「raw ソース候補」であり、内容の要約・改変は行わない（変換のみ）。
その後の取り込みは llm-wiki スキルの `ingest llm-wiki/inbox/<ファイル>.md` で行う。

- 依存: markitdown（未導入なら `pip install 'markitdown[all]'`）
- 使い方:
    python3 tools/file_to_md.py 資料.pdf
    python3 tools/file_to_md.py slides.pptx spec.docx -o /tmp/out
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".html", ".htm",
             ".csv", ".json", ".xml", ".epub", ".txt", ".rst"}


def unique_path(path: Path) -> Path:
    """出力先の既存ファイルを上書きしない（-2, -3 … を付与）。"""
    if not path.exists():
        return path
    for i in range(2, 100):
        cand = path.with_stem(f"{path.stem}-{i}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"出力先を確保できません: {path}")


def convert(src: Path, out_dir: Path) -> Path | None:
    if not src.is_file():
        print(f"  ✗ {src}: ファイルが見つかりません", file=sys.stderr)
        return None

    out = unique_path(out_dir / (src.stem + ".md"))

    if src.suffix.lower() in {".md", ".markdown"}:
        shutil.copy2(src, out)  # md はそのままコピー（本文無加工）
        return out

    if src.suffix.lower() not in SUPPORTED:
        print(f"  ✗ {src.name}: 未対応の拡張子です（対応: {', '.join(sorted(SUPPORTED))}）",
              file=sys.stderr)
        return None

    try:
        from markitdown import MarkItDown
    except ImportError:
        print("エラー: markitdown が未導入です。次を実行してください:\n"
              "  pip install 'markitdown[all]'", file=sys.stderr)
        sys.exit(1)

    try:
        result = MarkItDown().convert(str(src))
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {src.name}: 変換に失敗しました — {e}", file=sys.stderr)
        return None

    text = result.text_content or ""
    if not text.strip():
        print(f"  ✗ {src.name}: 変換結果が空です（スキャン PDF 等は OCR が必要）", file=sys.stderr)
        return None

    out.write_text(text, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="資料を Markdown 化して data/inbox/ へ置く")
    default_out = Path(__file__).resolve().parent.parent / "data" / "inbox"
    ap.add_argument("files", nargs="+", type=Path, help="変換する資料（PDF/DOCX/PPTX 等）")
    ap.add_argument("-o", "--out", type=Path, default=default_out,
                    help="出力先ディレクトリ（既定: data/inbox）")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    done = []
    for src in args.files:
        result = convert(src, args.out)
        if result:
            print(f"  ✓ {src.name} → {result}")
            done.append(result)

    if done:
        print("\n次のステップ: data/sources/transcripts/ へ移し、ゲート→整形:")
        for p in done:
            try:
                rel = p.relative_to(Path(__file__).resolve().parent.parent)
            except ValueError:
                rel = p
            print(f"  bash scripts/ingest.sh {rel}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
