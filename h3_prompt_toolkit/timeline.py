# -*- coding: utf-8 -*-
"""Utterance / Timeline データ構造と、台詞テキスト・時刻のパース。

fmt_ts / parse_lines は h3_audio_prompter.py から移植 (ロジック変更禁止)。
データ構造は h3_prompt_toolkit_spec.md の定義に従う。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict


DEFAULT_LANG = "Japanese"


def fmt_ts(sec):
    """MM:SS.mmm 形式。公式ガイドと ComfyUI テンプレートの例に合わせる。

    H3 のテキストエンコーダは書式をトークンとして読むので、学習分布に
    寄せて分を 2 桁ゼロ埋めする (0:03.500 ではなく 00:03.500)。
    読み取り側 (ref2va.TS_STRICT) は 1 桁の分も引き続き受ける。
    """
    if sec < 0:
        sec = 0.0
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:06.3f}"


_TS = re.compile(r"^\s*(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\s*$")


def parse_ts(text):
    """'M:SS.mmm' (多少崩れた形も含む) を秒に。解釈できなければ None。"""
    m = _TS.match(text or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + float(m.group(2))


def parse_lines(text):
    """1 行 1 発話。'S2: 台詞' で話者指定、省略時は S1。"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\(?(S\d+)\)?\s*[:：]\s*(.+)$", line, re.IGNORECASE)
        if m:
            out.append((m.group(1).upper(), m.group(2).strip()))
        else:
            out.append(("S1", line))
    return out


@dataclass
class Utterance:
    index: int
    start: float | None   # 秒。未検出は None
    end: float | None
    speaker: str          # "S1"
    text: str             # 台詞そのもの
    lang: str             # "Japanese"

    def usable(self) -> bool:
        """差し替え・骨組みの対象になるか (区間と台詞が揃っているか)。"""
        return self.start is not None and bool(self.text)


@dataclass
class Timeline:
    utterances: list[Utterance] = field(default_factory=list)
    total_sec: float = 0.0    # グリッドにスナップ済み
    frames: int = 0
    wav_path: str = ""
    n_images: int = 2
    # 参照の説明 (LLM 貼り付けの先頭に入れる)。{"pictures": [...], "audio": str}
    ref_texts: dict = field(default_factory=dict)

    def usable_utterances(self) -> list[Utterance]:
        return [u for u in self.utterances if u.usable()]

    # -- 直列化 -----------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        utts = [Utterance(**u) for u in d.get("utterances", [])]
        return cls(
            utterances=utts,
            total_sec=float(d.get("total_sec", 0.0)),
            frames=int(d.get("frames", 0)),
            wav_path=str(d.get("wav_path", "")),
            n_images=int(d.get("n_images", 2)),
            ref_texts=dict(d.get("ref_texts", {})),
        )

    @classmethod
    def from_json(cls, text: str) -> "Timeline":
        return cls.from_dict(json.loads(text))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @classmethod
    def load(cls, path) -> "Timeline":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(fh.read())


def build_timeline(segments, lines, lang=DEFAULT_LANG):
    """検出区間と台詞行を出現順に対応付けて Utterance の列にする。

    h3_audio_prompter.py の build_timeline と同じ対応付け。
    個数が食い違っても切り捨てず、欠けた側を None / 空文字で残す。
    """
    rows = []
    n = max(len(segments), len(lines))
    for i in range(n):
        seg = segments[i] if i < len(segments) else None
        spk, txt = lines[i] if i < len(lines) else ("S1", "")
        rows.append(Utterance(
            index=i + 1,
            start=seg[0] if seg else None,
            end=seg[1] if seg else None,
            speaker=spk,
            text=txt,
            lang=lang,
        ))
    return rows
