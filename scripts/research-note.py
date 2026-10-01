#!/usr/bin/env python3
"""research-note.py — 文字起こしから「調査付き・包括的議事録」を生成する.

やること（並列）:
  1. LM Studio で 概要 / 決定事項 / ネクストアクション / 調査すべき論点(質問) を抽出
  2. 各論点を並列に調査:
       - llm-wiki（ローカル vault）を検索して該当ページと根拠を集める（引用元）
       - （--web 時のみ）一般化クエリで Web を検索して要約（★生議事録は送らない）
       - LM Studio が集めた根拠から「回答」と「AIの判断」を合成
  3. 包括ノートを組み立て: 概要／決定事項／ネクストアクション／論点ごとの調査結果(引用付き)／AI判断／記録メタ

方針: 既定は完全ローカル（llm-wiki + LM Studio）。--web は明示オプトインで、外部へ出すのは
      固有名詞を除いた一般化クエリのみ（会議本文は送信しない）。全出力は data/wiki/（非共有）。

使い方:
  python3 scripts/research-note.py data/sources/transcripts/<会議>.md
  python3 scripts/research-note.py <会議> --web        # Web調査も有効化（要注意・下記）
  python3 scripts/research-note.py <会議> --workers 4  # 並列数
"""
import argparse, concurrent.futures as cf, datetime, json, os, re, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/").replace("host.docker.internal", "localhost")
MODEL = os.environ.get("RESEARCH_MODEL") or os.environ.get("LLM_MODEL", "google/gemma-4-e4b")
# llm-wiki（ローカル vault）。env が本物 vault を指す。無ければ検索を無効化。
WIKI_ROOT = os.environ.get("WIKI_ROOT") or (
    os.path.join(os.environ["KB_VAULT_ROOT"], "llm-wiki") if os.environ.get("KB_VAULT_ROOT") else "")


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def today():
    return datetime.date.today().isoformat()


def strip_fences(t):
    t = (t or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    return t


def llm(system, user, max_tokens=3500):
    body = json.dumps({
        "model": MODEL, "stream": False, "temperature": 0.2, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"].get("content", "").strip()


def llm_json(system, user, max_tokens=3500):
    raw = strip_fences(llm(system, user, max_tokens))
    m = re.search(r"(\{.*\}|\[.*\])", raw, re.S)
    try:
        return json.loads(m.group(1) if m else raw)
    except Exception:
        return None


# ---- llm-wiki ローカル検索（読み取りのみ） --------------------------------
def wiki_search(keywords, limit=3):
    """vault の wiki/ を keyword で検索し、上位ページ（名前＋根拠行）を返す。"""
    if not WIKI_ROOT or not os.path.isdir(os.path.join(WIKI_ROOT, "wiki")):
        return []
    kws = [k for k in keywords if k and len(k) >= 2]
    if not kws:
        return []
    hits = {}
    for base, _dirs, files in os.walk(os.path.join(WIKI_ROOT, "wiki")):
        for fn in files:
            if not fn.endswith(".md") or fn in ("index.md", "_overview.md", "changelog.md", "log.md"):
                continue
            path = os.path.join(base, fn)
            text = read(path)
            score = 0
            lines = []
            for kw in kws:
                c = text.count(kw)
                if c:
                    score += c
                    for ln in text.splitlines():
                        if kw in ln and ln.strip() and len(lines) < 2:
                            lines.append(ln.strip()[:120])
            if score:
                hits[fn[:-3]] = {"page": fn[:-3], "score": score, "evidence": lines[:2]}
    return sorted(hits.values(), key=lambda h: -h["score"])[:limit]


# ---- Web 検索（--web 時のみ・一般化クエリのみ） ----------------------------
def web_search(query, limit=3):
    """DuckDuckGo(HTML) を一般化クエリで検索。生議事録は渡さない前提で query を受ける。"""
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
        out = []
        for m in re.finditer(r'result__a[^>]*>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>', html, re.S):
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            snip = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title:
                out.append({"title": title[:120], "snippet": snip[:240]})
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        return [{"title": "(web検索失敗)", "snippet": str(e)[:120]}]


def research_one(q, use_web):
    """1論点を調査して {question, answer, judgment, wiki_pages, web} を返す。"""
    wiki_hits = wiki_search(q.get("keywords", []))
    web_hits = web_search(q.get("web_query") or q["q"]) if use_web else []

    ctx = ""
    if wiki_hits:
        ctx += "【llm-wiki 該当】\n" + "\n".join(
            f"- [[{h['page']}]]: " + " / ".join(h["evidence"]) for h in wiki_hits) + "\n"
    if web_hits:
        ctx += "【Web 検索結果（一般情報）】\n" + "\n".join(
            f"- {h['title']}: {h['snippet']}" for h in web_hits) + "\n"
    if not ctx:
        ctx = "（ナレッジベース・Webとも該当なし）"

    sysp = ("あなたは会議の論点を調べるアシスタント。与えられた根拠だけを使って簡潔に答える。"
            "根拠が弱い/無い場合は正直に『要追加調査』とし、憶測で断定しない。日本語。")
    usr = (f"論点: {q['q']}\n\n根拠:\n{ctx}\n\n"
           "次を JSON だけで出力: {\"answer\":\"120字程度の回答\","
           "\"judgment\":\"AIの判断・推奨アクション(80字)\",\"confidence\":\"高|中|低\"}")
    res = llm_json(sysp, usr, 1800) or {}
    return {
        "question": q["q"],
        "answer": res.get("answer", "（合成失敗）"),
        "judgment": res.get("judgment", ""),
        "confidence": res.get("confidence", "低"),
        "wiki_pages": [h["page"] for h in wiki_hits],
        "web": web_hits,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--web", action="store_true", help="Web検索を有効化（一般化クエリのみ送信）")
    ap.add_argument("--workers", type=int, default=3, help="論点調査の並列数")
    ap.add_argument("--date", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    tpath = args.transcript
    if not os.path.isfile(tpath):
        sys.exit(f"見つかりません: {tpath}")
    transcript = read(tpath)
    src = re.sub(r"\.[^.]+$", "", os.path.basename(tpath))
    mdate = args.date or (re.match(r"(\d{4}-\d{2}-\d{2})", src) or [None, today()])[1] if re.match(r"\d{4}-\d{2}-\d{2}", src) else (args.date or today())
    slug = src if re.match(r"\d{4}-\d{2}-\d{2}", src) else f"{mdate}-{src}"
    out_dir = os.path.join(ROOT, "data", "wiki", "meetings")
    out_path = os.path.join(out_dir, f"{slug}.包括.md")
    if os.path.exists(out_path) and not args.force:
        print(f"skip（既存）: {os.path.relpath(out_path, ROOT)}")
        return

    soul, agent = read(os.path.join(ROOT, "SOUL.md")), read(os.path.join(ROOT, "AGENT.md"))

    # 1) 抽出
    print("[1/3] 論点・決定事項を抽出…", file=sys.stderr)
    ext_sys = soul + "\n\n" + agent + ("\n\n---\n会議の文字起こしを分析し、以下を JSON で出力する。"
        "questions は本当に調べる価値のある論点だけ（0〜5件）。web_query は固有名詞・社名・人名を除いた"
        "一般化した検索語にする（外部に出すため）。")
    ext_usr = (f"文字起こし:\n{transcript}\n\n"
        "JSON形式: {\"summary\":\"3-4文\",\"decisions\":[\"..\"],"
        "\"next_actions\":[{\"who\":\"\",\"what\":\"\",\"due\":\"\"}],"
        "\"questions\":[{\"q\":\"論点\",\"keywords\":[\"ナレッジベース検索語\"],\"web_query\":\"一般化した検索語\"}]}")
    base = llm_json(ext_sys, ext_usr, 3000) or {}
    questions = base.get("questions", []) or []

    # 2) 並列調査
    print(f"[2/3] {len(questions)}件の論点を調査（web={'ON' if args.web else 'OFF'}, 並列{args.workers}）…", file=sys.stderr)
    researched = []
    if questions:
        with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            researched = list(ex.map(lambda q: research_one(q, args.web), questions))

    # 3) 組み立て（Pythonで決定的に）
    print("[3/3] 包括ノートを組み立て…", file=sys.stderr)
    L = []
    L.append("---")
    L.append(f'title: "{src} 包括議事録 {mdate}"')
    L.append("type: meeting-note-comprehensive")
    L.append(f"date: {mdate}")
    L.append(f"created: {today()}")
    L.append(f"updated: {today()}")
    L.append(f'source: "[[{src}]]"')
    L.append(f"research_web: {str(args.web).lower()}")
    L.append("---\n")
    L.append(f"# {src} 包括議事録 {mdate}\n")
    L.append("> 調査付き自動生成。回答は根拠（llm-wiki引用/Web）に基づく AI 合成で、確度表示付き。最終判断は人間が行う。\n")
    L.append("## 概要\n" + (base.get("summary", "（抽出失敗）")) + "\n")
    if base.get("decisions"):
        L.append("## 決定事項")
        L += [f"- {d}" for d in base["decisions"]]
        L.append("")
    if base.get("next_actions"):
        L.append("## ネクストアクション")
        for a in base["next_actions"]:
            who = f"[{a.get('who','')}] " if a.get("who") else ""
            due = f"（{a.get('due','')}）" if a.get("due") else ""
            L.append(f"- [ ] {who}{a.get('what','')}{due}")
        L.append("")
    if researched:
        L.append("## 論点と調査結果（AI）")
        for r in researched:
            L.append(f"### ❓ {r['question']}")
            L.append(f"- **回答**（確度: {r['confidence']}）: {r['answer']}")
            if r["judgment"]:
                L.append(f"- **AIの判断**: {r['judgment']}")
            if r["wiki_pages"]:
                L.append("- **llm-wiki 引用**: " + " ".join(f"[[{p}]]" for p in r["wiki_pages"]))
            if r["web"]:
                L.append("- **Web参考**: " + "; ".join(f"{w['title']}" for w in r["web"] if w.get("title")))
            L.append("")
    L.append("## 記録メタ")
    L.append(f"- 生成: {today()} / モデル: {MODEL} / Web調査: {'有効' if args.web else '無効(ローカルのみ)'}")
    L.append(f"- 元文字起こし: [[{src}]]")
    L.append("- 注意: 回答はローカルLLMの合成。固有名詞・数値・機密は人間が検証すること。\n")

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"✔ 包括議事録: {os.path.relpath(out_path, ROOT)}")


if __name__ == "__main__":
    main()
