# -*- coding: utf-8 -*-
"""モデル比較モード。

同一入力 (同一タイムライン) に対する複数モデルの出力を [D] 検証にかけ、
項目ごとの合否を並べて表示する。

想定する比較対象 (仕様書より):
  - 無検閲 9B GGUF (Q8) — 本命。Prompt Writer (Ref2VA) に挿す
  - 素の Qwen3.5-9B-Instruct — 書式追従の基準線
  - Rewriter Omni (Qwen2.5-Omni-7B + LoRA) — Ref2VA で実際に学習された唯一の選択肢
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .timeline import Timeline
from .validate import CHECKS, validate, summarize, counts


_CELL = {"ok": "OK", "info": "info", "warn": "warn", "error": "NG"}


@dataclass
class ModelReport:
    name: str
    findings: list = field(default_factory=list)
    verdicts: dict = field(default_factory=dict)   # check id -> ok/info/warn/error

    @property
    def n_errors(self):
        return counts(self.findings)[0]

    @property
    def n_warnings(self):
        return counts(self.findings)[1]


def compare_outputs(named_texts, tl: Timeline | None, expect_na: bool = True):
    """[(名前, 出力テキスト), ...] を検証して ModelReport の列を返す。"""
    reports = []
    for name, text in named_texts:
        findings = validate(text, tl, expect_na=expect_na)
        reports.append(ModelReport(name=name, findings=findings,
                                   verdicts=summarize(findings)))
    return reports


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "FW" else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def render_table(reports) -> str:
    """項目 × モデルの合否表 (等幅フォント前提、全角幅を考慮して整列)。"""
    if not reports:
        return "(比較対象がありません)"
    label_w = max(_width(label) for _, label in CHECKS + (("", "項目"),)) + 2
    col_ws = [max(_width(r.name), 4) + 2 for r in reports]

    lines = []
    header = _pad("項目", label_w) + "".join(
        _pad(r.name, w) for r, w in zip(reports, col_ws))
    lines.append(header)
    lines.append("-" * _width(header))
    for check, label in CHECKS:
        row = _pad(label, label_w)
        for r, w in zip(reports, col_ws):
            row += _pad(_CELL.get(r.verdicts.get(check, "ok"), "?"), w)
        lines.append(row.rstrip())
    lines.append("-" * _width(header))
    row = _pad("エラー/警告", label_w)
    for r, w in zip(reports, col_ws):
        row += _pad(f"{r.n_errors}/{r.n_warnings}", w)
    lines.append(row.rstrip())
    return "\n".join(lines)


def render_details(reports) -> str:
    """モデルごとの検出内容の全文。表の下に付ける。"""
    from .validate import render_report
    blocks = []
    for r in reports:
        blocks.append(f"── {r.name} " + "─" * max(0, 40 - _width(r.name)))
        blocks.append(render_report(r.findings))
    return "\n".join(blocks)
