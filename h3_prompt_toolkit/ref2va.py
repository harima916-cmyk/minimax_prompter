# -*- coding: utf-8 -*-
"""Ref2VA 出力テキストのパース基盤。[C] 差し替えと [D] 検証の両方が使う。

MiniMax-H3 の Ref2VA プロンプトは 6 フィールド構成:
    subject_definitions / summary / retention_analysis /
    detailed_description / overall_soundscape / non_diegetic_music

時刻は M:SS.mmm (ミリ秒 3 桁固定)、台詞は <d>[Language] ...</d>、
ショットは [Shot N] (2 つ目以降は "At M:SS.mmm," 付き)。
ラベルの崩れ (太字・見出し記号など) は寛容に受ける。
参照実装: pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI の fields.py / checks.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass


REF_OUTPUT_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

# 差し替えで N/A に強制するフィールド (音声 latent が凍結されているので
# 出力音声に効かず、映像側の条件付けを濁らせるだけ)
NA_FIELDS = ("overall_soundscape", "non_diegetic_music")


# ---------------------------------------------------------------------------
# 包み剥がし (LLM が付ける余計な外側)
# ---------------------------------------------------------------------------

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

_FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)\n[ \t]*```[ \t]*$", re.DOTALL)
_FENCE_ANY = re.compile(r"^[ \t]*```[^\n]*\n(.*)", re.DOTALL)


def strip_wrappers(text: str) -> str:
    """LLM 出力から、プロンプト本体でない外側を取り除く。

    Qwen 系はこの 3 つを付けてくることがある:
      - <think> ... </think> の推論ブロック (閉じずに切れることもある)
      - ``` で囲んだコードフェンス
      - 「Assumption:」等の前置き行 (最初のフィールド見出しより前)

    仕様書は「プロンプトのみを返せ」と指示しているが、守られなかった分は
    機械で剥がす。剥がせるものだけ剥がし、判断が要るものは残して [D] に
    報告させる。
    """
    s = text or ""

    # <think> ブロック: 閉じていれば最後の </think> まで、閉じていなければ
    # (＝途中で切れた出力) 開き以降を丸ごと捨てる
    if THINK_CLOSE in s:
        s = s.rsplit(THINK_CLOSE, 1)[-1]
    elif THINK_OPEN in s:
        s = s.split(THINK_OPEN, 1)[0]
    s = s.strip()

    # コードフェンス: 全体を囲っている形を優先、閉じ忘れも拾う
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    else:
        m = _FENCE_ANY.match(s)
        if m:
            s = m.group(1)
            if "```" in s:
                s = s.rsplit("```", 1)[0]
            s = s.strip()

    # 前置き: 最初のフィールド見出しより前を落とす (見出しが無ければ触らない)
    matches = list(_label_pattern(REF_OUTPUT_FIELDS).finditer(s))
    if matches and matches[0].start() > 0:
        s = s[matches[0].start():].strip()
    return s


def has_wrappers(text: str) -> bool:
    """剥がすべき包みが残っているか ([D] 用)。"""
    s = text or ""
    return (THINK_OPEN in s or THINK_CLOSE in s
            or bool(_FENCE_ANY.match(s.strip())))


# ---------------------------------------------------------------------------
# フィールド分割
# ---------------------------------------------------------------------------

@dataclass
class Section:
    name: str
    label_start: int   # ラベル行の開始位置 (装飾込み)
    body_start: int    # ラベルとコロンの直後
    body_end: int      # 次のラベルの手前 (末尾なら len(text))

    def body(self, text: str) -> str:
        return text[self.body_start:self.body_end].strip()


_LABEL_PATTERNS: dict[tuple, re.Pattern] = {}


def _label_pattern(names: tuple) -> re.Pattern:
    """ラベル行のマッチャ。太字・見出し・引用・箇条書きの装飾を許す。"""
    cached = _LABEL_PATTERNS.get(names)
    if cached is None:
        cached = re.compile(
            r"^[ \t]*[*_#>\-\s]*(" + "|".join(re.escape(n) for n in names) + r")[*_ \t]*[:：][ \t]*",
            re.IGNORECASE | re.MULTILINE,
        )
        _LABEL_PATTERNS[names] = cached
    return cached


def find_sections(text: str, names: tuple = REF_OUTPUT_FIELDS):
    """(先頭のラベル前テキスト, {フィールド名: Section}) を返す。

    同名ラベルが複数あれば最初のものを採用する (2 つ目以降は本文の一部)。
    """
    sections: dict[str, Section] = {}
    matches = list(_label_pattern(names).finditer(text or ""))
    head = (text[: matches[0].start()] if matches else (text or "")).strip()
    for i, m in enumerate(matches):
        name = m.group(1).lower()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if name not in sections:
            sections[name] = Section(name, m.start(), m.end(), end)
    return head, sections


def section_bodies(text: str, names: tuple = REF_OUTPUT_FIELDS) -> dict:
    """{フィールド名: 本文 (無ければ "")}。"""
    _, secs = find_sections(text, names)
    return {n: (secs[n].body(text) if n in secs else "") for n in names}


# ---------------------------------------------------------------------------
# タイムスタンプ
# ---------------------------------------------------------------------------

# 緩いトークン検出 ("16:9" のような比率は秒 2 桁必須ではじく)
TS_TOKEN = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2}(?:\.\d{1,3})?)(?![\d:])")
# 厳密書式 M:SS.mmm (ミリ秒 3 桁固定。分は 1〜2 桁を許す)
TS_STRICT = re.compile(r"^\d{1,2}:[0-5]\d\.\d{3}$")

_SHOT_BEFORE = re.compile(r"\[Shot\s+(\d+)\]\s*[,，]?\s*[Aa]t\s+$")
_AT_BEFORE = re.compile(r"\b[Aa]t\s+$")

SHOT_MARK = re.compile(r"\[Shot\s+(\d+)\]")
DIALOGUE = re.compile(r"<d>(.*?)</d>", re.DOTALL)
LANG_PREFIX = re.compile(r"^\s*\[([^\[\]\n]{1,32})\][ \t]*")
SPEAKER_TOKEN = re.compile(r"\bS(\d+)\b")
REF_TAG = re.compile(r"<(Picture|Video|Audio)\s+(\d+)>")


def ts_value(minutes: str, rest: str) -> float:
    return int(minutes) * 60 + float(rest)


def _context(text: str, start: int, end: int, span=36) -> str:
    lo = max(0, start - span)
    hi = min(len(text), end + span)
    snippet = text[lo:hi].replace("\n", " ")
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


@dataclass
class TsOcc:
    start: int          # 全文中のトークン開始位置
    end: int
    raw: str            # マッチした時刻文字列そのもの
    sec: float
    kind: str           # "shot" | "at" | "bare"
    shot: int | None    # kind == "shot" のときのショット番号
    context: str


@dataclass
class DOcc:
    start: int          # <d> の開始位置
    end: int            # </d> の直後
    inner_start: int
    inner_end: int
    inner: str          # タグの中身そのもの
    lang: str | None    # [Language] タグ。無ければ None
    spoken: str         # 言語タグを除いた台詞部分
    speaker: str | None # 直前にある話者 ID (S1 など)。見つからなければ None
    context: str


def dialogue_occurrences(text: str, base: int = 0, search_from: int = 0):
    """<d>...</d> を出現順に列挙する。base は全文中のオフセット。

    speaker は「前の <d> の終わり (無ければ search_from) から
    この <d> まで」の間にある最後の S\\d+ トークン。
    """
    out = []
    prev_end = search_from
    for m in DIALOGUE.finditer(text):
        inner = m.group(1)
        lm = LANG_PREFIX.match(inner)
        lang = lm.group(1) if lm else None
        spoken = inner[lm.end():] if lm else inner
        window = text[prev_end:m.start()]
        speakers = SPEAKER_TOKEN.findall(window)
        speaker = f"S{speakers[-1]}" if speakers else None
        out.append(DOcc(
            start=base + m.start(),
            end=base + m.end(),
            inner_start=base + m.start(1),
            inner_end=base + m.end(1),
            inner=inner,
            lang=lang,
            spoken=spoken.strip(),
            speaker=speaker,
            context=_context(text, m.start(), m.end()),
        ))
        prev_end = m.end()
    return out


def _dialogue_spans(text: str):
    return [(m.start(), m.end()) for m in DIALOGUE.finditer(text)]


def ts_occurrences(text: str, base: int = 0, exclude_dialogue: bool = True):
    """時刻トークンを出現順に列挙する。<d> の中身は既定で対象外。"""
    spans = _dialogue_spans(text) if exclude_dialogue else []
    out = []
    for m in TS_TOKEN.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        before = text[max(0, m.start() - 48):m.start()]
        shot_m = _SHOT_BEFORE.search(before)
        if shot_m:
            kind, shot = "shot", int(shot_m.group(1))
        elif _AT_BEFORE.search(before):
            kind, shot = "at", None
        else:
            kind, shot = "bare", None
        out.append(TsOcc(
            start=base + m.start(),
            end=base + m.end(),
            raw=m.group(0),
            sec=ts_value(m.group(1), m.group(2)),
            kind=kind,
            shot=shot,
            context=_context(text, m.start(), m.end()),
        ))
    return out


@dataclass
class ShotMark:
    number: int
    start: int
    end: int
    ts: TsOcc | None    # 直後に "At M:SS.mmm" があればその時刻


_AFTER_SHOT = re.compile(r"\s*[,，]?\s*[Aa]t\s+(\d{1,2}):(\d{2}(?:\.\d{1,3})?)")


def shot_marks(text: str, base: int = 0):
    """[Shot N] マークを出現順に列挙し、直後の切り替え時刻を紐付ける。

    時刻はマークの直後に "At M:SS.mmm" が続く場合だけ紐付ける
    (離れた位置の時刻を拾って別ショットに割り当てないため)。
    """
    out = []
    for m in SHOT_MARK.finditer(text):
        ts = None
        am = _AFTER_SHOT.match(text, m.end())
        if am:
            raw = text[am.start(1):am.end(2)]
            ts = TsOcc(
                start=base + am.start(1),
                end=base + am.end(2),
                raw=raw,
                sec=ts_value(am.group(1), am.group(2)),
                kind="shot",
                shot=int(m.group(1)),
                context=_context(text, m.start(), am.end(2)),
            )
        out.append(ShotMark(int(m.group(1)), base + m.start(), base + m.end(), ts))
    return out


def ref_tags(text: str) -> dict:
    """{"Picture": {1, 2}, "Audio": {1}, "Video": set()} 形式で列挙する。"""
    out = {"Picture": set(), "Video": set(), "Audio": set()}
    for m in REF_TAG.finditer(text or ""):
        out[m.group(1)].add(int(m.group(2)))
    return out
