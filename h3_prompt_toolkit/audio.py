# -*- coding: utf-8 -*-
"""WAV の読み書き。h3_audio_prompter.py から移植 (ロジック変更禁止)。

標準の wave モジュールは IEEE float 形式 (fmt tag 3) を読めないので、
RIFF を自前で解析する。TTS の出力は float32 wav のことが多い。
"""

from __future__ import annotations

import struct
import wave

import numpy as np


def read_wav(path):
    """wav を (mono float32 [-1,1], samplerate, n_channels) で返す。"""
    with open(path, "rb") as fh:
        data = fh.read()

    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("RIFF/WAVE ヘッダが見つかりません")

    pos = 12
    fmt = None
    raw = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8: pos + 8 + csize]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            raw = body
        pos += 8 + csize + (csize & 1)  # チャンクは偶数境界

    if fmt is None or raw is None:
        raise ValueError("fmt / data チャンクが揃っていません")

    audio_format, n_channels, sample_rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if audio_format == 0xFFFE and len(fmt) >= 40:
        # WAVE_FORMAT_EXTENSIBLE: 実体は SubFormat GUID の先頭 2 バイト
        audio_format = struct.unpack_from("<H", fmt, 24)[0]

    if audio_format == 1:  # PCM
        if bits == 16:
            arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif bits == 32:
            arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        elif bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8)
            n = len(b) // 3
            b = b[: n * 3].reshape(n, 3).astype(np.int32)
            v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            arr = v.astype(np.float32) / 8388608.0
        elif bits == 8:
            arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise ValueError(f"未対応のビット深度: {bits}")
    elif audio_format == 3:  # IEEE float
        dt = "<f4" if bits == 32 else "<f8"
        arr = np.frombuffer(raw, dtype=dt).astype(np.float32)
    else:
        raise ValueError(f"未対応の wav フォーマット (tag={audio_format})")

    if n_channels > 1:
        usable = (len(arr) // n_channels) * n_channels
        arr = arr[:usable].reshape(-1, n_channels).mean(axis=1)

    return arr, sample_rate, n_channels


def read_wav_raw_stereo(path):
    """パディング書き出し用に、チャンネルを保ったまま読む。"""
    mono, sr, ch = read_wav(path)
    with open(path, "rb") as fh:
        data = fh.read()
    pos = 12
    fmt = None
    raw = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8: pos + 8 + csize]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            raw = body
        pos += 8 + csize + (csize & 1)
    audio_format, n_channels, sample_rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if audio_format == 0xFFFE and len(fmt) >= 40:
        audio_format = struct.unpack_from("<H", fmt, 24)[0]

    if audio_format == 1 and bits == 16:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif audio_format == 3 and bits == 32:
        arr = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    else:
        arr = mono
        n_channels = 1

    if n_channels > 1:
        usable = (len(arr) // n_channels) * n_channels
        arr = arr[:usable].reshape(-1, n_channels)
    else:
        arr = arr.reshape(-1, 1)
    return arr, sample_rate


def write_wav_pcm16(path, arr, sample_rate):
    """arr: (n, ch) float32 [-1,1] を 16bit PCM で書き出す。"""
    arr = np.clip(arr, -1.0, 1.0)
    ints = (arr * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(arr.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(ints.tobytes())


def pad_to_seconds(arr, sr, target_sec):
    """(n, ch) 配列を target_sec ちょうどのサンプル数に無音パディング/切り詰めする。

    戻り値: (out_arr, trimmed_sec)。切り詰めた場合 trimmed_sec > 0。
    GUI では切り詰め前に確認を挟むこと (h3_audio_prompter.py の挙動を踏襲)。
    """
    want = int(round(target_sec * sr))
    cur = arr.shape[0]
    if cur > want:
        return arr[:want], (cur - want) / sr
    out = np.vstack([arr, np.zeros((want - cur, arr.shape[1]), dtype=arr.dtype)])
    return out, 0.0
