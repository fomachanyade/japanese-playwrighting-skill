#!/usr/bin/env python3
"""metrics.py — 台本の文体指紋を計測し、様式プロファイルと比較する

使い方:
    python3 metrics.py script/scene-*.md          # skill記法の台本
    python3 metrics.py --profile 自然主義型 script/

数値は良し悪しではなく様式の座標。error/warning は出さない(設計原則:
構造系はlint、文体系は計測)。プロファイルは実在戯曲のコーパスから校正した参考レンジ。
"""
import argparse
import re
import sys

try:
    import pykakasi
    _KKS = pykakasi.kakasi()
    _RCACHE = {}
    def _reading(t):
        if t not in _RCACHE:
            _RCACHE[t] = "".join(i["hira"] for i in _KKS.convert(t))
        return _RCACHE[t]
except ImportError:
    _KKS = None
from collections import Counter
from pathlib import Path

# コーパス由来の様式プロファイル(校正履歴は開発計画書を参照)
PROFILES = {
    "自然主義型": {   # 例: 髙谷誉『おかえり未来の子』(超短ターン・関西弁・間を記譜)
        "median_len": (8, 16), "pct_under10": (35, 55), "pct_over60": (0, 3),
        "filler_pct": (15, 30), "question_pct": (15, 25), "echo_pct": (4, 10),
        "pause_per_min": (0.3, 1.0), "narration_pct": (0, 1),
        "tsukkomi_pct": (8, 18), "keitai_pct": (0, 8),
        "aizuchi_pct": (4, 10), "overlap_per100": (0, 1), "repeat_pct": (0.2, 0.8), "oto_slide_pct": (1.5, 3.0), "denbun_per1000": (0.8, 2.0),
        "tempo": {"cpm": 320, "turn_sec": 0.7, "pause_sec": 3.0},  # 『おかえり』実測75分で校正
    },
    "明晰会話型": {   # 例: 前川知大『散歩する侵略者』(洗練された口語・疑問駆動・間は委ねる)
        "median_len": (12, 22), "pct_under10": (25, 40), "pct_over60": (4, 12),
        "filler_pct": (3, 10), "question_pct": (25, 40), "echo_pct": (8, 16),
        "pause_per_min": (0.0, 0.15), "narration_pct": (2, 8),
        "tsukkomi_pct": (4, 12), "keitai_pct": (5, 18),
        "aizuchi_pct": (2, 7), "overlap_per100": (0, 1), "repeat_pct": (0.0, 0.5), "oto_slide_pct": (2.0, 3.5), "denbun_per1000": (0.7, 2.0),
        "tempo": {"cpm": 330, "turn_sec": 0.4, "pause_sec": 3.0},  # 未校正(実上演時間の実測待ち)
    },
    "ツッコミ駆動型": { # 例: 蓮見翔『ロマンス』(直前発話へのメタ言及が推進力・敬体は距離計・間は書かない)
        "median_len": (12, 20), "pct_under10": (26, 40), "pct_over60": (2, 8),
        "filler_pct": (14, 26), "question_pct": (14, 24), "echo_pct": (2, 8),
        "pause_per_min": (0.0, 0.1), "narration_pct": (0, 1),
        "tsukkomi_pct": (16, 28), "keitai_pct": (15, 35),
        "aizuchi_pct": (1, 6), "overlap_per100": (0, 1), "repeat_pct": (0.2, 0.8), "oto_slide_pct": (2.5, 4.5), "denbun_per1000": (1.8, 3.8),
        "tempo": {"cpm": 355, "turn_sec": 0.0, "pause_sec": 3.0},  # 『ロマンス』実測約115分で校正。
        # かぶせ気味の応酬はターン交代コストがゼロに近く、発話速度も速い
    },
    "静かな演劇型": {  # 例: 平田オリザ『眠れない夜なんてない』(同時多発・相槌の層・読点終わり)
        "median_len": (7, 12), "pct_under10": (48, 62), "pct_over60": (0, 3),
        "filler_pct": (22, 34), "question_pct": (8, 18), "echo_pct": (0, 4),
        "pause_per_min": (0.8, 1.8), "narration_pct": (0, 1),
        "tsukkomi_pct": (10, 20), "keitai_pct": (10, 20),
        "aizuchi_pct": (14, 24), "overlap_per100": (4, 12), "repeat_pct": (0.7, 1.5), "oto_slide_pct": (0.5, 2.0), "denbun_per1000": (2.2, 4.2),
        "tempo": {"cpm": 380, "turn_sec": 0.0, "pause_sec": 3.0},
        # 弱校正: アーカイブの目安90〜120分の中央値105分から逆算。
        # 相槌とかぶり(同時発話)が時間を消費しないため、シリアル化した実効cpmが最大になる
    },
    "反復喜劇型": {   # 例: 宮藤官九郎『鈍獣』(完全反復の輪唱・カットバック・フィラー最少)
        "median_len": (10, 16), "pct_under10": (38, 52), "pct_over60": (2, 7),
        "filler_pct": (3, 8), "question_pct": (12, 20), "echo_pct": (3, 8),
        "pause_per_min": (0.0, 0.15), "narration_pct": (0, 1),
        "tsukkomi_pct": (4, 12), "keitai_pct": (6, 14),
        "aizuchi_pct": (1, 5), "overlap_per100": (0, 1), "repeat_pct": (1.2, 2.5), "oto_slide_pct": (3.0, 4.5), "denbun_per1000": (0.5, 1.5),
        "tempo": {"cpm": 326, "turn_sec": 0.0, "pause_sec": 3.0},
        # 弱校正: アーカイブ目安2〜3時間の中央値150分から逆算
    },
    "言葉遊び型": {   # 例: 野田秀樹『ザ・キャラクター』(音スライドが転換の蝶番・フィラーゼロ)
        "median_len": (13, 20), "pct_under10": (24, 36), "pct_over60": (4, 9),
        "filler_pct": (0, 5), "question_pct": (18, 28), "echo_pct": (10, 16),
        "pause_per_min": (0.0, 0.1), "narration_pct": (0, 1),
        "tsukkomi_pct": (5, 13), "keitai_pct": (12, 22),
        "aizuchi_pct": (2, 7), "overlap_per100": (0, 1),
        "repeat_pct": (0.2, 0.9), "oto_slide_pct": (5.0, 8.0), "denbun_per1000": (0.4, 1.4),
        "tempo": {"cpm": 292, "turn_sec": 0.0, "pause_sec": 3.0},
        # 弱校正: アーカイブ目安2〜3時間の中央値150分から逆算。cpmが低いのは
        # テキストに現れない身体時間(群舞・変容・転換のシークエンス)が上演を占有するため
    },
    "超口語語り型": {  # 例: 岡田利規『三月の5日間』(代話・話者=俳優・伝聞マーカー最大)
        "median_len": (24, 38), "pct_under10": (20, 34), "pct_over60": (26, 42),
        "filler_pct": (15, 42), "question_pct": (0, 8), "echo_pct": (14, 24),
        "pause_per_min": (0.0, 0.1), "narration_pct": (0, 1),
        "tsukkomi_pct": (6, 14), "keitai_pct": (8, 16),
        "aizuchi_pct": (6, 14), "overlap_per100": (0, 1),
        "repeat_pct": (1.4, 2.6), "oto_slide_pct": (0.0, 1.5), "denbun_per1000": (11.0, 18.0),
        "tempo": {"cpm": 379, "turn_sec": 0.0, "pause_sec": 3.0},
        # 実測校正: 初演80分(約1時間20分)。だらだら話法でも発話速度は速く、
        # 入れ子相槌(並行発話)が時間を圧縮する。冗長さと遅さは独立変数
    },
}
FILLERS = ["あー", "えー", "まあ", "うん", "え、", "なんか", "あの", "いや", "ほら", "その、", "はぁ", "あぁ", "えぇ"]
OVERLAP_RE = re.compile(r"[★☆▲△]")
DENBUN_RE = re.compile(r"っていう|ってゆう|つってい|みたいな|とか思って|とかって|って言って|んですけど|らしくて|とかそういう")
AIZUCHI_RE = re.compile(r"(あ+ぁ?|え+ぇ?|は+い|うん|そう|ふー?ん|へー|お+ぉ?|ん)[、。？…ー]*$")
CPM, TURN_SEC, PAUSE_SEC = 320, 0.7, 3.0


def parse(path):
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    speeches, narr, stage = [], [], []
    for l in text.split("\n"):
        if not l.strip():
            continue
        if l.startswith("\t"):
            stage.append(l.strip())
            continue
        m = re.match(r"^([^\t\s]+)\t(.+)$", l) or re.match(r"^([^\t\s　]{1,8})　(.+)$", l)
        if m:
            (narr if m.group(1).endswith("Ｍ") else speeches).append((m.group(1), m.group(2).strip()))
    return speeches, narr, stage


def count_overlaps(speeches):
    """かぶり記号(★☆▲△)を数え、本文から除去する"""
    n, cleaned = 0, []
    for name, t in speeches:
        n += len(OVERLAP_RE.findall(t))
        cleaned.append((name, OVERLAP_RE.sub("", t)))
    return n, cleaned


def fingerprint(speeches, narr, stage):
    n_overlap, speeches = count_overlaps(speeches)
    lens = sorted(len(t) for _, t in speeches)
    n = len(speeches)
    sp_chars = sum(lens)
    na_chars = sum(len(t) for _, t in narr)
    st_chars = sum(len(t) for t in stage)
    total = sp_chars + na_chars + st_chars
    pauses = sum(t.count("（間）") + t.count("(間)") for _, t in speeches)
    pauses += sum(1 for t in stage if re.match(r"^(少しの)?間([。、].*)?$|^沈黙", t) or re.fullmatch(r"[・]+", t))
    est_min = total / CPM + n * TURN_SEC / 60 + pauses * PAUSE_SEC / 60
    raw = {"total": total, "n": n, "pauses": pauses}
    echo = 0
    for a, b in zip(speeches, speeches[1:]):
        w = set(re.findall(r"[ァ-ヴー]{2,}|[一-龥]{2,}", a[1]))
        if w and any(x in b[1] for x in w):
            echo += 1
    denbun = sum(len(DENBUN_RE.findall(t)) for _, t in speeches)
    oto = None
    if _KKS is not None and n > 1:
        from collections import Counter as _C
        strip = lambda t: re.sub(r"[、。？！…・「」『』（）\s]", "", t)
        surf = [strip(t) for _, t in speeches]
        reads = [_reading(x) for x in surf]
        g5 = lambda x: {x[i:i + 5] for i in range(len(x) - 4)}
        g3 = lambda x: {x[i:i + 3] for i in range(len(x) - 2)}
        freq = _C(g for r in reads for g in g5(r))
        stop = {g for g, c in freq.items() if c > n * 0.005}
        hits = 0
        for i in range(n - 1):
            shared = (g5(reads[i]) & g5(reads[i + 1])) - stop
            if not shared:
                continue
            ss = g3(surf[i]) & g3(surf[i + 1])
            explained = {g for g in shared if any(g in _reading(x) for x in ss)}
            if shared - explained:
                hits += 1
        oto = round(hits / (n - 1) * 100, 1)
    repeat = 0
    for i, (_, t) in enumerate(speeches):
        if len(t) >= 4 and any(t == u for _, u in speeches[i + 1:i + 11]):
            repeat += 1
    tsukkomi = sum(1 for a, b in zip(speeches, speeches[1:])
                   if re.match(r"^(いや、?|え、?|なんで|今の|それ)", b[1]))
    keitai = sum(1 for _, t in speeches if re.search(r"(です|ます|でした|ました)[かよねぇけど]*[、。？…]*$", t))
    aizuchi = sum(1 for _, t in speeches if AIZUCHI_RE.fullmatch(t))
    fp = {
        "発話数": n, "総文字数": total, "推定分数": round(est_min, 1),
        "tsukkomi_pct": round(tsukkomi / (n - 1) * 100) if n > 1 else 0,
        "keitai_pct": round(keitai / n * 100) if n else 0,
        "aizuchi_pct": round(aizuchi / n * 100) if n else 0,
        "overlap_per100": round(n_overlap / n * 100, 1) if n else 0,
        "repeat_pct": round(repeat / n * 100, 1) if n else 0,
        "oto_slide_pct": oto,
        "denbun_per1000": round(denbun / (sp_chars or 1) * 1000, 1),
        "median_len": lens[n // 2] if n else 0,
        "pct_under10": round(sum(1 for x in lens if x <= 10) / n * 100) if n else 0,
        "pct_over60": round(sum(1 for x in lens if x > 60) / n * 100) if n else 0,
        "filler_pct": round(sum(1 for _, t in speeches if any(t.startswith(f) for f in FILLERS)) / n * 100) if n else 0,
        "question_pct": round(sum(1 for _, t in speeches if re.search(r"[？?]$", t)) / n * 100) if n else 0,
        "echo_pct": round(echo / (n - 1) * 100) if n > 1 else 0,
        "pause_per_min": round(pauses / est_min, 2) if est_min else 0,
        "_raw": raw,
        "narration_pct": round(na_chars / total * 100) if total else 0,
    }
    # 話者非対称
    chs, cnt = Counter(), Counter()
    for name, t in speeches:
        chs[name] += len(t)
        cnt[name] += 1
    fp["話者平均字数"] = {k: round(chs[k] / cnt[k]) for k, _ in cnt.most_common(6)}
    return fp


LABELS = {"median_len": "ターン長中央値(字)", "pct_under10": "10字以下(%)", "pct_over60": "60字超(%)",
          "filler_pct": "フィラー始まり(%)", "question_pct": "？終わり(%)", "echo_pct": "エコー率(%)",
          "pause_per_min": "間の記譜(回/分)", "narration_pct": "語りＭ比率(%)",
          "tsukkomi_pct": "ツッコミ受け(%)", "keitai_pct": "敬体終わり(%)",
          "aizuchi_pct": "相槌のみ(%)", "overlap_per100": "かぶり記号(/100発話)",
          "repeat_pct": "完全反復率(%)", "oto_slide_pct": "音スライド(%)",
          "denbun_per1000": "伝聞マーカー(/1000字)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts", nargs="+")
    ap.add_argument("--profile", choices=list(PROFILES), default=None)
    args = ap.parse_args()
    files = []
    for s in args.scripts:
        p = Path(s)
        files.extend(sorted(p.glob("*.md")) if p.is_dir() else [p])
    sp, na, st = [], [], []
    for f in files:
        a, b, c = parse(f)
        sp += a; na += b; st += c
    if not sp:
        sys.exit("せりふ行が見つかりません")
    fp = fingerprint(sp, na, st)
    print(f"発話 {fp['発話数']} / {fp['総文字数']}字(上演時間は様式依存 → 下の推定行を参照)\n")
    profiles = {args.profile: PROFILES[args.profile]} if args.profile else PROFILES
    header = f"{'指標':<16}" + f"{'実測':>8}" + "".join(f"{name:>14}" for name in profiles)
    print(header)
    for key, label in LABELS.items():
        if fp.get(key) is None:
            continue  # 依存ライブラリ不在などで計測不能な軸はスキップ
        row = f"{label:<16}{fp[key]:>8}"
        for name, prof in profiles.items():
            lo, hi = prof[key]
            mark = " ○" if lo <= fp[key] <= hi else "  "
            row += f"{f'{lo}–{hi}{mark}':>14}"
        print(row)
    r = fp["_raw"]
    row = f"{'推定上演時間(分)':<16}{'':>8}"
    for name, prof in profiles.items():
        t = prof["tempo"]
        est = r["total"] / t["cpm"] + r["n"] * t["turn_sec"] / 60 + r["pauses"] * t["pause_sec"] / 60
        row += f"{est:>13.0f} "
    print(row)
    print(f"\n話者平均字数: {fp['話者平均字数']}")
    print("\n※ レンジ外は誤りではなく様式の座標。意図した様式のプロファイルと照らして読むこと。")


if __name__ == "__main__":
    main()
