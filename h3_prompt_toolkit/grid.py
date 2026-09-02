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


def template_frames(seconds, rounding="ceil"):
    """ワークフローの Math Expression と同じ丸め (秒 → 17k+5 フレーム)。

    rounding:
      "ceil"  — ワークフロー v2 の `ceil(a * 24)` (既定)
      "round" — 公式テンプレートの `round(a * 24)`

    どちらも 24fps でフレーム数にしてから、17k+5 の次の有効値へ上げる。
    """
    scaled = seconds * FPS
    if rounding == "ceil":
        # 5.875 * 24 が 141.00000000000003 になるような誤差で 1 フレーム
        # 余分に上がると次の枠へ飛ぶので、丸めてから ceil する
        n = math.ceil(round(scaled, 6))
    elif rounding == "round":
        n = round(scaled)
    else:
        raise ValueError(f"未対応の丸め: {rounding}")
    x = max(GRID_BASE, n)
    return x + (GRID_BASE - (x % GRID_STEP)) % GRID_STEP


def comfy_float_hint(frames):
    """Float (Duration) に入れる、小数第 1 位までの安全値。

    正確な秒数 (frames/24) を「切り捨て」で 1 桁にする。Math Expression は
    切り上げ丸めなので、切り捨て値 (真値より最大 0.1 秒 = 2.4 フレーム下) は
    必ず同じ 17 フレーム窓に収まり、同じ frames に丸め上がる。四捨五入だと
    窓を飛び越えることがある (例: 90f = 3.750 秒 → 3.8 と入れると 107f)。

    ceil 丸めのワークフロー v2 では真値そのもの (3 桁) も危険で、
    5.875 → 141.00000000000003 → ceil 142 → 158f と 1 枠飛ぶことがある。
    案内する値はこの切り捨て 1 桁に統一する。
    """
    exact = frames / FPS
    return math.floor(round(exact * 10, 6)) / 10
