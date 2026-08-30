# -*- coding: utf-8 -*-
"""RMS ベースの発話区間検出。h3_audio_prompter.py から移植 (ロジック変更禁止)。"""

from __future__ import annotations

import numpy as np


def detect_segments(samples, sr, thresh_db=-40.0, min_silence_ms=250,
                    min_speech_ms=120, pad_ms=40):
    """RMS ベースの単純な区間検出。

    TTS 出力はノイズフロアがほぼ無音なので、これで十分に正確に切れる。
    録音物のような雑音の多い素材は想定していない。
    """
    hop = max(1, int(sr * 0.010))          # 10 ms
    win = max(hop, int(sr * 0.025))        # 25 ms
    if len(samples) < win:
        return []

    n_frames = 1 + (len(samples) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = samples[idx]
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)

    peak = float(rms.max())
    if peak <= 0:
        return []
    db = 20.0 * np.log10(rms / peak)
    voiced = db > thresh_db

    # 短い無音を埋める
    min_sil = max(1, int(min_silence_ms / 10))
    i = 0
    while i < len(voiced):
        if not voiced[i]:
            j = i
            while j < len(voiced) and not voiced[j]:
                j += 1
            if 0 < i and j < len(voiced) and (j - i) < min_sil:
                voiced[i:j] = True
            i = j
        else:
            i += 1

    # 区間化
    segs = []
    i = 0
    min_sp = max(1, int(min_speech_ms / 10))
    while i < len(voiced):
        if voiced[i]:
            j = i
            while j < len(voiced) and voiced[j]:
                j += 1
            if (j - i) >= min_sp:
                segs.append((i, j))
            i = j
        else:
            i += 1

    pad = pad_ms / 1000.0
    total = len(samples) / sr
    out = []
    for a, b in segs:
        s = max(0.0, a * hop / sr - pad)
        e = min(total, (b * hop + win) / sr + pad)
        if out and s < out[-1][1]:
            s = out[-1][1]
        if e > s:
            out.append((s, e))
    return out
