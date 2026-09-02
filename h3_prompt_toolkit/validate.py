# -*- coding: utf-8 -*-
"""[D] 検証パス。最終プロンプトを機械的にチェックする。

仕様書の検証表 (フィールド / 時刻書式 / 台詞の同一性 / 単調性 / 尺 /
話者 ID / 参照タグ) に、ショット構造・soundscape の N/A・実測時刻との
一致を加えたもの。モデル選定の判定にもそのまま使う。

検出は報告するだけで、直すのは [C] 差し替えパスの仕事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .timeline import Timeline, fmt_ts
from . import ref2va

ERROR = "error"
WARN = "warn"
INFO = "info"

_LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}

# (check id, 表示名) — compare の表の行順でもある
CHECKS = (
    ("fields", "フィールド"),
    ("wrappers", "出力の包み"),
    ("task_type", "タスク種別"),
    ("audio_marker", "音声マーカー"),
    ("ts_format", "時刻書式"),
    ("dialogue", "台詞の同一性"),
    ("monotonic", "単調性"),
    ("duration", "尺"),
    ("speakers", "話者ID"),
    ("ref_tags", "参照タグ"),
    ("shots", "ショット構造"),
    ("soundscape", "音響欄 N/A"),
    ("ts_match", "実測との一致"),
    ("resolution", "解像度非依存"),
    ("anchor", "アンカー整合"),
)

# [Shot 1] が <Picture 1> から始まると書いてある句
ANCHOR_PHRASE = re.compile(
    r"\b(?:begins? from|starts? from|opens? on|opens? from)\b", re.IGNORECASE)
ANCHOR_LOOKAHEAD = 200

_EPS = 0.0005

# 台詞の「軽微な改変」判定で無視する文字 (長音「ー」は意味を持つため残す)
_PUNCT = re.compile(r"[\s、。，．,\.!?！？…‥・「」『』“”\"'（）()〈〉《》:：;；]+")

# 解像度や工程に依存する語。ワークフロー v2 は同じプロンプトを 0.9MP の
# 精修パスで再利用するので、特定の解像度や「下書き」を指す語は書かせない。
RESOLUTION_WORDS = re.compile(
    r"\b(?:\d{3,4}p|\d{3,4}\s*[x×]\s*\d{3,4}|\d(?:\.\d+)?\s*MP|megapixels?|"
    r"low[- ]res(?:olution)?|high[- ]res(?:olution)?|upscal\w*|"
    r"draft|rough pass|refinement pass|first pass|second pass)\b",
    re.IGNORECASE)


@dataclass
class Finding:
    check: str
    level: str
    message: str


def _norm_dialogue(s: str) -> str:
    return _PUNCT.sub("", s or "")


def validate(text: str, tl: Timeline | None = None, expect_na: bool = True):
    """text を検査して Finding のリストを返す (深刻な順)。

    tl が無ければタイムライン照合系のチェックは省略される。
    expect_na=False にすると soundscape/music の N/A 要求を情報扱いに落とす。
    """
    f: list[Finding] = []
    if not (text or "").strip():
        return [Finding("fields", ERROR, "テキストが空です")]

    head, sections = ref2va.find_sections(text)
    bodies = {n: (sections[n].body(text) if n in sections else "")
              for n in ref2va.REF_OUTPUT_FIELDS}

    # -- フィールド ---------------------------------------------------------
    absent = [n for n in ref2va.REF_OUTPUT_FIELDS if not bodies[n]]
    if absent:
        f.append(Finding("fields", ERROR,
                         f"欠落フィールド: {', '.join(absent)}"))

    # -- 出力の包み (剥がし忘れ) --------------------------------------------
    if ref2va.has_wrappers(text):
        f.append(Finding("wrappers", ERROR,
                         "<think> ブロックかコードフェンスが残っています "
                         "(差し替え [C] を通すと自動で取り除かれます)"))
    if head:
        shown = head if len(head) <= 60 else head[:60] + "…"
        f.append(Finding("wrappers", WARN,
                         f"最初のフィールドより前に文章があります: 「{shown}」"))

    # -- タスク種別 / 音声マーカー (強制音声モードの FIXED) ------------------
    summary = bodies["summary"]
    if summary:
        if "audio reuse" not in summary.lower():
            f.append(Finding("task_type", ERROR,
                             "summary のタスク種別に audio reuse がありません "
                             "(強制音声は音声信号をそのまま使う)"))
        if not re.search(r"\[[^\]\n]+\]", summary):
            f.append(Finding("task_type", ERROR,
                             "summary が [タスク種別] の角括弧で始まっていません"))

    retention = bodies["retention_analysis"]
    if retention:
        audio_line = None
        for line in retention.splitlines():
            if re.search(r"<Audio\s+1>", line):
                audio_line = line
                break
        if audio_line is None:
            f.append(Finding("audio_marker", ERROR,
                             "retention_analysis に <Audio 1> の行がありません"))
        elif "fully_copy" not in audio_line:
            f.append(Finding("audio_marker", ERROR,
                             "<Audio 1> のマーカーが fully_copy ではありません: "
                             f"「{audio_line.strip()[:60]}」"))

    dd = sections.get("detailed_description")
    dd_body = text[dd.body_start:dd.body_end] if dd else ""
    dd_base = dd.body_start if dd else 0

    all_occs = ref2va.ts_occurrences(text)          # 全文 (<d> 内は除外)
    occs = ref2va.ts_occurrences(dd_body, base=dd_base) if dd else []
    at_occs = [o for o in occs if o.kind == "at"]
    d_occs = ref2va.dialogue_occurrences(dd_body, base=dd_base) if dd else []

    # -- 時刻書式 -----------------------------------------------------------
    opened, closed = text.count("<d>"), text.count("</d>")
    if opened != closed:
        f.append(Finding("ts_format", ERROR,
                         f"<d> タグが不均衡です ({opened} 開 / {closed} 閉)"))
    for o in all_occs:
        if not ref2va.TS_STRICT.match(o.raw):
            f.append(Finding("ts_format", ERROR,
                             f"時刻書式が M:SS.mmm でない: \"{o.raw}\" … {o.context}"))

    # -- 単調性 (detailed_description 内の出現順) ---------------------------
    prev = None
    for o in occs:
        if prev is not None and o.sec < prev.sec - _EPS:
            f.append(Finding("monotonic", ERROR,
                             f"時刻が逆行: {prev.raw} の後に {o.raw} … {o.context}"))
        prev = o

    # -- 尺 ----------------------------------------------------------------
    if tl is not None and tl.total_sec > 0:
        for o in occs:
            if o.sec > tl.total_sec + _EPS:
                f.append(Finding("duration", ERROR,
                                 f"動画長 {fmt_ts(tl.total_sec)} を超過: {o.raw} … {o.context}"))

    # -- 台詞の同一性 / 話者 ID --------------------------------------------
    if tl is not None:
        utts = tl.usable_utterances()
        if len(d_occs) != len(utts):
            f.append(Finding("dialogue", ERROR,
                             f"<d> ブロックが {len(d_occs)} 個 (入力の発話は {len(utts)} 個)"))
        else:
            for i, (o, u) in enumerate(zip(d_occs, utts), 1):
                if o.lang is None:
                    f.append(Finding("dialogue", ERROR,
                                     f"発話({i}): [言語] タグがありません"))
                elif o.lang.lower() != u.lang.lower():
                    f.append(Finding("dialogue", WARN,
                                     f"発話({i}): 言語タグ [{o.lang}] ≠ 入力 [{u.lang}]"))
                if o.spoken == u.text:
                    pass
                elif _norm_dialogue(o.spoken) == _norm_dialogue(u.text):
                    f.append(Finding("dialogue", WARN,
                                     f"発話({i}): 句読点・空白の改変\n"
                                     f"      入力: {u.text}\n      出力: {o.spoken}"))
                else:
                    f.append(Finding("dialogue", ERROR,
                                     f"発話({i}): 台詞が入力と不一致 (翻訳・要約・言い換えの疑い)\n"
                                     f"      入力: {u.text}\n      出力: {o.spoken}"))
                if o.speaker is None:
                    f.append(Finding("speakers", WARN,
                                     f"発話({i}): <d> の直前に話者 ID が見つかりません"))
                elif o.speaker != u.speaker:
                    f.append(Finding("speakers", ERROR,
                                     f"発話({i}): 話者 {o.speaker} ≠ 入力 {u.speaker}"))
        used = set(re.findall(r"\bS\d+\b", ref2va.DIALOGUE.sub(" ", dd_body)))
        expected = {u.speaker for u in utts}
        extra = sorted(used - expected)
        if extra:
            f.append(Finding("speakers", WARN,
                             f"入力に無い話者 ID が出現: {', '.join(extra)}"))

    # -- 参照タグ -----------------------------------------------------------
    tags = ref2va.ref_tags(text)
    if tl is not None:
        n_img = tl.n_images
        for n in sorted(tags["Picture"]):
            if n > n_img:
                f.append(Finding("ref_tags", ERROR,
                                 f"<Picture {n}> が引用されていますが接続は {n_img} 枚です"))
        for n in range(1, n_img + 1):
            if n not in tags["Picture"]:
                f.append(Finding("ref_tags", WARN,
                                 f"<Picture {n}> が一度も引用されていません"))
        for n in sorted(tags["Audio"]):
            if n > 1:
                f.append(Finding("ref_tags", ERROR,
                                 f"<Audio {n}> が引用されていますが接続は 1 本です"))
        if 1 not in tags["Audio"]:
            f.append(Finding("ref_tags", WARN,
                             "<Audio 1> (強制音声) が一度も引用されていません"))
        for n in sorted(tags["Video"]):
            f.append(Finding("ref_tags", WARN,
                             f"<Video {n}> が引用されていますが Video 参照は接続されていません"))
    else:
        # タイムラインなしでは Ref2VA の上限だけを見る
        caps = {"Picture": 9, "Video": 3, "Audio": 3}
        for kind, cap in caps.items():
            for n in sorted(tags[kind]):
                if n > cap:
                    f.append(Finding("ref_tags", WARN,
                                     f"<{kind} {n}> は Ref2VA の上限 {cap} を超えています"))

    # -- ショット構造 -------------------------------------------------------
    if dd:
        marks = ref2va.shot_marks(dd_body, base=dd_base)
        if not any(m.number == 1 for m in marks):
            f.append(Finding("shots", WARN,
                             "[Shot 1] がありません (H3 はショットで構造を読む)"))
        highest = 0
        for m in marks:
            if m.number <= highest:
                continue  # 小さい番号の再出現は文中の言及とみなす
            if m.number > highest + 1:
                f.append(Finding("shots", WARN,
                                 f"ショット番号が {highest} から {m.number} へ飛んでいます"))
            if m.number == 1 and m.ts is not None:
                f.append(Finding("shots", WARN,
                                 "[Shot 1] に切り替え時刻が付いています (先頭ショットは時刻なし)"))
            if m.number >= 2 and m.ts is None:
                f.append(Finding("shots", WARN,
                                 f"[Shot {m.number}] に \"At M:SS.mmm\" の切り替え時刻がありません"))
            highest = m.number

    # -- overall_soundscape / non_diegetic_music ----------------------------
    for name in ref2va.NA_FIELDS:
        body = bodies[name]
        if body and body != "N/A":
            shown = body if len(body) <= 40 else body[:40] + "…"
            level = ERROR if expect_na else INFO
            f.append(Finding("soundscape", level,
                             f"{name} が N/A ではありません: 「{shown}」"))

    # -- First-frame anchor (MiniMaxH3AddGuide) との整合 ----------------------
    if tl is not None:
        anchor = bool((tl.ref_texts or {}).get("anchor", False))
        has_keyframe = "keyframe completion" in (summary or "").lower()
        if anchor:
            if not has_keyframe:
                f.append(Finding("anchor", ERROR,
                                 "anchor ON なのに summary のタスク種別に "
                                 "keyframe completion がありません"))
            pic_line = None
            for line in (retention or "").splitlines():
                if re.match(r"\s*<Picture\s+1>", line):
                    pic_line = line
                    break
            if pic_line is None:
                f.append(Finding("anchor", ERROR,
                                 "anchor ON なのに retention_analysis に "
                                 "<Picture 1> の独立行がありません"))
            elif "fully_preserved" not in pic_line:
                f.append(Finding("anchor", ERROR,
                                 "anchor ON の <Picture 1> が fully_preserved "
                                 f"ではありません: 「{pic_line.strip()[:60]}」"))
            if dd_body:
                i = dd_body.find("[Shot 1]")
                window = dd_body[i:i + ANCHOR_LOOKAHEAD] if i >= 0 else ""
                if not ANCHOR_PHRASE.search(window):
                    f.append(Finding("anchor", WARN,
                                     "[Shot 1] の冒頭に <Picture 1> から始まると"
                                     "書かれていません (begins from / opens on)"))
        elif has_keyframe:
            f.append(Finding("anchor", WARN,
                             "anchor OFF なのに keyframe completion が使われて"
                             "います (ワークフローの AddGuide 設定と食い違い)"))

    # -- 解像度非依存 (精修パスで同じプロンプトを使い回すため) ----------------
    if dd_body:
        for m in RESOLUTION_WORDS.finditer(dd_body):
            f.append(Finding("resolution", WARN,
                             f"解像度・工程に依存する語: 「{m.group(0)}」 — "
                             "同じプロンプトを精修パスで使い回せなくなります"))

    # -- 実測タイムスタンプとの一致 -----------------------------------------
    if tl is not None:
        utts = tl.usable_utterances()
        if len(at_occs) != len(utts):
            f.append(Finding("ts_match", WARN,
                             f"\"At M:SS.mmm\" が {len(at_occs)} 個 (発話は {len(utts)} 個)"))
        else:
            for i, (o, u) in enumerate(zip(at_occs, utts), 1):
                if abs(o.sec - u.start) > _EPS:
                    f.append(Finding("ts_match", WARN,
                                     f"発話({i}): 出力 {o.raw} ≠ 実測 {fmt_ts(u.start)} "
                                     f"(差し替えで修正可能)"))

    f.sort(key=lambda x: (_LEVEL_ORDER[x.level],
                          [c for c, _ in CHECKS].index(x.check)))
    return f


def summarize(findings) -> dict:
    """チェック項目ごとの最悪レベル。問題なしは "ok"。"""
    out = {check: "ok" for check, _ in CHECKS}
    for x in findings:
        cur = out.get(x.check, "ok")
        if cur == "ok" or _LEVEL_ORDER[x.level] < _LEVEL_ORDER.get(cur, 3):
            out[x.check] = x.level
    return out


def counts(findings) -> tuple:
    e = sum(1 for x in findings if x.level == ERROR)
    w = sum(1 for x in findings if x.level == WARN)
    i = len(findings) - e - w
    return e, w, i


def render_report(findings) -> str:
    if not findings:
        return "問題は見つかりませんでした。"
    e, w, i = counts(findings)
    mark = {ERROR: "✗", WARN: "⚠", INFO: "・"}
    lines = [f"エラー {e} / 警告 {w} / 情報 {i}"]
    labels = dict(CHECKS)
    for x in findings:
        lines.append(f"{mark[x.level]} [{labels.get(x.check, x.check)}] {x.message}")
    return "\n".join(lines)
