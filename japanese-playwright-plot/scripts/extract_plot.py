#!/usr/bin/env python3
"""extract_plot.py — 完成台本からフレンチシーン台帳(structure.yaml相当)を逆生成する

使い方:
    python3 extract_plot.py play.md [--yaml out.yaml]

ヒューリスティック:
    - 場の区切り: '###' 見出し
    - せりふ: 名前<TAB|全角SP>本文(継続行=空行を挟まないプレーン行は結合)
    - 入退場: ト書き内の動詞で判定
        入場系: 入場 / 登場 / 出てくる / 出てきて / 入ってくる
        退場系: 退場 / 去る
      それ以外の移動記述(「家に入っていく」等)は判定せずレポートに残す
    - 舞台に居ないはずの話者が喋ったら、そのFSの頭から居たものとして遡って補正(inferred)

上演時間モデル(『おかえり未来の子』75分実測で校正):
    分数 = 文字数/320 + ターン数×0.7秒 + (間・沈黙)×3秒
"""
import argparse
import re
import sys
from collections import Counter

CPM = 320
TURN_SEC = 0.7
PAUSE_SEC = 3.0
ENTER_RE = re.compile(r"入場|登場|出てくる|出てきて|入ってくる")
EXIT_RE = re.compile(r"退場|去る")
PAUSE_RE = re.compile(r"^(間|沈黙)([。、].*)?$")

GROUP_ALIASES = {"婦人達": ["婦人A", "婦人B", "婦人C"], "3人の婦人": ["婦人A", "婦人B", "婦人C"]}


def parse(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    scenes, cur = [], None
    prev_blank, in_head = True, True
    for raw in lines:
        l = raw.rstrip().rstrip(" ")
        if re.match(r"^#{2,3}\s", l):
            name = re.sub(r"^#+\s*|\*", "", l).strip()
            if "登場人物" in name or "舞台" in name:
                in_head = True
            else:
                in_head = False
                cur = {"name": name, "events": []}
                scenes.append(cur)
            prev_blank = True
            continue
        if in_head or cur is None:
            continue
        if not l.strip():
            prev_blank = True
            continue
        m = re.match(r"^([^\t　\s#>*]{1,8})[\t　](.+)$", l)
        if m:
            cur["events"].append(["speech", m.group(1), m.group(2).strip()])
        elif not prev_blank and cur["events"] and cur["events"][-1][0] == "speech":
            cur["events"][-1][2] += l.strip()  # 継続行
        else:
            cur["events"].append(["stage", None, l.strip()])
        prev_blank = False
    return [s for s in scenes if s["events"]]


def known_names(scenes):
    names = Counter()
    for sc in scenes:
        for kind, name, _ in sc["events"]:
            if kind == "speech":
                for n in name.split("・"):
                    names[n] += 1
    return set(names)


def movement(text, names):
    """ト書き1行から (enters, exits, unparsed) を抽出"""
    enters, exits, unparsed = [], [], []
    for clause in re.split(r"[。]", text):
        if not clause.strip():
            continue
        found = [n for n in names if n in clause]
        for alias, members in GROUP_ALIASES.items():
            if alias in clause or ("婦人" in clause and not found):
                found = members
        is_enter, is_exit = bool(ENTER_RE.search(clause)), bool(EXIT_RE.search(clause))
        if is_enter and found:
            enters += [n for n in found if n not in enters]
        if is_exit and found:
            exits += [n for n in found if n not in exits]
        if (is_enter or is_exit) and not found:
            unparsed.append(clause)
        if not is_enter and not is_exit and re.search(r"入っていく|出て行く|出ていく|はける", clause):
            unparsed.append(clause)
    return enters, exits, unparsed


def extract(scenes):
    names = known_names(scenes)
    report_unparsed = []
    acts = []
    for ai, sc in enumerate(scenes, 1):
        on = set()
        fs_list = [{"enters": [], "exits": [], "inferred": set(), "speeches": [], "pauses": 0, "stage_chars": 0}]
        for kind, name, text in sc["events"]:
            cur = fs_list[-1]
            if kind == "speech":
                for n in name.split("・"):
                    if n not in on:
                        on.add(n)
                        cur["inferred"].add(n)  # 遡って居たことにする
                cur["speeches"].append((name, text))
                cur["pauses"] += text.count("（間）") + text.count("(間)")
            else:
                if PAUSE_RE.match(text):
                    cur["pauses"] += 1
                    continue
                ent, ext, unp = movement(text, names)
                report_unparsed += [(sc["name"], u) for u in unp]
                cur["stage_chars"] += len(text)
                if ent or ext:
                    new_on = (on | set(ent)) - set(ext)
                    fs_list.append({"enters": ent, "exits": ext, "inferred": set(),
                                    "speeches": [], "pauses": 0, "stage_chars": 0,
                                    "on_snapshot": None})
                    on = new_on
            fs_list[-1].setdefault("on_snapshot", None)
        # on_stage を再構築(inferred を初期集合に反映)
        acts.append({"act": ai, "name": sc["name"], "fs": fs_list})
    # on_stage の前進計算
    for act in acts:
        on = set()
        for i, fs in enumerate(act["fs"]):
            if i == 0:
                on = set(fs["inferred"])
            else:
                on = (on | set(fs["enters"])) - set(fs["exits"])
                on |= fs["inferred"]
            fs["on_stage"] = sorted(on)
    return acts, report_unparsed


def duration(fs):
    chars = sum(len(t) for _, t in fs["speeches"]) + fs["stage_chars"]
    return chars / CPM + len(fs["speeches"]) * TURN_SEC / 60 + fs["pauses"] * PAUSE_SEC / 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("play")
    ap.add_argument("--yaml", default=None)
    args = ap.parse_args()

    scenes = parse(args.play)
    acts, unparsed = extract(scenes)

    total, cum = 0, 0.0
    print(f"{'FS':6} {'累積分':>6} {'分':>5} {'発話':>4} {'間':>3}  on_stage(→enters/exits)")
    for act in acts:
        print(f"--- {act['name']} ---")
        for i, fs in enumerate(act["fs"], 1):
            d = duration(fs)
            cum += d
            total += 1
            io = ""
            if fs["enters"]:
                io += " +" + ",".join(fs["enters"])
            if fs["exits"]:
                io += " -" + ",".join(fs["exits"])
            inf = f" (推定初期: {','.join(sorted(fs['inferred']))})" if fs["inferred"] and i > 1 else ""
            print(f"{act['act']}-{i:<4} {cum:6.1f} {d:5.1f} {len(fs['speeches']):4d} {fs['pauses']:3d}  "
                  f"{','.join(fs['on_stage'])}{io}{inf}")
    print(f"\n合計: 物理FS {total} 個 / 推定 {cum:.1f} 分")

    t = cum
    print(f"\n=== パラダイム目安({t:.0f}分換算) ===")
    for key, lo, hi in [("PP1", 12/60, 17/60), ("ミッドポイント", 27/60, 33/60),
                        ("PP2", 40/60, 47/60), ("クライマックス", 48/60, 58/60)]:
        print(f"  {key}: 累積 {lo*t:.0f}〜{hi*t:.0f} 分のあたり")

    if unparsed:
        print(f"\n=== 判定できなかった移動記述({len(unparsed)}件 / 要人間確認) ===")
        for scname, u in unparsed:
            print(f"  [{scname}] {u}")

    if args.yaml:
        import yaml
        out = {"acts": []}
        for act in acts:
            a = {"act": act["act"], "location": act["name"], "french_scenes": []}
            for i, fs in enumerate(act["fs"], 1):
                a["french_scenes"].append({
                    "fs": f"{act['act']}-{i}", "on_stage": fs["on_stage"],
                    "enters": fs["enters"], "exits": fs["exits"],
                    "duration_min": round(duration(fs), 1),
                    "beat": "",
                })
            out["acts"].append(a)
        open(args.yaml, "w", encoding="utf-8").write(
            yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
        print(f"\nYAML出力: {args.yaml}")


if __name__ == "__main__":
    main()
