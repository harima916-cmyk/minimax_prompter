# -*- coding: utf-8 -*-
"""17k+5 フレームグリッド。h3_audio_prompter.py から移植 (ロジック変更禁止)。

MiniMax-H3 の有効フレーム長は 17k+5 (24fps) のみ。ここで選んだ秒数を
ComfyUI 側の Float (Duration) に入れ、音声も同じ長さに揃える。
"""

from __future__ import annotations

import math

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


def template_frames(seconds):
    """ComfyUI 公式テンプレートの Math Expression と同じ丸め。

    `max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17`
    — 秒数を 17k+5 の有効フレーム数へ「切り上げ」る。
    """
    x = max(GRID_BASE, round(seconds * FPS))
    return x + (GRID_BASE - (x % GRID_STEP)) % GRID_STEP


def comfy_float_hint(frames):
    """ComfyUI の Float ウィジェットが小数第 1 位までしか受けない場合に
    Duration へ入れる値。

    正確な秒数 (frames/24) を「切り捨て」で 1 桁にする。テンプレートの
    Math Expression は切り上げ丸めなので、切り捨て値 (真値より最大 0.1 秒
    = 2.4 フレーム下) は必ず同じ 17 フレーム窓に収まり、同じ frames に
    丸め上がる。四捨五入だと窓を飛び越えることがある
    (例: 90f = 3.750 秒 → 3.8 と入れると 107f になる)。
    """
    exact = frames / FPS
    return math.floor(round(exact * 10, 6)) / 10
