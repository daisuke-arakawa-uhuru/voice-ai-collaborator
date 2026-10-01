#!/usr/bin/env python3
"""dashboard/server.py — voice-ai-collaborator ローカル司令塔ダッシュボードのバックエンド.

会議 → Amical文字起こし → export → 整形 → 包括議事録 の各段の状態を俯瞰し、
アプリ起動やパイプライン実行をボタンから行う。ブラウザ単体ではシェルを叩けないため、
この軽量サーバーが橋渡しする。

安全設計:
  - **127.0.0.1 のみ bind**（LAN からは触れない）。
  - アクションは下の ACTIONS 辞書の**固定許可リストのみ**。リクエストは名前(key)を渡すだけで、
    任意のコマンド文字列は一切受け付けない（shell=False・引数配列）。
  - 会議本文は表示しない（先頭要約タイトルのみ）。全データはローカル。

使い方:  python3 dashboard/server.py [--port 8787]
"""
import argparse, datetime, glob, json, os, re, shutil, sqlite3, subprocess, tempfile, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SRC_DIR = os.path.join(DATA, "sources", "transcripts")
NOTE_DIR = os.path.join(DATA, "wiki", "meetings")
LOG = os.path.join(DATA, ".logs", "pipeline.log")
LOCK = os.path.join(DATA, ".logs", "pipeline.lock")
DB = os.path.expanduser("~/Library/Application Support/Amical/amical.db")
LLM_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/").replace("host.docker.internal", "localhost")


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def config_model(kind):
    cfg = read(os.path.join(ROOT, "config.env"))
    key = {"compile": "COMPILE_MODEL", "research": "RESEARCH_MODEL"}[kind]
    m = re.search(rf"^{key}=(.+)$", cfg, re.M) or re.search(r"^LLM_MODEL=(.+)$", cfg, re.M)
    return m.group(1).strip() if m else "google/gemma-4-e4b"


def sh(args, timeout=8, cwd=None, env=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env={**os.environ, **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


# ---- 状態収集 -------------------------------------------------------------
def lm_studio():
    try:
        with urllib.request.urlopen(f"{LLM_BASE}/models", timeout=2) as r:
            data = json.load(r)
        models = [m["id"] for m in data.get("data", [])]
        return {"up": True, "models": models, "compile_model": config_model("compile"),
                "research_model": config_model("research")}
    except Exception:
        return {"up": False, "models": [], "compile_model": config_model("compile"),
                "research_model": config_model("research")}


def proc_running(name):
    rc, out = sh(["pgrep", "-x", name], timeout=4)
    return rc == 0


def n8n_status():
    rc, out = sh(["docker", "ps", "--filter", "name=amical-n8n", "--format", "{{.Status}}"], timeout=6)
    up = rc == 0 and out.strip().startswith("Up")
    return {"up": up, "detail": out.strip()}


def pipeline_agent():
    rc, out = sh(["launchctl", "list"], timeout=6)
    loaded, last_exit = False, None
    for line in out.splitlines():
        if "ai.amical.pipeline" in line:
            loaded = True
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
                last_exit = int(parts[1])
    running = os.path.isdir(LOCK)
    last_run = ""
    if os.path.isfile(LOG):
        for line in reversed(read(LOG).splitlines()):
            m = re.search(r"\[(.*?)\] pipeline done", line)
            if m:
                last_run = m.group(1); break
    return {"loaded": loaded, "last_exit": last_exit, "running": running, "last_run": last_run}


def db_transcriptions():
    if not os.path.isfile(DB):
        return []
    tmp = tempfile.mkdtemp(prefix="amical_dash_")
    base = os.path.join(tmp, "a.db")
    shutil.copy2(DB, base)
    for suf in ("-wal", "-shm"):
        if os.path.exists(DB + suf):
            shutil.copy2(DB + suf, base + suf)
    rows = []
    try:
        con = sqlite3.connect(base); con.row_factory = sqlite3.Row
        for r in con.execute("SELECT id, created_at, timestamp, audio_duration_ms, text FROM transcriptions ORDER BY created_at DESC"):
            rows.append({"id": r["id"], "ts": r["created_at"] or r["timestamp"],
                        "dur": r["audio_duration_ms"] or 0, "text": (r["text"] or "")})
        con.close()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    return rows


def flow():
    # exported: transcripts の frontmatter amical_id → basename
    exported = {}
    for f in glob.glob(os.path.join(SRC_DIR, "*.md")):
        m = re.search(r"amical_id:\s*(\d+)", read(f))
        if m:
            exported[int(m.group(1))] = os.path.basename(f)[:-3]
    items = []
    for r in db_transcriptions():
        empty = not r["text"].strip()
        base = exported.get(r["id"])
        compiled = bool(base) and os.path.exists(os.path.join(NOTE_DIR, f"{base}.md"))
        researched = bool(base) and os.path.exists(os.path.join(NOTE_DIR, f"{base}.包括.md"))
        stage = ("empty" if empty else "researched" if researched else "compiled" if compiled
                 else "exported" if base else "db-only")
        try:
            d = datetime.datetime.fromtimestamp(int(r["ts"]))
            date, time = d.strftime("%Y-%m-%d"), d.strftime("%H:%M")
        except Exception:
            date, time = "?", "?"
        title = (r["text"].strip().splitlines()[0][:34] if r["text"].strip() else "（空録音）")
        items.append({"id": r["id"], "date": date, "time": time,
                      "dur_s": round(r["dur"] / 1000), "stage": stage, "title": title,
                      "note": f"{base}.md" if compiled else ""})
    return items


def status():
    items = flow()
    stages = ["db-only", "exported", "compiled", "researched", "empty"]
    counts = {s: sum(1 for it in items if it["stage"] == s) for s in stages}
    counts["total"] = len(items)
    counts["pending"] = counts["db-only"] + counts["exported"]  # 未整形（要処理）
    # 日毎
    daily = {}
    for it in items:
        daily.setdefault(it["date"], {s: 0 for s in stages})
        daily[it["date"]][it["stage"]] += 1
    daily_list = [{"date": d, **c} for d, c in sorted(daily.items(), reverse=True)]
    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "apps": {
            "lm_studio": lm_studio(),
            "amical": {"up": proc_running("Amical")},
            "n8n": n8n_status(),
            "pipeline": pipeline_agent(),
        },
        "counts": counts, "items": items[:60], "daily": daily_list,
    }


# ---- アクション（固定許可リスト）-----------------------------------------
def act_start_all():
    sh(["open", "-a", "LM Studio"], timeout=10)
    sh(["open", "-a", "Amical"], timeout=10)
    sh(["docker", "compose", "up", "-d"], cwd=ROOT, timeout=60)
    return "LM Studio / Amical / n8n を起動しました（初回はモデルロードに時間がかかります）"


def act_warmup():
    model = config_model("compile")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 1, "stream": False}).encode()
    req = urllib.request.Request(f"{LLM_BASE}/chat/completions", data=body,
                                headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120)
        return f"モデル {model} をロード（warmup）しました"
    except Exception as e:
        return f"warmup 失敗: {e}（LM Studio サーバーが起動し、Local Server が ON か確認）"


def act_run_pipeline():
    if os.path.isdir(LOCK):
        return "パイプラインは既に実行中です"
    subprocess.Popen(["bash", os.path.join(ROOT, "scripts", "pipeline.sh")],
                     env={**os.environ, "RESEARCH": "1"},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "パイプラインをバックグラウンドで開始しました（進捗は pipeline.log / 本画面で確認）"


def act_open_notes():
    sh(["open", NOTE_DIR], timeout=6)
    return "整形ノートのフォルダを開きました"


def act_check():
    rc, out = sh(["python3", os.path.join(ROOT, "tools", "wikicheck.py")], timeout=30)
    return out[-1500:] if out else "（出力なし）"


ACTIONS = {
    "start-all": act_start_all,
    "start-lmstudio": lambda: (sh(["open", "-a", "LM Studio"]) and "LM Studio を起動") or "LM Studio を起動",
    "start-amical": lambda: (sh(["open", "-a", "Amical"]) and "Amical を起動") or "Amical を起動",
    "start-n8n": lambda: (sh(["docker", "compose", "up", "-d"], cwd=ROOT, timeout=60) and "n8n を起動") or "n8n を起動",
    "warmup-model": act_warmup,
    "run-pipeline": act_run_pipeline,
    "open-notes": act_open_notes,
    "check": act_check,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            html = read(os.path.join(ROOT, "dashboard", "index.html"))
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path.startswith("/api/status"):
            return self._send(200, json.dumps(status(), ensure_ascii=False))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        m = re.match(r"^/api/action/([a-z-]+)$", self.path)
        if not m or m.group(1) not in ACTIONS:
            return self._send(400, json.dumps({"error": "unknown action"}))
        try:
            msg = ACTIONS[m.group(1)]()
        except Exception as e:
            return self._send(500, json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False))
        self._send(200, json.dumps({"ok": True, "message": msg}, ensure_ascii=False))

    def log_message(self, *a):
        pass  # 静音


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"amical dashboard → http://127.0.0.1:{args.port}  (Ctrl-C で停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
