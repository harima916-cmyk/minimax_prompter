# -*- coding: utf-8 -*-
"""手動クリップ方式の支援ロジック (GUI から独立した部分)。

「音声を自分で範囲選択して、その範囲の台詞を手入力する」ための
波形エンベロープ計算・区間再生・行 (Utterance) 管理をまとめる。
Tkinter に依存しないので単体テストできる。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

from .timeline import Utterance


# ---------------------------------------------------------------------------
# 波形表示用エンベロープ
# ---------------------------------------------------------------------------

def envelope(samples, columns):
    """描画列ごとの (最小値, 最大値) を返す。samples は mono float32。

    columns 本の縦線で波形を描くための下ごしらえ。列数がサンプル数を
    超える場合は同じサンプルを引き延ばす。
    """
    columns = max(1, int(columns))
    n = len(samples)
    if n == 0:
        z = np.zeros(columns, dtype=np.float32)
        return z, z
    idx = np.linspace(0, n, columns + 1).astype(np.int64)
    mins = np.empty(columns, dtype=np.float32)
    maxs = np.empty(columns, dtype=np.float32)
    for i in range(columns):
        a, b = idx[i], max(idx[i] + 1, idx[i + 1])
        chunk = samples[a:min(b, n)]
        if len(chunk) == 0:
            chunk = samples[min(a, n - 1):min(a, n - 1) + 1]
        mins[i] = chunk.min()
        maxs[i] = chunk.max()
    return mins, maxs


def slice_range(samples, sr, start_sec, end_sec):
    """[start, end) 秒の範囲を切り出す。範囲外は黙って丸める。"""
    a = max(0, int(round(start_sec * sr)))
    b = min(len(samples), int(round(end_sec * sr)))
    if b <= a:
        return samples[0:0]
    return samples[a:b]


# ---------------------------------------------------------------------------
# 行 (Utterance) の管理
# ---------------------------------------------------------------------------

def renumber(utts):
    """開始時刻順に並べ替えて index を振り直す (同じリストを破壊的に整えて返す)。

    範囲未設定 (start が None) の行は末尾に回す。GUI の表と同じオブジェクトを
    保ったまま並べ替えたいので、新しいリストは作らない。
    """
    utts.sort(key=lambda u: (u.start is None, u.start if u.start is not None else 0.0))
    for i, u in enumerate(utts):
        u.index = i + 1
    return utts


def from_segments(segments, lang, speaker="S1"):
    """自動検出の区間列を台詞未入力の行に変換する。"""
    return [Utterance(i + 1, a, b, speaker, "", lang)
            for i, (a, b) in enumerate(segments)]


def distribute_lines(utts, lines):
    """一括貼り付けした台詞行を、行の並び順に流し込む。

    lines は parse_lines の結果 [(speaker, text), ...]。
    行数が合わない部分はそのまま残し、割り当てた件数を返す。
    """
    n = min(len(utts), len(lines))
    for u, (spk, txt) in zip(utts[:n], lines[:n]):
        u.speaker = spk
        u.text = txt
    return n


# ---------------------------------------------------------------------------
# 区間再生
# ---------------------------------------------------------------------------

def wav_bytes_pcm16(samples, sr) -> bytes:
    """mono float32 [-1,1] をメモリ上の 16bit PCM WAV にする (再生用)。"""
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    ints = (arr * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(ints.tobytes())
    return buf.getvalue()


_PLAYER_COMMANDS = (
    ("afplay", lambda p: ["afplay", p]),                     # macOS
    ("paplay", lambda p: ["paplay", p]),                     # PulseAudio
    ("aplay", lambda p: ["aplay", "-q", p]),                 # ALSA
    ("ffplay", lambda p: ["ffplay", "-nodisp", "-autoexit",  # ffmpeg
                          "-loglevel", "quiet", p]),
)


def _find_command():
    for name, build in _PLAYER_COMMANDS:
        if shutil.which(name):
            return name, build
    return None, None


class Player:
    """選択範囲のプレビュー再生。

    Windows は winsound (追加依存なし)、それ以外は afplay / paplay /
    aplay / ffplay のうち見つかったものを使う。どれも無ければ
    available() が False になり、GUI はボタンを無効化する。
    """

    def __init__(self):
        self._proc = None
        self._tmp = None
        self._use_winsound = sys.platform == "win32"
        if self._use_winsound:
            self._cmd_name = "winsound"
            self._cmd_build = None
        else:
            self._cmd_name, self._cmd_build = _find_command()

    def available(self) -> bool:
        return self._use_winsound or self._cmd_build is not None

    def backend(self) -> str:
        return self._cmd_name or "なし"

    def play(self, samples, sr):
        """mono float32 の切り出し済み範囲を非同期再生する。"""
        self.stop()
        if len(samples) == 0:
            return
        data = wav_bytes_pcm16(samples, sr)
        if self._use_winsound:
            import winsound
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
            return
        if self._cmd_build is None:
            return
        if self._tmp is None:
            fd, self._tmp = tempfile.mkstemp(prefix="h3_preview_", suffix=".wav")
            os.close(fd)
        with open(self._tmp, "wb") as fh:
            fh.write(data)
        self._proc = subprocess.Popen(
            self._cmd_build(self._tmp),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self._use_winsound:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
            return
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass
            self._proc = None

    def close(self):
        self.stop()
        if self._tmp and os.path.exists(self._tmp):
            try:
                os.remove(self._tmp)
            except OSError:
                pass
            self._tmp = None
