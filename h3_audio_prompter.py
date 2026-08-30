#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniMax-H3 Forced-Audio Prompt Builder
======================================

IrodoriTTS などで作った wav と、その台詞テキストから、
MiniMax-H3 の強制音声リップシンク用プロンプトの「固定枠」を組み立てる。

やること:
  1. wav を読んで発話区間を検出し、正確なタイムスタンプを得る
  2. H3 の有効フレーム長 (17k+5 @ 24fps) に合わせた尺を提示し、
     必要なら無音パディングした wav を書き出す
  3. LLM に渡す「固定枠」と、H3 に貼る REF2V プロンプトの骨組みを生成する

タイムスタンプを LLM に推測させないことが目的。音声が手元にあるのだから
正解は測ればよい。

依存: numpy と tkinter のみ (どちらも標準的な環境にある)
"""

import os
import re
import sys
import wave
import struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np

FPS = 24
GRID_STEP = 17
GRID_BASE = 5

APP_TITLE = "MiniMax-H3 強制音声プロンプトビルダー"


# ---------------------------------------------------------------------------
# WAV 読み書き
# ---------------------------------------------------------------------------

def read_wav(path):
    """wav を (mono float32 [-1,1], samplerate, n_channels) で返す。

    標準の wave モジュールは IEEE float 形式 (fmt tag 3) を読めないので、
    RIFF を自前で解析する。TTS の出力は float32 wav のことが多い。
    """
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


# ---------------------------------------------------------------------------
# フレームグリッド
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 発話区間検出
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 整形
# ---------------------------------------------------------------------------

def fmt_ts(sec):
    """M:SS.mmm 形式。H3 が要求する固定書式。"""
    if sec < 0:
        sec = 0.0
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:06.3f}"


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


def build_timeline(segments, lines, lang="Japanese"):
    rows = []
    n = max(len(segments), len(lines))
    for i in range(n):
        seg = segments[i] if i < len(segments) else None
        spk, txt = lines[i] if i < len(lines) else ("S1", "")
        rows.append({
            "index": i + 1,
            "start": seg[0] if seg else None,
            "end": seg[1] if seg else None,
            "speaker": spk,
            "text": txt,
            "lang": lang,
        })
    return rows


def render_scaffold(rows, total_sec, frames, wav_name, n_images, mode_note):
    L = []
    L.append("=== 固定情報 / 変更禁止 ===")
    L.append(f"動画長: {total_sec:.3f} 秒 = {frames} フレーム @ {FPS}fps")
    L.append(f"音声ファイル: {wav_name}  (強制音声。出力音声はこの波形そのもの)")
    L.append(f"参照画像: {n_images} 枚 → <Picture 1>..<Picture {n_images}>")
    L.append("音声参照: <Audio 1> (= 強制音声と同一)")
    L.append("")
    L.append("発話タイムライン:")
    if not rows:
        L.append("  (発話なし)")
    for r in rows:
        if r["start"] is None:
            L.append(f"  [{r['index']}] 区間未検出  ({r['speaker']})  {r['text']}")
            continue
        if not r["text"]:
            L.append(f"  [{r['index']}] {fmt_ts(r['start'])} – {fmt_ts(r['end'])}  "
                     f"({r['speaker']})  ※台詞テキスト未入力")
            continue
        L.append(f"  [{r['index']}] {fmt_ts(r['start'])} – {fmt_ts(r['end'])}  "
                 f"({r['speaker']})")
        L.append(f"        {r['speaker']} says, <d>[{r['lang']}] {r['text']}</d>")
    L.append("")
    L.append("=== LLM への指示 ===")
    L.append("上の発話タイムラインは実際の音声波形から測定した確定値である。")
    L.append("以下を厳守すること:")
    L.append("- タイムスタンプを一切変更・追加・削除しない。")
    L.append("- <d> タグの中身を一字一句変更しない。翻訳・要約・言い換えも禁止。")
    L.append("- 話者 ID (S1, S2...) の対応を変えない。")
    L.append("- 出力音声は差し替え済みのため、overall_soundscape と")
    L.append("  non_diegetic_music は N/A のままにする。")
    L.append("- 発話区間では該当話者の口が動き、無音区間では口を閉じている描写にする。")
    L.append("- 埋めるのは映像の描写のみ。以下の骨組みの [ ] を置き換える形で出力する。")
    L.append("- 出力は最終プロンプトのみ。説明・思考過程・見出しの追加は禁止。")
    if mode_note:
        L.append(f"- {mode_note}")
    return "\n".join(L)


def render_prompt_skeleton(rows, total_sec, n_images):
    L = []
    L.append("subject_definitions:")
    for i in range(1, n_images + 1):
        role = "主要被写体の同一性アンカー" if i == 1 else "参照要素"
        L.append(f"<Picture {i}> (ref_image_{i-1}): [{role}の定義 — "
                 f"体型・髪・顔立ち・衣装・色調を、背景を除いて記述]")
    L.append("<Audio 1> (ref_audio_0): [話者の声質定義 — 音域・音色・話速・"
             "画面内か否か。台詞内容はここに書かない]")
    L.append("")
    L.append("summary:")
    L.append("[reference generation] [目標映像の 1 段落要約。どの参照が"
             "何を規定するかを明示する]")
    L.append("")
    L.append("retention_analysis:")
    for i in range(1, n_images + 1):
        L.append(f"<Picture {i}>: fully_preserved — [何をどう保持するか]")
    L.append("<Audio 1>: audio reuse — fully_preserved — "
             "音声信号をそのまま再利用する。")
    L.append("")
    L.append("detailed_description:")
    L.append("[Shot 1] [全体の映像スタイルと初期構図。カメラワークは "
             "種類＋振幅＋速度 で記述]")
    for r in rows:
        if r["start"] is None or not r["text"]:
            continue
        L.append(f"At {fmt_ts(r['start'])}, [{r['speaker']} の動作と表情、"
                 f"カメラの状態]。{r['speaker']} says, "
                 f"<d>[{r['lang']}] {r['text']}</d>.")
        L.append(f"[{fmt_ts(r['end'])} 以降の間の動き]")
    L.append(f"[{fmt_ts(total_sec)} で終わるまでの締めの動作]")
    L.append("")
    L.append("overall_soundscape: N/A")
    L.append("non_diegetic_music: N/A")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self.samples = None
        self.sr = None
        self.n_ch = 1
        self.wav_path = None
        self.segments = []
        self.rows = []

        self._build_file_row()
        self._build_grid_row()
        self._build_detect_row()
        self._build_body()
        self._build_status()

    # -- UI 構築 ------------------------------------------------------------

    def _build_file_row(self):
        f = ttk.LabelFrame(self, text="1. 音声ファイル", padding=8)
        f.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        f.columnconfigure(1, weight=1)

        ttk.Button(f, text="wav を開く…", command=self.on_open).grid(
            row=0, column=0, padx=(0, 8))
        self.var_path = tk.StringVar(value="未選択")
        ttk.Label(f, textvariable=self.var_path, foreground="#333").grid(
            row=0, column=1, sticky="w")
        self.var_info = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.var_info).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_grid_row(self):
        f = ttk.LabelFrame(self, text="2. 尺をフレームグリッドに合わせる", padding=8)
        f.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="動画長:").grid(row=0, column=0, sticky="w")
        self.cmb_grid = ttk.Combobox(f, state="readonly", width=46, values=[])
        self.cmb_grid.grid(row=0, column=1, sticky="w", padx=(6, 8))
        self.cmb_grid.bind("<<ComboboxSelected>>", lambda e: self.refresh_output())

        self.btn_pad = ttk.Button(f, text="パディング済み wav を書き出す",
                                  command=self.on_pad, state="disabled")
        self.btn_pad.grid(row=0, column=2, sticky="e")

        ttk.Label(
            f,
            text="H3 の有効長は 17k+5 フレーム (24fps) のみ。ここで選んだ秒数を "
                 "Float (Duration) に入れ、音声も同じ長さに揃える。",
            foreground="#666", wraplength=780, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_detect_row(self):
        f = ttk.LabelFrame(self, text="3. 発話区間の検出", padding=8)
        f.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        ttk.Label(f, text="しきい値 (dB):").grid(row=0, column=0, sticky="w")
        self.var_thresh = tk.DoubleVar(value=-40.0)
        s = ttk.Scale(f, from_=-70, to=-15, variable=self.var_thresh,
                      orient="horizontal", length=170,
                      command=lambda e: self.var_thresh_lbl.set(
                          f"{self.var_thresh.get():.0f}"))
        s.grid(row=0, column=1, padx=(6, 4))
        self.var_thresh_lbl = tk.StringVar(value="-40")
        ttk.Label(f, textvariable=self.var_thresh_lbl, width=5).grid(row=0, column=2)

        ttk.Label(f, text="無音とみなす長さ (ms):").grid(row=0, column=3, sticky="w",
                                                        padx=(14, 0))
        self.var_sil = tk.IntVar(value=250)
        ttk.Spinbox(f, from_=50, to=2000, increment=50, width=7,
                    textvariable=self.var_sil).grid(row=0, column=4, padx=(6, 0))

        ttk.Button(f, text="再検出", command=self.on_detect).grid(
            row=0, column=5, padx=(14, 0))

        self.var_seg = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.var_seg, foreground="#666").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _build_body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=3, column=0, sticky="nsew")

        # 左: 入力
        left = ttk.Frame(pane, padding=(0, 0, 6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        opt = ttk.Frame(left)
        opt.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(opt, text="参照画像:").pack(side="left")
        self.var_nimg = tk.IntVar(value=2)
        sp = ttk.Spinbox(opt, from_=1, to=9, width=4, textvariable=self.var_nimg,
                         command=self.refresh_output)
        sp.pack(side="left", padx=(4, 12))
        ttk.Label(opt, text="言語タグ:").pack(side="left")
        self.var_lang = tk.StringVar(value="Japanese")
        ttk.Combobox(opt, textvariable=self.var_lang, width=12, state="readonly",
                     values=["Japanese", "English", "Chinese", "Korean"]).pack(
            side="left", padx=(4, 0))

        ttk.Label(left, text="台詞 (1 行 1 発話 / 「S2: …」で話者指定)").grid(
            row=1, column=0, sticky="w")
        self.txt_lines = tk.Text(left, height=10, wrap="word", undo=True)
        self.txt_lines.grid(row=2, column=0, sticky="nsew")
        self.txt_lines.bind("<KeyRelease>", lambda e: self.refresh_output())

        ttk.Button(left, text="この内容で出力を更新",
                   command=self.refresh_output).grid(row=3, column=0,
                                                     sticky="ew", pady=(6, 0))
        pane.add(left, weight=1)

        # 右: 出力
        right = ttk.Frame(pane)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        nb = ttk.Notebook(right)
        nb.grid(row=0, column=0, sticky="nsew")

        t1 = ttk.Frame(nb)
        t1.columnconfigure(0, weight=1)
        t1.rowconfigure(0, weight=1)
        self.out_scaffold = tk.Text(t1, wrap="word", undo=True)
        self.out_scaffold.grid(row=0, column=0, sticky="nsew")
        ttk.Button(t1, text="コピー",
                   command=lambda: self.copy(self.out_scaffold)).grid(
            row=1, column=0, sticky="ew")
        nb.add(t1, text="LLM に渡す固定枠")

        t2 = ttk.Frame(nb)
        t2.columnconfigure(0, weight=1)
        t2.rowconfigure(0, weight=1)
        self.out_prompt = tk.Text(t2, wrap="word", undo=True)
        self.out_prompt.grid(row=0, column=0, sticky="nsew")
        ttk.Button(t2, text="コピー",
                   command=lambda: self.copy(self.out_prompt)).grid(
            row=1, column=0, sticky="ew")
        nb.add(t2, text="プロンプト骨組み")

        pane.add(right, weight=2)

    def _build_status(self):
        self.var_status = tk.StringVar(value="wav を開いてください。")
        ttk.Label(self, textvariable=self.var_status, foreground="#444").grid(
            row=4, column=0, sticky="w", pady=(6, 0))

    # -- 動作 --------------------------------------------------------------

    def on_open(self):
        path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[("WAV", "*.wav"), ("すべて", "*.*")])
        if not path:
            return
        try:
            self.samples, self.sr, self.n_ch = read_wav(path)
        except Exception as exc:
            messagebox.showerror("読み込みエラー", str(exc))
            return

        self.wav_path = path
        dur = len(self.samples) / self.sr
        self.var_path.set(os.path.basename(path))
        self.var_info.set(
            f"{dur:.3f} 秒 / {self.sr} Hz / {self.n_ch} ch "
            f"= {dur * FPS:.1f} フレーム相当")

        cands, k0 = grid_candidates(dur)
        vals = []
        for k, fr, sec in cands:
            tag = "  ← 最小の収まる長さ" if k == k0 else ""
            pad = sec - dur
            vals.append(f"{sec:.3f} 秒 / {fr} フレーム / 無音追加 {pad:+.3f} 秒{tag}")
        self.cmb_grid["values"] = vals
        for i, (k, _, _) in enumerate(cands):
            if k == k0:
                self.cmb_grid.current(i)
                break
        self._grid_cands = cands
        self.btn_pad["state"] = "normal"

        self.on_detect()

    def selected_grid(self):
        i = self.cmb_grid.current()
        if i < 0 or not getattr(self, "_grid_cands", None):
            return None
        return self._grid_cands[i]

    def on_detect(self):
        if self.samples is None:
            return
        self.segments = detect_segments(
            self.samples, self.sr,
            thresh_db=float(self.var_thresh.get()),
            min_silence_ms=int(self.var_sil.get()))
        if self.segments:
            spans = ", ".join(f"{fmt_ts(a)}–{fmt_ts(b)}"
                              for a, b in self.segments[:6])
            more = " …" if len(self.segments) > 6 else ""
            self.var_seg.set(f"{len(self.segments)} 区間: {spans}{more}")
        else:
            self.var_seg.set("区間が検出できませんでした。しきい値を上げてください。")
        self.refresh_output()

    def on_pad(self):
        if self.samples is None:
            return
        sel = self.selected_grid()
        if sel is None:
            return
        _, frames, target = sel
        try:
            arr, sr = read_wav_raw_stereo(self.wav_path)
        except Exception as exc:
            messagebox.showerror("読み込みエラー", str(exc))
            return

        want = int(round(target * sr))
        cur = arr.shape[0]
        if cur > want:
            if not messagebox.askyesno(
                    "確認",
                    f"音声のほうが {(cur - want) / sr:.3f} 秒長いため末尾を切り詰めます。"
                    "続けますか？"):
                return
            out = arr[:want]
        else:
            out = np.vstack([arr, np.zeros((want - cur, arr.shape[1]),
                                           dtype=arr.dtype)])

        base, _ = os.path.splitext(self.wav_path)
        dst = f"{base}_{frames}f.wav"
        try:
            write_wav_pcm16(dst, out, sr)
        except Exception as exc:
            messagebox.showerror("書き出しエラー", str(exc))
            return
        self.var_status.set(
            f"書き出し: {os.path.basename(dst)}  "
            f"({target:.3f} 秒 / {frames} フレーム / 16bit PCM)")
        messagebox.showinfo(
            "完了",
            f"{os.path.basename(dst)} を書き出しました。\n\n"
            f"ComfyUI 側の Float (Duration) に {target:.3f} を入れ、\n"
            f"Load Audio (forced) にこのファイルを読み込ませてください。")

    def refresh_output(self, *_):
        if self.samples is None:
            return
        sel = self.selected_grid()
        if sel is None:
            return
        _, frames, target = sel

        lines = parse_lines(self.txt_lines.get("1.0", "end"))
        self.rows = build_timeline(self.segments, lines, self.var_lang.get())
        n_img = int(self.var_nimg.get())

        note = ""
        if len(lines) and len(self.segments) and len(lines) != len(self.segments):
            note = (f"※ 台詞 {len(lines)} 行に対し検出区間 {len(self.segments)} 個。"
                    "対応がずれている可能性があるので確認すること。")
            self.var_status.set(
                f"⚠ 台詞 {len(lines)} 行 / 検出区間 {len(self.segments)} 個 — "
                "しきい値か行数を調整してください。")
        else:
            self.var_status.set(
                f"台詞 {len(lines)} 行 / 検出区間 {len(self.segments)} 個 — "
                f"動画長 {target:.3f} 秒 ({frames} フレーム)")

        wav_name = os.path.basename(self.wav_path) if self.wav_path else "-"
        sc = render_scaffold(self.rows, target, frames, wav_name, n_img, note)
        pr = render_prompt_skeleton(self.rows, target, n_img)

        for widget, text in ((self.out_scaffold, sc), (self.out_prompt, pr)):
            widget.delete("1.0", "end")
            widget.insert("1.0", text)

    def copy(self, widget):
        text = widget.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.var_status.set("クリップボードにコピーしました。")


def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1180x780")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
