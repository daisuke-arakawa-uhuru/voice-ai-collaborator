#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""redact — llm-wiki 取り込み用 クレデンシャル/PII マスキングツール

ソースを sources/ に取り込む際、個人情報・APIキー・トークン・シークレット・
秘密鍵/公開鍵などのクレデンシャルを、既存の `[REDACTED_*]` 規約に沿って
インライン置換で伏せる。

【特権】このツールは llm-wiki の「sources 不変原則」の**例外**として、
sources/ 配下（および inbox/ 等）を直接書き換えてよい唯一の整形ツール。
ただし変更は「機微情報のマスキング」に限られ、それ以外の本文は変えない。

使い方:
  python3 tools/redact.py <file|dir> ...            # 検査（ドライラン）。何も書かない
  python3 tools/redact.py <file|dir> ... --apply    # 実際にマスクして上書き保存
  python3 tools/redact.py llm-wiki/inbox --apply     # 取り込み前に inbox をまとめて浄化
  python3 tools/redact.py <file> --only private_key,jwt,kv_secret
  python3 tools/redact.py <file> --exclude ipv4,aws_account_id
  python3 tools/redact.py <file> --list             # ルール一覧を表示

終了コード: HIGH 信頼度の秘密（鍵・トークン等）を検出したら 1（ingest ゲート用）。
"""
import re, os, sys, glob, argparse

VAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ai-wiki/

# ---- 検出ルール ------------------------------------------------------------
# 各ルール: name, confidence(HIGH/REVIEW), placeholder, regex, desc, [value_group]
# value_group を指定した場合、その捕捉群のみを placeholder に置換（key= は残す）。
RULES = []

def rule(name, conf, placeholder, pattern, desc, flags=0, value_group=None):
    RULES.append({
        "name": name, "conf": conf, "ph": placeholder,
        "re": re.compile(pattern, flags), "desc": desc, "vg": value_group,
    })

# --- HIGH: 構造的な秘密（誤検知ほぼ無し。検出されたら ingest を止めるべき） ---
rule("private_key", "HIGH", "[REDACTED_PRIVATE_KEY]",
     r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----.*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----",
     "PEM 秘密鍵ブロック（RSA/EC/OpenSSH/PGP）", flags=re.DOTALL)
rule("public_key", "HIGH", "[REDACTED_PUBLIC_KEY]",
     r"-----BEGIN (?:RSA )?PUBLIC KEY-----.*?-----END (?:RSA )?PUBLIC KEY-----",
     "PEM 公開鍵ブロック", flags=re.DOTALL)
rule("ssh_pubkey", "HIGH", "[REDACTED_SSH_PUBKEY]",
     r"ssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/=]{80,}(?:\s+\S+)?",
     "OpenSSH 公開鍵行")
rule("aws_access_key", "HIGH", "[REDACTED_AWS_ACCESS_KEY]",
     r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b",
     "AWS アクセスキーID")
rule("gcp_api_key", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API キー")
rule("slack_token", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b", "Slack トークン(xoxb/xoxp 等)")
rule("github_token", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bgh[pousr]_[0-9A-Za-z]{30,}\b", "GitHub トークン(ghp_/gho_ 等)")
rule("openai_key", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bsk-[A-Za-z0-9_\-]{20,}\b", "OpenAI/汎用 sk- キー")
rule("heroku_token", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bHRKU-[A-Za-z0-9_\-]{20,}\b", "Heroku トークン(HRKU- 接頭辞)")
rule("twilio_sid", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\b(?:AC|SK)[0-9a-fA-F]{32}\b", "Twilio Account/API Key SID(AC…/SK…)")
rule("stripe_key", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b|\bwhsec_[0-9A-Za-z]{16,}\b",
     "Stripe シークレット/制限キー(sk_/rk_live_test_)・Webhook署名(whsec_)")
rule("sendgrid_key", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bSG\.[0-9A-Za-z_\-]{16,}\.[0-9A-Za-z_\-]{16,}\b", "SendGrid API キー(SG.xxx.yyy)")
rule("npm_token", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bnpm_[A-Za-z0-9]{36}\b", "npm アクセストークン(npm_ 接頭辞)")
rule("pypi_token", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bpypi-[A-Za-z0-9_\-]{16,}\b", "PyPI API トークン(pypi- 接頭辞)")
rule("google_oauth_secret", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bGOCSPX-[A-Za-z0-9_\-]{20,}\b", "Google OAuth クライアントシークレット(GOCSPX-)")
rule("cloudflare_ca_key", "HIGH", "[REDACTED_CREDENTIAL]",
     r"\bv1\.0-[0-9a-f\-]{40,}\b", "Cloudflare Origin CA キー(v1.0- 接頭辞)")
rule("jwt", "HIGH", "[REDACTED_JWT]",
     r"\beyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\b",
     "JWT(3セグメント)")
# key = value 形式の汎用シークレット（値だけ置換）。
# 先頭に env 変数風の接頭辞を許容し HEROKU_API_KEY / DB_PASSWORD 等も拾う。
rule("kv_secret", "HIGH", "[REDACTED_CREDENTIAL]",
     r"(?i)\b(?:[a-z0-9]+[_-])*(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|api[_-]?token|access[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?token|secret[_-]?key|x-api-key)\b\s*[:=]\s*[\"']?([^\s\"'`,}\])]{6,})[\"']?",
     "key=value 形式のシークレット値(env変数風の接頭辞も可)", value_group=1)
# URL 埋め込みクレデンシャル user:pass@host（pass だけ置換）
rule("url_cred", "HIGH", "[REDACTED_CREDENTIAL]",
     r"(?i)\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:([^\s:/@]{3,})@",
     "URL 埋め込みパスワード(MongoDB Atlas の mongodb+srv:// 含む)", value_group=1)

# --- REVIEW: PII / 文脈依存（誤検知あり。--apply 前にレポート確認を推奨） ---
rule("email", "REVIEW", "[REDACTED_EMAIL]",
     r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "メールアドレス")
rule("phone_jp", "REVIEW", "[REDACTED_PHONE]",
     r"\b0\d{1,3}-\d{2,4}-\d{4}\b", "電話番号(日本)")
rule("credit_card", "REVIEW", "[REDACTED_CREDENTIAL]",
     r"\b(?:\d{4}[ \-]){3}\d{4}\b", "クレジットカード番号形式")
rule("aws_account_id", "REVIEW", "[REDACTED_AWS_ACCOUNT_ID]",
     r"(?<![\d\-])\d{12}(?![\d\-])", "12桁数字(AWSアカウントID候補・誤検知注意)")
# IPv4（プライベート/予約レンジは除外＝アーキ図の CIDR を壊さない）
rule("ipv4", "REVIEW", "[REDACTED_IP]",
     r"\b(?!(?:10|127)\.)(?!169\.254\.)(?!192\.168\.)(?!172\.(?:1[6-9]|2\d|3[01])\.)(?!0\.0\.0\.0)(?!255\.255\.255\.255)(?:\d{1,3}\.){3}\d{1,3}\b",
     "IPv4(グローバル。private/予約は除外)")

PLACEHOLDER_RE = re.compile(r"\[REDACTED_[A-Z0-9_]*\]|\[REDACTED\]")
NAMES = {r["name"] for r in RULES}
HIGH = {r["name"] for r in RULES if r["conf"] == "HIGH"}


def redact_text(text, enabled):
    """順にマスクし、(new_text, counts, samples) を返す。既存プレースホルダは温存。"""
    counts = {}
    samples = {}
    for r in RULES:
        if r["name"] not in enabled:
            continue
        vg = r["vg"]; ph = r["ph"]; name = r["name"]

        def _sub(m):
            # 既にプレースホルダを含む一致は触らない（冪等性）
            whole = m.group(0)
            if PLACEHOLDER_RE.search(whole):
                return whole
            if vg:
                val = m.group(vg)
                if (not val or PLACEHOLDER_RE.search(val) or val.startswith("[REDACTED")
                        or val[0] in "{$<" or set(val) <= set("*xX-")):
                    return whole
                counts[name] = counts.get(name, 0) + 1
                samples.setdefault(name, []).append(whole[:60])
                s, e = m.span(vg)
                return whole[: s - m.start()] + ph + whole[e - m.start():]
            counts[name] = counts.get(name, 0) + 1
            samples.setdefault(name, []).append(whole[:60])
            return ph

        text = r["re"].sub(_sub, text)
    return text, counts, samples


def collect(paths):
    files = []
    for p in paths:
        ap = p if os.path.isabs(p) else os.path.join(os.getcwd(), p)
        if os.path.isdir(ap):
            files += glob.glob(os.path.join(ap, "**", "*.md"), recursive=True)
        elif os.path.isfile(ap):
            files.append(ap)
        else:
            print(f"⚠ 見つかりません: {p}", file=sys.stderr)
    return sorted(set(files))


def main():
    ap = argparse.ArgumentParser(description="クレデンシャル/PII マスキング（sources 更新特権あり）")
    ap.add_argument("paths", nargs="*", help="対象ファイル/ディレクトリ")
    ap.add_argument("--apply", action="store_true", help="実際にマスクして上書き保存")
    ap.add_argument("--only", help="使うルール（カンマ区切り）")
    ap.add_argument("--exclude", help="除外するルール（カンマ区切り）")
    ap.add_argument("--high-only", action="store_true", help="HIGH 信頼度ルールのみ")
    ap.add_argument("--quiet", action="store_true", help="サマリのみ")
    ap.add_argument("--list", action="store_true", help="ルール一覧を表示して終了")
    args = ap.parse_args()

    if args.list:
        print("ルール一覧（name  信頼度  → placeholder : 説明）:")
        for r in RULES:
            print(f"  {r['name']:16s} {r['conf']:6s} → {r['ph']:26s} {r['desc']}")
        return 0

    enabled = set(NAMES)
    if args.high_only:
        enabled = set(HIGH)
    if args.only:
        enabled = {x.strip() for x in args.only.split(",")} & NAMES
    if args.exclude:
        enabled -= {x.strip() for x in args.exclude.split(",")}
    if not args.paths:
        ap.error("対象パスを指定してください（例: tools/redact.py llm-wiki/inbox）")

    files = collect(args.paths)
    total = {}
    hit_high = 0
    changed_files = 0
    print(f"{'[APPLY]' if args.apply else '[DRY-RUN]'} 対象 {len(files)} files / 有効ルール {len(enabled)}\n")
    for p in files:
        rel = os.path.relpath(p, VAULT)
        old = open(p, encoding="utf-8").read()
        new, counts, samples = redact_text(old, enabled)
        if not counts:
            continue
        changed_files += 1
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
            if k in HIGH:
                hit_high += v
        marks = " ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        print(f"● {rel}\n    {marks}")
        if not args.quiet:
            for k, ss in samples.items():
                for s in ss[:2]:
                    print(f"      [{k}] {s.strip()[:70]}")
        if args.apply and new != old:
            open(p, "w", encoding="utf-8").write(new)

    print("\n=== サマリ ===")
    if not total:
        print("検出なし。")
        return 0
    for k in sorted(total, key=lambda x: (-total[x], x)):
        conf = "HIGH" if k in HIGH else "REVIEW"
        print(f"  {k:16s} {conf:6s} {total[k]:5d} 件")
    print(f"  {'':16s} {'':6s} 対象ファイル {changed_files}")
    if args.apply:
        print("\n✅ マスク適用済み（上書き保存）。")
    else:
        print("\nドライラン。書き込むには --apply を付けてください。")
        print("※ REVIEW 系(email/ipv4/aws_account_id/phone 等)は誤検知があり得ます。")
        print("  レポートを確認し、必要なら --only / --exclude で範囲を絞ってから --apply を。")
    print("\n注意: 氏名など文脈依存の個人情報は正規表現では拾えません。")
    print("  [REDACTED_END_USER_NAME] 等は AI 併用でレビューしてください。")
    # HIGH 秘密が残っていれば非ゼロ終了（ingest ゲート用）。--apply 後は 0。
    return 1 if (hit_high and not args.apply) else 0


if __name__ == "__main__":
    sys.exit(main())
