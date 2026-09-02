# -*- coding: utf-8 -*-
"""[C] 差し替えパス。

LLM が生成した Ref2VA 出力を受け取り、タイムスタンプと台詞を実測値に置換する。

書き換え器は 6 フィールドを一気に書き切るよう学習されているため、
「時刻を空けておけ」という指示は効かない。生成させてから機械的に直す。

方針 (仕様書より):
  - 対応付けは出現順に 1:1。個数が食い違う場合は自動で捨てず、
    差分を提示して人間に選ばせる (ts_map / d_map で明示指定できる)。
  - <d> の外に書かれた話者同定情報 (音域・音色・画面内か否か) は触らない。
  - overall_soundscape / non_diegetic_music は N/A に強制する。
  - [Shot N] の切り替え時刻は発話境界にスナップする (snap_shots で無効化可)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .timeline import Timeline, Utterance, fmt_ts
from . import ref2va


@dataclass
class Edit:
    start: int
    end: int
    replacement: str
    note: str


@dataclass
class SubstitutionResult:
    text: str                      # 差し替え後 (needs_mapping 時は原文のまま)
    ok: bool                       # 差し替えを完了できたか
    needs_mapping: bool            # 個数不一致で人間の対応付けが必要か
    report: list = field(default_factory=list)    # 実行内容 (人間向け)
    problems: list = field(default_factory=list)  # 未解決の問題
    at_occs: list = field(default_factory=list)   # "At M:SS.mmm" のプレーン出現
    d_occs: list = field(default_factory=list)    # <d>...</d> の出現
    shot_occs: list = field(default_factory=list) # [Shot N] At M:SS.mmm の出現
    utterances: list = field(default_factory=list)

    def report_text(self) -> str:
        lines = list(self.report)
        if self.problems:
            lines.append("")
            lines.append("⚠ 未解決の問題:")
            lines.extend("  " + p for p in self.problems)
        return "\n".join(lines)


def _snap_boundaries(utts: list) -> list:
    bounds = set()
    for u in utts:
        if u.start is not None:
            bounds.add(round(u.start, 3))
        if u.end is not None:
            bounds.add(round(u.end, 3))
    return sorted(bounds)


def _listing_ts(occs) -> list:
    out = []
    for i, o in enumerate(occs, 1):
        out.append(f"  [{i}] {o.raw}  … {o.context}")
    return out


def _listing_d(occs) -> list:
    out = []
    for i, o in enumerate(occs, 1):
        spoken = o.spoken if len(o.spoken) <= 40 else o.spoken[:40] + "…"
        spk = o.speaker or "?"
        out.append(f"  [{i}] ({spk}) <d>{spoken}</d>")
    return out


def _listing_utts(utts) -> list:
    out = []
    for i, u in enumerate(utts, 1):
        out.append(f"  ({i}) {fmt_ts(u.start)}–{fmt_ts(u.end)} ({u.speaker}) {u.text}")
    return out


def _resolve_map(n_utts: int, n_occs: int, explicit, label: str, problems: list):
    """発話 i (1 始まり) → 出現 explicit[i-1] の対応表を作る。

    explicit が None のときは個数一致なら恒等対応、不一致なら None (要人間)。
    explicit の値 0 は「この発話は置換しない」。
    """
    if explicit is not None:
        if len(explicit) != n_utts:
            problems.append(
                f"{label}: 対応指定が {len(explicit)} 個ですが発話は {n_utts} 個です。")
            return None
        used = set()
        for v in explicit:
            if not (0 <= v <= n_occs):
                problems.append(f"{label}: 出現番号 {v} は範囲外です (1〜{n_occs})。")
                return None
            if v != 0 and v in used:
                problems.append(f"{label}: 出現番号 {v} が重複しています。")
                return None
            used.add(v)
        return list(explicit)
    if n_utts == n_occs:
        return list(range(1, n_utts + 1))
    return None


def substitute(text: str, tl: Timeline, ts_map=None, d_map=None,
               snap_shots: bool = True, force_na: bool = True) -> SubstitutionResult:
    """Ref2VA 出力 text を実測タイムライン tl で機械的に修正する。

    ts_map / d_map: 発話順の出現番号リスト (1 始まり、0 は置換しない)。
    個数一致時は省略可 (出現順に 1:1)。
    """
    res = SubstitutionResult(text=text, ok=False, needs_mapping=False)
    utts = tl.usable_utterances()
    res.utterances = utts

    # LLM が付けた <think> / コードフェンス / 前置きを先に剥がす。
    # 剥がすものが無ければ原文のまま (前後の空白だけの差では触らない)。
    stripped = ref2va.strip_wrappers(text)
    if stripped != text.strip():
        res.report.append("LLM 出力の包み (<think> / コードフェンス / 前置き) "
                          "を取り除きました。")
        text = stripped
        res.text = text

    head, sections = ref2va.find_sections(text)
    dd = sections.get("detailed_description")
    if dd is None:
        res.problems.append(
            "detailed_description フィールドが見つかりません。差し替えできません。")
        return res

    body = text[dd.body_start:dd.body_end]
    occs = ref2va.ts_occurrences(body, base=dd.body_start)
    res.at_occs = [o for o in occs if o.kind == "at"]
    res.shot_occs = [o for o in occs if o.kind == "shot"]
    bare_occs = [o for o in occs if o.kind == "bare"]
    res.d_occs = ref2va.dialogue_occurrences(body, base=dd.body_start)

    # -- 対応付け ----------------------------------------------------------
    auto_skip_ts = ts_map is None and not res.at_occs
    if auto_skip_ts:
        # "At M:SS.mmm" 形式が 1 つも無い出力 (単一ショット構成など) は
        # 置換対象が無いだけなので、個数不一致としては扱わない。
        tmap = [0] * len(utts)
        res.report.append(
            "出力に \"At M:SS.mmm\" 形式の時刻は無いため、時刻の置換はありません"
            " (単一ショット構成)。")
    else:
        tmap = _resolve_map(len(utts), len(res.at_occs), ts_map,
                            "タイムスタンプ", res.problems)
    dmap = _resolve_map(len(utts), len(res.d_occs), d_map,
                        "台詞", res.problems)

    if tmap is None and not res.problems:
        res.problems.append(
            f"タイムスタンプ: 発話 {len(utts)} 個に対し \"At M:SS.mmm\" が "
            f"{len(res.at_occs)} 個あります。ts_map で対応を指定してください。")
        res.problems.extend(_listing_ts(res.at_occs))
    if dmap is None and not any(p.startswith("台詞") for p in res.problems):
        res.problems.append(
            f"台詞: 発話 {len(utts)} 個に対し <d> ブロックが "
            f"{len(res.d_occs)} 個あります。d_map で対応を指定してください。")
        res.problems.extend(_listing_d(res.d_occs))
    if tmap is None or dmap is None:
        res.needs_mapping = True
        res.problems.append("実測タイムライン:")
        res.problems.extend(_listing_utts(utts))
        res.report.append("個数不一致のため差し替えを実行していません。"
                          "対応を指定して再実行してください。")
        return res

    # -- 編集を組み立てる ---------------------------------------------------
    edits: list[Edit] = []

    n_ts = 0
    for i, u in enumerate(utts):
        j = tmap[i]
        if j == 0:
            if not auto_skip_ts:
                res.report.append(f"発話({i+1}): タイムスタンプ置換をスキップ (指定による)")
            continue
        occ = res.at_occs[j - 1]
        new = fmt_ts(u.start)
        n_ts += 1
        if occ.raw != new:
            edits.append(Edit(occ.start, occ.end, new,
                              f"時刻 {occ.raw} → {new} (発話{i+1})"))
        else:
            res.report.append(f"時刻 {new} は一致 (発話{i+1})")

    n_d = 0
    for i, u in enumerate(utts):
        j = dmap[i]
        if j == 0:
            res.report.append(f"発話({i+1}): 台詞置換をスキップ (指定による)")
            continue
        occ = res.d_occs[j - 1]
        new_inner = f"[{u.lang}] {u.text}"
        n_d += 1
        if occ.speaker is not None and occ.speaker != u.speaker:
            res.report.append(
                f"⚠ 発話({i+1}) の話者 ID が不一致: 出力 {occ.speaker} / "
                f"入力 {u.speaker} (<d> の外は触らないため未修正)")
        if occ.lang is None:
            res.report.append(f"発話({i+1}): [言語] タグ欠落 → [{u.lang}] を付与")
        if occ.inner != new_inner:
            old = occ.spoken if len(occ.spoken) <= 24 else occ.spoken[:24] + "…"
            edits.append(Edit(occ.inner_start, occ.inner_end, new_inner,
                              f"台詞({i+1}) <d>{old}</d> → 入力どおりに復元"))
        else:
            res.report.append(f"台詞({i+1}) は一致")

    # -- ショット切り替え時刻のスナップ -------------------------------------
    bounds = _snap_boundaries(utts)
    for occ in res.shot_occs:
        if occ.shot == 1:
            res.report.append(
                "⚠ [Shot 1] に切り替え時刻が付いています (ガイドでは先頭ショットは"
                "時刻なし)。自動では削除しません。")
            continue
        if not snap_shots:
            continue
        if not bounds:
            continue
        target = min(bounds, key=lambda b: abs(b - occ.sec))
        new = fmt_ts(target)
        if occ.raw != new:
            edits.append(Edit(occ.start, occ.end, new,
                              f"[Shot {occ.shot}] {occ.raw} → {new} "
                              f"(発話境界へスナップ, Δ{target - occ.sec:+.3f}s)"))
        else:
            res.report.append(f"[Shot {occ.shot}] {new} は境界に一致")

    # -- 置換しなかった時刻は書式だけ MM:SS.mmm に揃える --------------------
    edited = {(e.start, e.end) for e in edits}
    for occ in bare_occs + res.at_occs + res.shot_occs:
        if (occ.start, occ.end) in edited:
            continue
        canonical = fmt_ts(occ.sec)
        if occ.raw == canonical:
            continue
        edits.append(Edit(occ.start, occ.end, canonical,
                          f"書式 {occ.raw} → {canonical} (値は変えていない)"))
        edited.add((occ.start, occ.end))

    # -- overall_soundscape / non_diegetic_music を N/A に -------------------
    if force_na:
        tail_add = []
        for name in ref2va.NA_FIELDS:
            sec = sections.get(name)
            if sec is None:
                tail_add.append(name)
                continue
            raw = text[sec.body_start:sec.body_end]
            stripped = raw.strip()
            if stripped == "N/A":
                continue
            # 本文の実内容の範囲だけを置き換える (末尾の空行は保つ)
            lead = len(raw) - len(raw.lstrip())
            trail = len(raw) - len(raw.rstrip())
            a = sec.body_start + lead
            b = sec.body_end - trail
            if stripped:
                shown = stripped if len(stripped) <= 30 else stripped[:30] + "…"
                edits.append(Edit(a, b, "N/A", f"{name}: 「{shown}」 → N/A に強制"))
            else:
                edits.append(Edit(a, b, "N/A", f"{name}: 空欄 → N/A"))
        for name in tail_add:
            edits.append(Edit(len(text), len(text), f"\n\n{name}: N/A",
                              f"⚠ {name} フィールドが無いため末尾に追加"))

    # -- 適用 ---------------------------------------------------------------
    edits.sort(key=lambda e: e.start)
    for a, b in zip(edits, edits[1:]):
        if a.end > b.start:
            res.problems.append(
                f"内部エラー: 編集範囲が重複しました ({a.note} / {b.note})。"
                "差し替えを中止します。")
            return res

    for e in edits:
        res.report.append(e.note)
    out = text
    for e in reversed(edits):
        out = out[:e.start] + e.replacement + out[e.end:]

    res.text = out
    res.ok = True
    res.report.insert(0, (
        f"差し替え完了: 時刻 {n_ts} 箇所 / 台詞 {n_d} 箇所 / "
        f"ショット時刻 {len(res.shot_occs)} 箇所を確認 (編集 {len(edits)} 件)"))
    return res
