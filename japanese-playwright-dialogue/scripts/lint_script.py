#!/usr/bin/env python3
"""lint_script.py — 台本(scene.md)をプロット(structure.yaml)と突き合わせて lint する

使い方:
    python3 lint_script.py plot/structure.yaml script/scene-1-2.md [scene-1-3.md ...]
    python3 lint_script.py plot/structure.yaml script/          # ディレクトリ一括
    python3 lint_script.py plot/structure.yaml script/ --characters characters/

記法:
    せりふ   = 人物名<TAB>本文(1発話1行)
    ト書き   = 行頭<TAB>の行
    ト書きブロックとせりふの間は空行1行必須

終了コード: error があれば 1
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

ERROR, WARNING, INFO = "error", "warning", "info"
SPEECH_RE = re.compile(r"^([^\t\s]+)\t(.*)$")
ZSP_SPEECH_RE = re.compile(r"^([^\t\s　]{1,8})　(.*)$")  # 全角スペース区切り(要正規化)
FIRST_PERSON_SET = ["俺", "僕", "私", "わたし", "あたし", "わし", "おれ", "ぼく"]
MONOLOGUE_LIMIT = 400
BUDGET_TOLERANCE = 0.20


class Reporter:
    def __init__(self):
        self.issues = []

    def add(self, level, rule, loc, msg):
        self.issues.append((level, rule, loc, msg))

    def dump(self):
        order = {ERROR: 0, WARNING: 1, INFO: 2}
        icon = {ERROR: "✖", WARNING: "▲", INFO: "ℹ"}
        for level, rule, loc, msg in sorted(self.issues, key=lambda x: order[x[0]]):
            print(f"{icon[level]} [{level}] {rule} ({loc}): {msg}")
        counts = {lv: sum(1 for i in self.issues if i[0] == lv) for lv in order}
        print(f"\n--- error: {counts[ERROR]} / warning: {counts[WARNING]} / info: {counts[INFO]} ---")
        return counts[ERROR] == 0


def load_plot_index(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    index = {}
    for act in data.get("acts") or []:
        for fs in act.get("french_scenes") or []:
            index[str(fs.get("fs"))] = fs
    return data, index


def load_characters(chardir):
    sheets = {}
    if chardir and Path(chardir).is_dir():
        for f in sorted(Path(chardir).glob("*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if d.get("name"):
                sheets[d["name"]] = d
    return sheets


def parse_scene(text):
    """frontmatter・本文行リスト・行番号オフセットを返す"""
    fm, body, offset = {}, text, 0
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            offset = parts[1].count("\n")  # frontmatter が占める行数分ずらす
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)  # コメントは無視
    return fm, body.split("\n"), offset


def classify(line):
    if not line.strip():
        return "blank", None, None
    if line.startswith("\t"):
        return "stage", None, line.strip()
    m = SPEECH_RE.match(line)
    if m:
        return "speech", m.group(1), m.group(2)
    m = ZSP_SPEECH_RE.match(line)
    if m:
        return "speech_zsp", m.group(1), m.group(2)
    return "invalid", None, line


def lint_scene(path, plot, index, sheets, rep):
    loc = path.name
    fm, lines, offset = parse_scene(path.read_text(encoding="utf-8"))

    fsid = str(fm.get("fs", ""))
    fs = index.get(fsid)
    if not fs:
        rep.add(ERROR, "fs-ref", loc, f"frontmatter の fs {fsid!r} がプロットに存在しません")
        return
    on_stage = set(fs.get("on_stage") or [])

    # --- 行分類と形式チェック ---
    events = []  # (lineno, kind, name, text)
    for n, line in enumerate(lines, 1 + offset):
        kind, name, text = classify(line)
        if kind == "invalid":
            rep.add(ERROR, "format", f"{loc}:{n}",
                    "せりふ(名前<TAB>本文)でもト書き(行頭TAB)でもない行です")
            continue
        if kind == "speech_zsp":
            rep.add(WARNING, "format-zsp", f"{loc}:{n}",
                    "区切りが全角スペースです(タブへの正規化を推奨)")
            kind = "speech"
        events.append((n, kind, name, text))

    # --- ト書き⇔せりふ間の空行 ---
    prev_kind, prev_n = None, None
    for n, kind, name, text in events:
        if kind == "blank":
            prev_kind, prev_n = "blank", n
            continue
        if prev_kind in ("speech", "stage") and prev_kind != kind:
            rep.add(ERROR, "blank-line", f"{loc}:{n}",
                    "ト書きとせりふの間には空行が1行必要です")
        prev_kind, prev_n = kind, n

    speeches = [(n, name, text) for n, kind, name, text in events if kind == "speech"]
    # かぶり記号は文字数予算から除外
    budget_speeches = [(n, name, re.sub(r"[★☆▲△]", "", text)) for n, name, text in speeches]

    # --- 話者がプロットの on_stage に居るか ---
    voices = set(plot.get("voices") or [])
    casting = plot.get("casting") or {}  # {俳優名: [役名, ...]} 兼ね役の宣言
    speakers = set()
    for n, name, text in speeches:
        if name.endswith("Ｍ"):          # 語り(モノローグ)行: 舞台上に居なくてもよい
            base = name[:-1]
            if base not in on_stage:
                rep.add(INFO, "narration", f"{loc}:{n}",
                        f"{base}Ｍ の語り(語り手は舞台外でも可)")
            continue
        speakers.add(name)
        if name in voices:               # 声のみの出演(キャスター、留守電等)
            continue
        if name in casting:              # 兼ね役: 俳優名話者。演じる役のどれかが舞台上ならOK
            if not any(r in on_stage for r in casting[name]):
                rep.add(ERROR, "speaker", f"{loc}:{n}",
                        f"俳優 {name} の役 {casting[name]} はいずれも FS {fsid} の舞台上に居ません")
            continue
        for part in name.split("・"):    # 連名せりふ 名A・名B
            if part not in on_stage:
                rep.add(ERROR, "speaker", f"{loc}:{n}",
                        f"{part} は FS {fsid} の on_stage {sorted(on_stage)} に居ません")

    # --- 舞台上に居るのに一言も喋らない人物 ---
    for name in on_stage - speakers:
        rep.add(INFO, "silent", loc, f"{name} は舞台上に居ますが、せりふがありません(意図的なら無視して良い)")

    # --- 長ぜりふ ---
    if not fs.get("monologue"):
        for n, name, text in speeches:
            if len(text) > MONOLOGUE_LIMIT:
                rep.add(WARNING, "long-speech", f"{loc}:{n}",
                        f"{name} のせりふが {len(text)} 字あります"
                        f"({MONOLOGUE_LIMIT}字超。独白として意図的ならプロット側に monologue: true)")

    # --- 文字数予算(1分 ≒ chars_per_minute 字、±20%) ---
    cpm = plot.get("chars_per_minute", 320)
    budget = (fs.get("duration_min") or 0) * cpm
    count = sum(len(t) for _, _, t in budget_speeches)
    count += sum(len(t) for _, k, _, t in events if k == "stage")
    if budget:
        lo, hi = budget * (1 - BUDGET_TOLERANCE), budget * (1 + BUDGET_TOLERANCE)
        if not (lo <= count <= hi):
            direction = "不足" if count < lo else "超過"
            rep.add(WARNING, "char-budget", loc,
                    f"文字数 {count} 字 / 予算 {budget} 字(duration_min {fs.get('duration_min')}分 × {cpm}字)。"
                    f"許容 {lo:.0f}〜{hi:.0f} 字を{direction}")

    # --- 入れ子相槌 (名前「…」) : 入れ子話者が舞台上に居るか ---
    NEST_RE = re.compile(r"（([^\t\s（）「」]{1,8})「")
    for n, name, text in speeches:
        for nested in NEST_RE.findall(text):
            if nested not in on_stage and nested not in voices and nested not in casting:
                rep.add(WARNING, "nested-speaker", f"{loc}:{n}",
                        f"入れ子相槌の {nested} が舞台上に見当たりません")

    # --- かぶり記号(★☆▲△)の対応 ---
    # 同一記号列(★、☆、☆☆、…)は「どこでかぶるか」と「かぶって始まる発話」の対で使うため、
    # シーン内での出現回数は偶数になるのが原則
    from collections import Counter as _C
    runs = _C()
    for n, name, text in speeches:
        for r in re.findall(r"★+|☆+|▲+|△+", text):
            runs[r] += 1
    for r, c in runs.items():
        if c % 2 == 1:
            rep.add(WARNING, "overlap-pairing", loc,
                    f"かぶり記号 {r!r} の出現が {c} 回(奇数)です。対応相手が欠けている可能性")

    # --- ターン比率(情報のみ) ---
    if len(speakers) >= 2 and len(speeches) >= 8:
        from collections import Counter
        c = Counter(name for _, name, _ in speeches)
        top, cnt = c.most_common(1)[0]
        if cnt / len(speeches) > 0.75:
            rep.add(INFO, "turn-balance", loc,
                    f"せりふの {cnt}/{len(speeches)} が {top} に偏っています(意図的なら無視して良い)")

    # --- キャラシート: 一人称の揺れ / NGワード ---
    for n, name, text in speeches:
        sheet = sheets.get(name)
        if not sheet:
            continue
        declared = sheet.get("first_person")
        if declared:
            for fp in FIRST_PERSON_SET:
                if fp == declared:
                    continue
                if re.search(rf"(?<![ぁ-んァ-ン一-龥]){re.escape(fp)}(?=[はがものにをって、。])", text):
                    rep.add(WARNING, "first-person", f"{loc}:{n}",
                            f"{name} の一人称はキャラシートでは {declared!r} ですが {fp!r} が出現しています"
                            "(他人のせりふの引用なら無視して良い)")
        for ng in sheet.get("ng_words") or []:
            if ng and ng in text:
                rep.add(WARNING, "ng-word", f"{loc}:{n}",
                        f"{name} の NGワード {ng!r} が出現しています")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plot")
    ap.add_argument("scripts", nargs="+")
    ap.add_argument("--characters", default=None)
    args = ap.parse_args()

    plot, index = load_plot_index(args.plot)
    sheets = load_characters(args.characters)

    files = []
    for s in args.scripts:
        p = Path(s)
        files.extend(sorted(p.glob("*.md")) if p.is_dir() else [p])

    rep = Reporter()
    for f in files:
        lint_scene(f, plot, index, sheets, rep)
    ok = rep.dump()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
