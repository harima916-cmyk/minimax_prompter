# -*- coding: utf-8 -*-
"""17k+5 フレームグリッド。h3_audio_prompter.py から移植 (ロジック変更禁止)。

MiniMax-H3 の有効フレーム長は 17k+5 (24fps) のみ。ここで選んだ秒数を
ComfyUI 側の Float (Duration) に入れ、音声も同じ長さに揃える。
"""

from __future__ import annotations

FPS = 24
GRID_STEP = 17
GRID_BASE = 5


def grid_frames(k):
    return GRID_STEP * k + GRID_BASE


def grid_seconds(k):
    return grid_frames(k) / FPS


def snap_up(duration_sec):
    """duration 以上で最小の有効フレーム長を返す。(k, frames, seconds)"""
    k = 0
    while grid_seconds(k) < duration_sec - 1e-9:
        k += 1
    return k, grid_frames(k), grid_seconds(k)


def grid_candidates(duration_sec, span=3):
    k0, _, _ = snap_up(duration_sec)
    out = []
    for k in range(max(0, k0 - 1), k0 + span):
        out.append((k, grid_frames(k), grid_seconds(k)))
    return out, k0
