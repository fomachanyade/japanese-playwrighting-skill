#!/usr/bin/env python3
"""lint_plot.py — 戯曲プロット(structure.yaml)の構造 lint

使い方:
    python3 lint_plot.py path/to/structure.yaml

終了コード: error があれば 1、なければ 0(warning / info のみなら 0)
"""
import sys
from pathlib import Path

import yaml

ERROR, WARNING, INFO = "error", "warning", "info"
BACKBONE_KEYS = ["climax", "opening", "plot_point_1", "plot_point_2"]

# パラダイム骨格点の累積時間の目安(60分基準の比率で保持し、target_minutes に比例させる)
TIMING_RATIO = {
    "plot_point_1": (12 / 60, 17 / 60),
    "midpoint": (27 / 60, 33 / 60),
    "plot_point_2": (40 / 60, 47 / 60),
    "climax": (48 / 60, 58 / 60),
}


class Reporter:
    def __init__(self):
        self.issues = []

    def add(self, level, rule, msg):
        self.issues.append((level, rule, msg))

    def dump(self):
        order = {ERROR: 0, WARNING: 1, INFO: 2}
        icon = {ERROR: "✖", WARNING: "▲", INFO: "ℹ"}
        for level, rule, msg in sorted(self.issues, key=lambda x: order[x[0]]):
            print(f"{icon[level]} [{level}] {rule}: {msg}")
        counts = {lv: sum(1 for i in self.issues if i[0] == lv) for lv in order}
        print(f"\n--- error: {counts[ERROR]} / warning: {counts[WARNING]} / info: {counts[INFO]} ---")
        return counts[ERROR] == 0


def flatten_fs(acts):
    """全FSを (act_dict, fs_dict, act内index) の上演順リストにする"""
    out = []
    for act in acts:
        for i, fs in enumerate(act.get("french_scenes") or []):
            out.append((act, fs, i))
    return out


def lint(data, rep):
    acts = data.get("acts") or []
    paradigm = data.get("paradigm") or {}
    target = data.get("target_minutes", 60)
    all_fs = flatten_fs(acts)
    fs_ids = [fs.get("fs") for _, fs, _ in all_fs]

    # --- 執筆順(シド・フィールド式)のガード ---
    climax = paradigm.get("climax") or {}
    if not climax.get("decided") and all_fs:
        rep.add(ERROR, "field-order",
                "クライマックスが未確定(climax.decided: false)のままFSカードが作られています。"
                "まずエンディング/クライマックスを決めてください")
    undecided = [k for k in BACKBONE_KEYS if not (paradigm.get(k) or {}).get("decided")]
    if undecided and len(all_fs) >= 5:
        rep.add(WARNING, "field-order",
                f"骨格点 {', '.join(undecided)} が未確定のままカードが {len(all_fs)} 枚あります。"
                "骨格4点(climax / opening / PP1 / PP2)を先に固めることを推奨")

    # --- 景の数と場転 ---
    zones = data.get("stage_zones") or []
    if zones:
        # 常設多重セット: 物理的な建て込みはゾーン数で数え、見出し数は無制限(切替コストゼロ)
        bad = {a.get("location") for a in acts} - set(zones) - {"回想"}
        if bad:
            rep.add(ERROR, "zone-ref", f"stage_zones に無い場所が使われています: {sorted(bad)}")
        if len(zones) >= 6:
            rep.add(WARNING, "zone-count", f"常設ゾーンが {len(zones)} あります。舞台上に同時に建て込めますか?")
    elif len(acts) >= 10:
        rep.add(ERROR, "act-count", f"景が {len(acts)} あります(10以上は禁止)")
    elif len(acts) >= 4:
        rep.add(WARNING, "act-count", f"景が {len(acts)} あります。場転を減らせないか検討してください")
    for prev, cur in zip(acts, acts[1:]):
        if prev.get("location") == cur.get("location") and cur.get("transition") not in ("暗転", "明転のまま"):
            rep.add(INFO, "act-transition",
                    f"景{prev.get('act')}→景{cur.get('act')} は同一の場所です。"
                    "場転ではなく暗転/照明転換で処理できるかもしれません"
                    f"(transition: {cur.get('transition')!r})")

    # --- FS数の目安 ---
    if all_fs and not (8 <= len(all_fs) <= 12):
        rep.add(INFO, "fs-count", f"FSが {len(all_fs)} 個です(目安: 8〜12。カード1枚=FS1個)")

    # --- FS ID の重複 ---
    dupes = {i for i in fs_ids if fs_ids.count(i) > 1}
    if dupes:
        rep.add(ERROR, "fs-id", f"FS ID が重複しています: {sorted(dupes)}")

    # --- 出入り簿の検算(景内) ---
    for act in acts:
        scenes = act.get("french_scenes") or []
        for i, fs in enumerate(scenes):
            on = set(fs.get("on_stage") or [])
            ent = set(fs.get("enters") or [])
            ext = set(fs.get("exits") or [])
            if i == 0:
                if not ent <= on:
                    rep.add(ERROR, "stage-ledger",
                            f"FS {fs.get('fs')}: enters {sorted(ent - on)} が on_stage に含まれていません")
                continue
            if fs.get("flashback") or scenes[i - 1].get("flashback"):
                continue  # 回想の出入りは通常の連続性から除外
            prev = set(scenes[i - 1].get("on_stage") or [])
            expected = (prev | ent) - ext
            if expected != on:
                rep.add(ERROR, "stage-ledger",
                        f"FS {fs.get('fs')}: 出入り簿が合いません。"
                        f"前FS {sorted(prev)} + enters {sorted(ent)} - exits {sorted(ext)}"
                        f" = {sorted(expected)} のはずが on_stage は {sorted(on)}")
            if not ent and not ext and not fs.get("time_jump") and not fs.get("flashback"):
                rep.add(ERROR, "fs-boundary",
                        f"FS {fs.get('fs')}: enters/exits が両方空です。"
                        "人物の出入りなしにFSは区切れません(時間経過なら time_jump: true を付ける)")

    # --- 骨格FSの goal/conflict/outcome 必須 ---
    backbone_fs = {}
    for key in BACKBONE_KEYS + ["midpoint"]:
        node = paradigm.get(key) or {}
        if node.get("fs"):
            backbone_fs[node["fs"]] = key
    for _, fs, _ in all_fs:
        key = backbone_fs.get(fs.get("fs"))
        if key:
            missing = [f for f in ("goal", "conflict", "outcome") if not fs.get(f)]
            if missing:
                rep.add(ERROR, "backbone-detail",
                        f"FS {fs.get('fs')}({key}): 骨格点を含むFSには {', '.join(missing)} が必須です")
    for fsid, key in backbone_fs.items():
        if fsid not in fs_ids:
            rep.add(ERROR, "backbone-ref", f"paradigm.{key} が参照する FS {fsid!r} が存在しません")

    # --- 時間予算(開場 preshow: true の景は本編の予算から除外) ---
    durations = [fs.get("duration_min") or 0 for act, fs, _ in all_fs if not act.get("preshow")]
    total = sum(durations)
    if all_fs and not (target - 5 <= total <= target):
        rep.add(WARNING, "duration-total",
                f"合計 {total} 分です(目標: {target - 5}〜{target} 分)")
    cumulative = {}
    acc = 0
    for act, fs, _ in all_fs:
        if act.get("preshow"):
            continue
        acc += fs.get("duration_min") or 0
        cumulative[fs.get("fs")] = acc
    for key, (lo_r, hi_r) in TIMING_RATIO.items():
        node = paradigm.get(key) or {}
        fsid = node.get("fs")
        if fsid and fsid in cumulative:
            lo, hi = lo_r * target, hi_r * target
            pos = cumulative[fsid]
            if not (lo <= pos <= hi):
                rep.add(WARNING, "paradigm-timing",
                        f"{key}(FS {fsid})の累積時間が {pos} 分です(目安: {lo:.0f}〜{hi:.0f} 分)")

    # --- 伏線(setup / payoff) ---
    setups, payoffs = {}, {}
    for idx, (_, fs, _) in enumerate(all_fs):
        for tag in fs.get("setup") or []:
            setups.setdefault(tag, idx)
        for tag in fs.get("payoff") or []:
            payoffs.setdefault(tag, idx)
    for tag, idx in setups.items():
        if tag not in payoffs:
            rep.add(WARNING, "chekhov", f"setup {tag!r}(FS {all_fs[idx][1].get('fs')})が回収されていません")
    for tag, idx in payoffs.items():
        if tag not in setups:
            rep.add(ERROR, "chekhov", f"payoff {tag!r}(FS {all_fs[idx][1].get('fs')})に対応する setup がありません")
        elif setups[tag] >= idx:
            rep.add(ERROR, "chekhov", f"{tag!r} の payoff が setup と同時か先行しています")

    # --- 人物の組み合わせが動いていない ---
    combos = {}
    for _, fs, _ in all_fs:
        for name in fs.get("on_stage") or []:
            others = frozenset(set(fs.get("on_stage") or []) - {name})
            combos.setdefault(name, set()).add(others)
    for name, cs in combos.items():
        appearances = sum(1 for _, fs, _ in all_fs if name in (fs.get("on_stage") or []))
        if appearances >= 2 and len(cs) == 1:
            rep.add(INFO, "relationship-static",
                    f"{name} は全編を通して同じ顔ぶれとしか舞台に立ちません。関係性が動いていない可能性")

    # --- 未登場の登場人物 ---
    declared = set(data.get("characters") or []) | set(data.get("voices") or [])
    seen = {n for _, fs, _ in all_fs for n in (fs.get("on_stage") or [])}
    for name in seen - declared:
        rep.add(WARNING, "character-undeclared", f"{name} が characters に宣言されていません")
    for name in declared - seen:
        rep.add(INFO, "character-unused", f"{name} はどのFSにも登場しません")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    path = Path(sys.argv[1])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rep = Reporter()
    lint(data, rep)
    ok = rep.dump()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
