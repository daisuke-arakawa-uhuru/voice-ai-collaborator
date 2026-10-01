#!/usr/bin/env node
// compile-note.mjs — 文字起こし(sources)→整形ノート(wiki) を、n8n コンテナ内の node で実行する版。
// n8n の Execute Command ノードから `node /project/scripts/compile-note.mjs --all` で呼ぶ想定。
// compile-note.py（ホスト用）と同じ思想: SOUL.md / AGENT.md を毎回読み込み、LLM は母艦のみ、sources は不変。
//
// パス（コンテナ内マウント）:
//   /project/{SOUL.md,AGENT.md,scripts}  … リポジトリ（ro）
//   /data/sources/transcripts            … 文字起こし（raw）
//   /data/wiki/meetings                  … 整形ノート出力
// 環境変数: LLM_BASE_URL(既定 http://host.docker.internal:1234/v1) / LLM_MODEL(既定 google/gemma-4-e4b)
import fs from 'node:fs';
import path from 'node:path';

const PROJECT = process.env.PROJECT_DIR || '/project';
const DATA = process.env.DATA_DIR || '/data';
const SRC_DIR = `${DATA}/sources/transcripts`;
const OUT_DIR = `${DATA}/wiki/meetings`;
const INDEX = `${DATA}/wiki/index.md`;
const BASE = (process.env.LLM_BASE_URL || 'http://host.docker.internal:1234/v1').replace(/\/$/, '');
const MODEL = process.env.COMPILE_MODEL || process.env.LLM_MODEL || 'google/gemma-4-e4b';

const readSafe = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch { return ''; } };
const today = () => new Date().toISOString().slice(0, 10);
const dateOf = (file) => {
  const m = path.basename(file).match(/(\d{4}-\d{2}-\d{2})/);
  if (m) return m[1];
  try { return fs.statSync(`${SRC_DIR}/${file}`).mtime.toISOString().slice(0, 10); } catch { return today(); }
};
const stripFences = (t) => {
  t = t.trim();
  if (t.startsWith('```')) t = t.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
  return t;
};

async function callLLM(system, user) {
  const body = {
    model: MODEL,
    messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
    stream: false, temperature: 0.2, max_tokens: 4000,
  };
  const r = await fetch(`${BASE}/chat/completions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`LLM HTTP ${r.status}`);
  const j = await r.json();
  return (j.choices?.[0]?.message?.content || '').trim();
}

async function compileOne(file) {
  const base = file.replace(/\.[^.]+$/, '');
  const mdate = dateOf(file);
  // base が既に日付始まりならそのまま、そうでなければ日付を前置（二重日付を防ぐ）
  const slug = /^\d{4}-\d{2}-\d{2}/.test(base) ? base : `${mdate}-${base}`;
  const outPath = `${OUT_DIR}/${slug}.md`;
  if (fs.existsSync(outPath)) return { skipped: true, base };

  const transcript = readSafe(`${SRC_DIR}/${file}`);
  const soul = readSafe(`${PROJECT}/SOUL.md`);
  const agent = readSafe(`${PROJECT}/AGENT.md`);
  const system = soul + '\n\n' + agent +
    '\n\n---\n今から文字起こし1件を『整形ノート』にコンパイルする。AGENT.md の「整形ノートの書式」に厳密に従い、' +
    'frontmatter とセクションを持つ Markdown ノート**だけ**を出力する（前後の説明・コードフェンス・思考は出力しない）。日本語で書く。';
  const user = `会議日: ${mdate}\n作成日: ${today()}\nタイトル: ${base} ${mdate}\n` +
    `ソースファイル名（wikilink用・拡張子なし）: ${base}\n\n=== 文字起こし本文 ===\n${transcript}`;

  let note = stripFences(await callLLM(system, user));
  if (!note.startsWith('---')) {
    note = `---\ntitle: "${base} ${mdate}"\ntype: meeting-note\ndate: ${mdate}\n` +
      `created: ${today()}\nupdated: ${today()}\nsource: "[[${base}]]"\n---\n\n` + note;
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(outPath, note.endsWith('\n') ? note : note + '\n');

  if (!fs.existsSync(INDEX)) {
    fs.writeFileSync(INDEX, `---\ntitle: "会議まとめ Index"\ntype: index\n` +
      `created: ${today()}\nupdated: ${today()}\n---\n\n# 会議まとめ カタログ\n\n| 日付 | ノート | ソース |\n|---|---|---|\n`);
  }
  fs.appendFileSync(INDEX, `| ${mdate} | [[${slug}]] | [[${base}]] |\n`);
  return { created: true, base, outPath };
}

const args = process.argv.slice(2);
const all = args.includes('--all') || args.filter((a) => !a.startsWith('--')).length === 0;
const targets = all
  ? (fs.existsSync(SRC_DIR) ? fs.readdirSync(SRC_DIR).filter((f) => /\.(md|txt)$/i.test(f)) : [])
  : args.filter((a) => !a.startsWith('--')).map((a) => path.basename(a));

let created = 0, skipped = 0;
for (const f of targets) {
  try {
    const r = await compileOne(f);
    if (r.created) { created++; console.log(`✔ created: ${r.base}`); }
    else if (r.skipped) { skipped++; }
  } catch (e) { console.error(`✗ failed: ${f} — ${e.message}`); }
}
console.log(`done: created=${created} skipped=${skipped} scanned=${targets.length}`);
