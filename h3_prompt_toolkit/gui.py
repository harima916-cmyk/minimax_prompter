# -*- coding: utf-8 -*-
"""Tkinter GUI。

台詞の入力は「手動クリップ方式」: 波形をドラッグして範囲を選び、
その範囲を再生して確かめながら台詞を打ち込み、行として追加する。
自動検出は区間の下書きを表に流し込む補助に使う。

右側のタブは
  固定枠 [B] / 骨組み [B] / 差し替え [C] / 検証 [D] / モデル比較 / 運用設定。
依存は numpy と tkinter のみ。ComfyUI 環境には一切依存しない。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .grid import FPS, grid_candidates
from .audio import read_wav, read_wav_raw_stereo, write_wav_pcm16, pad_to_seconds
from .segments import detect_segments
from .timeline import (Timeline, Utterance, parse_lines, parse_ts, fmt_ts)
from .scaffold import render_scaffold, render_prompt_skeleton, render_settings_note
from .substitute import substitute
from .validate import validate, render_report
from .compare import compare_outputs, render_table, render_details
from . import clips

APP_TITLE = "MiniMax-H3 強制音声プロンプトビルダー"

SPEAKER_COLORS = {
    "S1": "#2f6fbd",
    "S2": "#c2571a",
    "S3": "#2e8b57",
    "S4": "#8b5cf6",
}
OTHER_COLOR = "#666666"


def _speaker_color(spk):
    return SPEAKER_COLORS.get(spk, OTHER_COLOR)


def _parse_time_field(text):
    """'M:SS.mmm' でも '3.5' (秒) でも受ける。だめなら None。"""
    s = (text or "").strip()
    if not s:
        return None
    ts = parse_ts(s)
    if ts is not None:
        return ts
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 波形ビュー
# ---------------------------------------------------------------------------

class WaveformView(ttk.Frame):
    """波形の表示と範囲選択。

    - ドラッグ: 範囲選択 (on_range(a, b) を呼ぶ)
    - 選択範囲の端をドラッグ: その端だけを動かして微調整
    - クリック: その時刻を含む行を選ぶ (on_pick(t) を呼ぶ)
    - ホイール: カーソル位置を中心にズーム
    """

    HEIGHT = 150
    TICK_H = 16
    MAX_PPS = 800.0
    EDGE_PX = 6           # 端つかみ判定の幅 (px)

    def __init__(self, master, on_range=None, on_pick=None):
        super().__init__(master)
        self.on_range = on_range
        self.on_pick = on_pick

        self.samples = None
        self.sr = 0
        self.duration = 0.0
        self.pps = None            # pixels per second (None = 音声なし)
        self.rows = []             # Utterance の列 (参照)
        self.selected_index = None # 選択中の行 index (1 始まり) or None
        self.selection = None      # (a, b) or None
        self.marker = None         # 動画長 (秒) or None
        self._drag_from = None

        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, height=self.HEIGHT + self.TICK_H,
                                background="#fafafa", highlightthickness=1,
                                highlightbackground="#ccc")
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.scroll = ttk.Scrollbar(self, orient="horizontal",
                                    command=self.canvas.xview)
        self.scroll.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(xscrollcommand=self.scroll.set)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<MouseWheel>", self._wheel)     # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, +1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<Configure>", lambda e: self._fit_if_unzoomed())

    # -- 公開 API ------------------------------------------------------------

    def set_audio(self, samples, sr):
        self.samples = samples
        self.sr = sr
        self.duration = (len(samples) / sr) if (samples is not None and sr) else 0.0
        self.selection = None
        self.pps = None
        self.zoom_fit()

    def clear_audio(self):
        self.samples = None
        self.sr = 0
        self.duration = 0.0
        self.selection = None
        self.pps = None
        self.redraw()

    def set_rows(self, rows, selected_index=None):
        self.rows = rows
        self.selected_index = selected_index
        self.redraw()

    def set_selection(self, a, b):
        self.selection = (a, b)
        self.redraw()

    def clear_selection(self):
        self.selection = None
        self.redraw()

    def set_marker(self, total_sec):
        self.marker = total_sec
        self.redraw()

    def zoom_fit(self):
        self.pps = self._fit_pps()
        self.redraw()

    # -- 内部 ----------------------------------------------------------------

    def _fit_pps(self):
        if not self.duration:
            return None
        width = self.canvas.winfo_width()
        if width < 50:          # まだ配置前
            width = 900
        return max(1.0, (width - 4) / self.duration)

    def _fit_if_unzoomed(self):
        fit = self._fit_pps()
        if fit is None:
            return
        # ズームしていない (= フィット幅のまま) ならリサイズに追従する
        if self.pps is None or abs(self.pps - fit) < 1e-6 or self.pps < fit:
            self.pps = fit
            self.redraw()

    def _t2x(self, t):
        return t * self.pps

    def _x2t(self, x):
        return max(0.0, min(self.duration, x / self.pps)) if self.pps else 0.0

    def _event_time(self, event):
        return self._x2t(self.canvas.canvasx(event.x))

    def _edge_at(self, widget_x):
        """widget_x が選択範囲のどちらかの端の上なら 'a' か 'b' を返す。"""
        if not self.selection or self.pps is None:
            return None
        x = self.canvas.canvasx(widget_x)
        if abs(x - self._t2x(self.selection[0])) <= self.EDGE_PX:
            return "a"
        if abs(x - self._t2x(self.selection[1])) <= self.EDGE_PX:
            return "b"
        return None

    def _press(self, event):
        if self.pps is None:
            return
        t = self._event_time(event)
        edge = self._edge_at(event.x)
        if edge == "a":
            # 始端をつかんだ → 終端を支点に動かす
            self._drag_from = (event.x, t, "edge", self.selection[1])
        elif edge == "b":
            self._drag_from = (event.x, t, "edge", self.selection[0])
        else:
            self._drag_from = (event.x, t, "new", t)

    def _drag(self, event):
        if self._drag_from is None:
            return
        _, _, _kind, anchor = self._drag_from
        t = self._event_time(event)
        self.selection = (min(anchor, t), max(anchor, t))
        self.redraw()

    def _release(self, event):
        if self._drag_from is None:
            return
        x0, t0, kind, anchor = self._drag_from
        self._drag_from = None
        if kind == "new" and abs(event.x - x0) < 4:
            if self.on_pick:
                self.on_pick(t0)
            return
        t1 = self._event_time(event)
        a, b = min(anchor, t1), max(anchor, t1)
        if b - a < 0.01:
            if kind == "new":
                return
            b = min(self.duration, a + 0.01)   # 端ドラッグで潰れたら最小幅を残す
        self.selection = (a, b)
        self.redraw()
        if self.on_range:
            self.on_range(a, b)

    def _hover(self, event):
        cursor = "sb_h_double_arrow" if self._edge_at(event.x) else ""
        if self.canvas["cursor"] != cursor:
            self.canvas.configure(cursor=cursor)

    def _wheel(self, event, direction=None):
        if self.pps is None:
            return
        if direction is None:
            direction = +1 if event.delta > 0 else -1
        factor = 1.25 if direction > 0 else 0.8
        fit = self._fit_pps() or 1.0
        anchor_t = self._event_time(event)
        old_pps = self.pps
        self.pps = min(self.MAX_PPS, max(fit, self.pps * factor))
        if abs(self.pps - old_pps) < 1e-9:
            return
        self.redraw()
        # カーソル位置の時刻が同じ画面位置に来るようにスクロール
        total_w = self.duration * self.pps
        left = self._t2x(anchor_t) - event.x
        if total_w > 0:
            self.canvas.xview_moveto(max(0.0, left / total_w))

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.pps is None or not self.duration:
            c.configure(scrollregion=(0, 0, 0, 0))
            c.create_text(12, self.HEIGHT // 2, anchor="w", fill="#999",
                          text="wav を開くと波形が表示されます")
            return

        W = int(self.duration * self.pps) + 1
        H = self.HEIGHT
        mid = H // 2
        c.configure(scrollregion=(0, 0, W, H + self.TICK_H))

        # 行の帯 (話者色)
        for u in self.rows:
            if u.start is None or u.end is None:
                continue
            x0, x1 = self._t2x(u.start), self._t2x(u.end)
            color = _speaker_color(u.speaker)
            sel = (self.selected_index == u.index)
            c.create_rectangle(x0, 0, x1, H, fill=color, stipple="gray25",
                               outline=color, width=3 if sel else 1)
            c.create_text(x0 + 3, 3, anchor="nw", fill=color,
                          font=("TkDefaultFont", 9, "bold"),
                          text=f"[{u.index}] {u.speaker}" + ("" if u.text else " ※未入力"))

        # 波形
        if self.samples is not None and len(self.samples):
            mins, maxs = clips.envelope(self.samples, W)
            amp = (H // 2) - 6
            for x in range(W):
                y0 = mid - float(maxs[x]) * amp
                y1 = mid - float(mins[x]) * amp
                c.create_line(x, y0, x, y1, fill="#5b8bd0")
        c.create_line(0, mid, W, mid, fill="#bbb")

        # 動画長マーカー
        if self.marker is not None and self.marker <= self.duration + 1e-6:
            x = self._t2x(self.marker)
            c.create_line(x, 0, x, H, fill="#d33", dash=(4, 3), width=2)

        # 選択範囲 (両端に微調整用のつまみを描く)
        if self.selection:
            a, b = self.selection
            xa, xb = self._t2x(a), self._t2x(b)
            c.create_rectangle(xa, 0, xb, H,
                               fill="#3b82f6", stipple="gray50",
                               outline="#1d4ed8", width=2)
            for x in (xa, xb):
                c.create_rectangle(x - 3, mid - 12, x + 3, mid + 12,
                                   fill="#1d4ed8", outline="#ffffff")

        # 時間目盛り
        step = 1.0
        if self.pps < 12:
            step = 5.0
        minor = 0.1 if self.pps >= 240 else None
        t = 0.0
        while t <= self.duration + 1e-9:
            x = self._t2x(t)
            c.create_line(x, H, x, H + 6, fill="#888")
            c.create_text(x + 2, H + self.TICK_H - 2, anchor="sw",
                          fill="#666", font=("TkDefaultFont", 8),
                          text=fmt_ts(t))
            t += step
        if minor:
            t = 0.0
            while t <= self.duration + 1e-9:
                x = self._t2x(t)
                c.create_line(x, H, x, H + 3, fill="#bbb")
                t += minor


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        self.samples = None
        self.sr = None
        self.n_ch = 1
        self.wav_path = None
        self.loaded_tl = None       # JSON 読込時の尺情報 (wav なし運用)
        self.utts = []              # 発話クリップの表 (Utterance のリスト)
        self.sel_row = None         # 選択中の行 index (1 始まり)
        self.cmp_entries = []       # [(名前, テキスト)]
        self.player = clips.Player()

        self._build_file_row()
        self._build_grid_row()
        self._build_wave_row()
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
        ttk.Button(f, text="タイムライン読込…", command=self.on_load_tl).grid(
            row=0, column=2, padx=(8, 4))
        ttk.Button(f, text="タイムライン保存…", command=self.on_save_tl).grid(
            row=0, column=3)
        self.var_info = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.var_info).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

    def _build_grid_row(self):
        f = ttk.LabelFrame(self, text="2. 尺をフレームグリッドに合わせる", padding=8)
        f.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="動画長:").grid(row=0, column=0, sticky="w")
        self.cmb_grid = ttk.Combobox(f, state="readonly", width=46, values=[])
        self.cmb_grid.grid(row=0, column=1, sticky="w", padx=(6, 8))
        self.cmb_grid.bind("<<ComboboxSelected>>", lambda e: self._on_grid_change())

        self.btn_pad = ttk.Button(f, text="パディング済み wav を書き出す",
                                  command=self.on_pad, state="disabled")
        self.btn_pad.grid(row=0, column=2, sticky="e")

        ttk.Label(
            f,
            text="H3 の有効長は 17k+5 フレーム (24fps) のみ。ここで選んだ秒数を "
                 "Float (Duration) に入れ、音声も同じ長さに揃える。",
            foreground="#666", wraplength=780, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_wave_row(self):
        f = ttk.LabelFrame(
            self,
            text="3. 発話クリップ — 波形をドラッグして範囲を選び、再生で確かめて台詞を入力",
            padding=8)
        f.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        f.columnconfigure(0, weight=1)

        self.wave = WaveformView(f, on_range=self.on_wave_range,
                                 on_pick=self.on_wave_pick)
        self.wave.grid(row=0, column=0, sticky="ew")

        bar = ttk.Frame(f)
        bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.var_selinfo = tk.StringVar(value="選択範囲: なし")
        ttk.Label(bar, textvariable=self.var_selinfo, width=34).pack(side="left")

        self.btn_play = ttk.Button(bar, text="▶ 選択範囲を再生",
                                   command=self.on_play, state="disabled")
        self.btn_play.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(bar, text="■ 停止", command=self.player.stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="表示を全体に戻す",
                   command=self.wave.zoom_fit).pack(side="left", padx=(12, 0))
        ttk.Label(bar, text="ホイールでズーム / クリックで行を選択",
                  foreground="#888").pack(side="left", padx=(12, 0))
        if not self.player.available():
            ttk.Label(bar, text="(この環境では再生コマンドが見つかりません)",
                      foreground="#a33").pack(side="left", padx=(12, 0))

    def _build_body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.grid(row=4, column=0, sticky="nsew")

        # 左: 発話クリップの表と編集
        left = ttk.Frame(pane, padding=(0, 0, 6, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        opt = ttk.Frame(left)
        opt.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(opt, text="参照画像:").pack(side="left")
        self.var_nimg = tk.IntVar(value=2)
        ttk.Spinbox(opt, from_=1, to=9, width=4, textvariable=self.var_nimg,
                    command=self._sync_outputs).pack(side="left", padx=(4, 12))
        ttk.Label(opt, text="言語タグ:").pack(side="left")
        self.var_lang = tk.StringVar(value="Japanese")
        cb = ttk.Combobox(opt, textvariable=self.var_lang, width=12, state="readonly",
                          values=["Japanese", "English", "Chinese", "Korean"])
        cb.pack(side="left", padx=(4, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_lang())

        cols = ("no", "start", "end", "spk", "text")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 height=9, selectmode="browse")
        for cid, label, w, anchor in (
                ("no", "#", 32, "e"),
                ("start", "開始", 84, "e"),
                ("end", "終了", 84, "e"),
                ("spk", "話者", 48, "center"),
                ("text", "台詞", 380, "w")):
            self.tree.heading(cid, text=label)
            self.tree.column(cid, width=w, anchor=anchor, stretch=(cid == "text"))
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        edit = ttk.LabelFrame(left, text="行の追加 / 編集", padding=6)
        edit.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        edit.columnconfigure(7, weight=1)

        ttk.Label(edit, text="開始:").grid(row=0, column=0, sticky="e")
        self.ent_start = ttk.Entry(edit, width=10)
        self.ent_start.grid(row=0, column=1, padx=(2, 8))
        ttk.Label(edit, text="終了:").grid(row=0, column=2, sticky="e")
        self.ent_end = ttk.Entry(edit, width=10)
        self.ent_end.grid(row=0, column=3, padx=(2, 8))
        for w in (self.ent_start, self.ent_end):
            w.bind("<Return>", self._on_range_typed)
            w.bind("<FocusOut>", self._on_range_typed)
        ttk.Label(edit, text="話者:").grid(row=0, column=4, sticky="e")
        self.cmb_spk = ttk.Combobox(edit, width=5, values=["S1", "S2", "S3", "S4"])
        self.cmb_spk.set("S1")
        self.cmb_spk.grid(row=0, column=5, padx=(2, 8))
        ttk.Label(edit, text="台詞:").grid(row=0, column=6, sticky="e")
        self.ent_text = ttk.Entry(edit)
        self.ent_text.grid(row=0, column=7, sticky="ew", padx=(2, 0))
        self.ent_text.bind("<Return>", lambda e: self.on_row_add_or_update())

        btns = ttk.Frame(edit)
        btns.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="＋ 選択範囲から行を追加",
                   command=self.on_row_add).pack(side="left")
        ttk.Button(btns, text="選択行を更新",
                   command=self.on_row_update).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="選択行を削除",
                   command=self.on_row_delete).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="全行削除",
                   command=self.on_rows_clear).pack(side="left", padx=(6, 0))
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(btns, text="自動検出を取り込む…",
                   command=self.on_detect_import).pack(side="left")
        ttk.Button(btns, text="台詞を一括貼り付け…",
                   command=self.on_bulk_paste).pack(side="left", padx=(6, 0))

        det = ttk.Frame(edit)
        det.grid(row=2, column=0, columnspan=8, sticky="ew", pady=(6, 0))
        ttk.Label(det, text="自動検出のしきい値 (dB):").pack(side="left")
        self.var_thresh = tk.DoubleVar(value=-40.0)
        ttk.Scale(det, from_=-70, to=-15, variable=self.var_thresh,
                  orient="horizontal", length=140,
                  command=lambda e: self.var_thresh_lbl.set(
                      f"{self.var_thresh.get():.0f}")).pack(side="left", padx=(4, 2))
        self.var_thresh_lbl = tk.StringVar(value="-40")
        ttk.Label(det, textvariable=self.var_thresh_lbl, width=4).pack(side="left")
        ttk.Label(det, text="無音とみなす長さ (ms):").pack(side="left", padx=(10, 0))
        self.var_sil = tk.IntVar(value=250)
        ttk.Spinbox(det, from_=50, to=2000, increment=50, width=6,
                    textvariable=self.var_sil).pack(side="left", padx=(4, 0))

        pane.add(left, weight=1)

        # 右: 出力
        right = ttk.Frame(pane)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        nb = ttk.Notebook(right)
        nb.grid(row=0, column=0, sticky="nsew")
        self.nb = nb

        self.out_scaffold = self._text_tab(nb, "LLM に渡す固定枠")
        self.out_prompt = self._text_tab(nb, "プロンプト骨組み")
        self._build_substitute_tab(nb)
        self._build_validate_tab(nb)
        self._build_compare_tab(nb)
        self._build_settings_tab(nb)

        pane.add(right, weight=2)

    def _text_tab(self, nb, title):
        t = ttk.Frame(nb)
        t.columnconfigure(0, weight=1)
        t.rowconfigure(0, weight=1)
        w = tk.Text(t, wrap="word", undo=True)
        w.grid(row=0, column=0, sticky="nsew")
        ttk.Button(t, text="コピー", command=lambda: self.copy(w)).grid(
            row=1, column=0, sticky="ew")
        nb.add(t, text=title)
        return w

    def _build_substitute_tab(self, nb):
        t = ttk.Frame(nb, padding=4)
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=3)
        t.rowconfigure(4, weight=3)
        t.rowconfigure(7, weight=2)

        ttk.Label(t, text="LLM の Ref2VA 出力を貼り付け:").grid(row=0, column=0, sticky="w")
        self.txt_llm = tk.Text(t, height=10, wrap="word", undo=True)
        self.txt_llm.grid(row=1, column=0, sticky="nsew")

        bar = ttk.Frame(t)
        bar.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(bar, text="差し替え実行", command=self.on_substitute).pack(side="left")
        self.var_snap = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="[Shot N] 時刻を発話境界にスナップ",
                        variable=self.var_snap).pack(side="left", padx=(12, 0))
        self.var_keepna = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="soundscape/music を N/A に強制しない",
                        variable=self.var_keepna).pack(side="left", padx=(12, 0))

        ttk.Label(t, text="差し替え結果 (最終プロンプト):").grid(row=3, column=0, sticky="w")
        self.txt_subst = tk.Text(t, height=10, wrap="word", undo=True)
        self.txt_subst.grid(row=4, column=0, sticky="nsew")
        ttk.Button(t, text="コピー", command=lambda: self.copy(self.txt_subst)).grid(
            row=5, column=0, sticky="ew")

        ttk.Label(t, text="報告:").grid(row=6, column=0, sticky="nw")
        self.txt_subrep = tk.Text(t, height=6, wrap="word", foreground="#444")
        self.txt_subrep.grid(row=7, column=0, sticky="nsew")
        nb.add(t, text="差し替え [C]")

    def _build_validate_tab(self, nb):
        t = ttk.Frame(nb, padding=4)
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=3)
        t.rowconfigure(4, weight=2)

        ttk.Label(t, text="検証するプロンプト:").grid(row=0, column=0, sticky="w")
        self.txt_val = tk.Text(t, height=10, wrap="word", undo=True)
        self.txt_val.grid(row=1, column=0, sticky="nsew")

        bar = ttk.Frame(t)
        bar.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(bar, text="検証実行", command=self.on_validate).pack(side="left")
        ttk.Button(bar, text="差し替え結果を取り込む",
                   command=self.on_pull_subst).pack(side="left", padx=(8, 0))
        self.var_nona = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="N/A 要求を緩める (生の LLM 出力向け)",
                        variable=self.var_nona).pack(side="left", padx=(12, 0))

        ttk.Label(t, text="検証結果:").grid(row=3, column=0, sticky="w")
        self.txt_valres = tk.Text(t, height=10, wrap="word", foreground="#333")
        self.txt_valres.grid(row=4, column=0, sticky="nsew")
        nb.add(t, text="検証 [D]")

    def _build_compare_tab(self, nb):
        t = ttk.Frame(nb, padding=4)
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=1)
        t.rowconfigure(3, weight=3)

        ttk.Label(t, text="比較するモデル出力:").grid(row=0, column=0, sticky="w")
        self.lst_cmp = tk.Listbox(t, height=5)
        self.lst_cmp.grid(row=1, column=0, sticky="nsew")

        bar = ttk.Frame(t)
        bar.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(bar, text="貼り付けで追加…", command=self.on_cmp_paste).pack(side="left")
        ttk.Button(bar, text="ファイルで追加…", command=self.on_cmp_file).pack(
            side="left", padx=(8, 0))
        ttk.Button(bar, text="選択を削除", command=self.on_cmp_del).pack(
            side="left", padx=(8, 0))
        ttk.Button(bar, text="比較実行", command=self.on_compare).pack(
            side="left", padx=(16, 0))

        self.txt_cmpres = tk.Text(t, height=12, wrap="none",
                                  font=("TkFixedFont",))
        self.txt_cmpres.grid(row=3, column=0, sticky="nsew")
        nb.add(t, text="モデル比較")

    def _build_settings_tab(self, nb):
        t = ttk.Frame(nb, padding=4)
        t.columnconfigure(0, weight=1)
        t.rowconfigure(0, weight=1)
        w = tk.Text(t, wrap="word", foreground="#333")
        w.grid(row=0, column=0, sticky="nsew")
        w.insert("1.0", render_settings_note() + "\n\n"
                 "この値は ComfyUI 側 (Prompt Writer / Rewriter ノード) の設定と\n"
                 "一致させること。食い違うと事故になる。")
        w.configure(state="disabled")
        nb.add(t, text="運用設定")

    def _build_status(self):
        self.var_status = tk.StringVar(
            value="wav を開くか、タイムライン JSON を読み込んでください。")
        ttk.Label(self, textvariable=self.var_status, foreground="#444").grid(
            row=5, column=0, sticky="w", pady=(6, 0))

    # -- wav / グリッド ------------------------------------------------------

    def on_open(self):
        path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[("WAV", "*.wav"), ("すべて", "*.*")])
        if not path:
            return
        try:
            samples, sr, n_ch = read_wav(path)
        except Exception as exc:
            messagebox.showerror("読み込みエラー", str(exc))
            return

        if self.utts and any(u.text for u in self.utts):
            if not messagebox.askyesno(
                    "確認", "入力済みの発話クリップを消して新しい wav を開きますか？"):
                return

        self.samples, self.sr, self.n_ch = samples, sr, n_ch
        self.wav_path = path
        self.loaded_tl = None
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
        self.btn_play["state"] = "normal" if self.player.available() else "disabled"
        self.btn_stop["state"] = self.btn_play["state"]

        self.wave.set_audio(self.samples, self.sr)

        # 下書きとして自動検出を流し込む (台詞は空。あとで各行に入力する)
        segs = self._detect()
        self.utts = clips.from_segments(segs, self.var_lang.get())
        self.sel_row = None
        self._sync_all()
        self.var_status.set(
            f"自動検出で {len(segs)} 区間を下書きにしました。"
            "各行を選んで再生し、台詞を入力してください。範囲は波形のドラッグで作り直せます。")

    def selected_grid(self):
        i = self.cmb_grid.current()
        if i < 0 or not getattr(self, "_grid_cands", None):
            return None
        return self._grid_cands[i]

    def _on_grid_change(self):
        sel = self.selected_grid()
        if sel:
            self.wave.set_marker(sel[2])
        self._sync_outputs()

    def _detect(self):
        if self.samples is None:
            return []
        return detect_segments(
            self.samples, self.sr,
            thresh_db=float(self.var_thresh.get()),
            min_silence_ms=int(self.var_sil.get()))

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
        out, _trimmed = pad_to_seconds(arr, sr, target)

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

    # -- 波形イベント --------------------------------------------------------

    def on_wave_range(self, a, b):
        self.ent_start.delete(0, "end")
        self.ent_start.insert(0, fmt_ts(a))
        self.ent_end.delete(0, "end")
        self.ent_end.insert(0, fmt_ts(b))
        self.var_selinfo.set(
            f"選択範囲: {fmt_ts(a)} – {fmt_ts(b)}  ({b - a:.3f} 秒)")

    def _on_range_typed(self, *_):
        """開始/終了欄に打った値を波形の選択範囲へ反映する。"""
        a = _parse_time_field(self.ent_start.get())
        b = _parse_time_field(self.ent_end.get())
        if a is None or b is None or b <= a:
            return
        if self.samples is not None:
            dur = len(self.samples) / self.sr
            a = max(0.0, min(a, dur))
            b = max(0.0, min(b, dur))
            if b <= a:
                return
            self.wave.set_selection(a, b)
        self.var_selinfo.set(
            f"選択範囲: {fmt_ts(a)} – {fmt_ts(b)}  ({b - a:.3f} 秒)")

    def on_wave_pick(self, t):
        for u in self.utts:
            if u.start is not None and u.end is not None and u.start <= t <= u.end:
                self._select_row(u.index)
                return

    def on_play(self):
        if self.samples is None:
            return
        a = _parse_time_field(self.ent_start.get())
        b = _parse_time_field(self.ent_end.get())
        if a is None or b is None or b <= a:
            self.var_status.set("再生する範囲を先に選択してください。")
            return
        try:
            self.player.play(clips.slice_range(self.samples, self.sr, a, b), self.sr)
        except Exception as exc:
            self.var_status.set(f"再生できませんでした: {exc}")

    # -- 行の操作 ------------------------------------------------------------

    def _edit_fields(self):
        a = _parse_time_field(self.ent_start.get())
        b = _parse_time_field(self.ent_end.get())
        spk = (self.cmb_spk.get().strip() or "S1").upper()
        txt = self.ent_text.get().strip()
        return a, b, spk, txt

    def on_row_add(self):
        a, b, spk, txt = self._edit_fields()
        if a is None or b is None or b <= a:
            messagebox.showwarning(
                "範囲がありません",
                "先に波形をドラッグして範囲を選択してください\n"
                "(開始・終了の欄に手入力もできます)。")
            return
        u = Utterance(0, a, b, spk, txt, self.var_lang.get())
        self.utts.append(u)
        clips.renumber(self.utts)
        self.sel_row = u.index
        self._sync_all()
        self.ent_text.delete(0, "end")
        self.wave.clear_selection()
        self.var_status.set(
            f"行 [{u.index}] を追加しました。" +
            ("" if txt else " 台詞が未入力です。行を選んだまま入力して「選択行を更新」。"))

    def on_row_update(self):
        u = self._current_row()
        if u is None:
            messagebox.showwarning("行が未選択", "更新する行を表で選んでください。")
            return
        a, b, spk, txt = self._edit_fields()
        if a is not None and b is not None and b > a:
            u.start, u.end = a, b
        u.speaker = spk
        u.text = txt
        clips.renumber(self.utts)
        self.sel_row = u.index
        self._sync_all()
        self.var_status.set(f"行 [{u.index}] を更新しました。")

    def on_row_add_or_update(self):
        if self._current_row() is not None:
            self.on_row_update()
        else:
            self.on_row_add()

    def on_row_delete(self):
        u = self._current_row()
        if u is None:
            return
        self.utts.remove(u)
        clips.renumber(self.utts)
        self.sel_row = None
        self._sync_all()

    def on_rows_clear(self):
        if self.utts and not messagebox.askyesno("確認", "すべての行を削除しますか？"):
            return
        self.utts = []
        self.sel_row = None
        self._sync_all()

    def on_detect_import(self):
        if self.samples is None:
            messagebox.showwarning("wav がありません", "先に wav を開いてください。")
            return
        segs = self._detect()
        if not segs:
            self.var_status.set("区間が検出できませんでした。しきい値を上げてください。")
            return
        if self.utts and any(u.text for u in self.utts):
            if not messagebox.askyesno(
                    "確認",
                    f"検出した {len(segs)} 区間で現在の {len(self.utts)} 行を"
                    "置き換えます (入力済みの台詞は消えます)。よろしいですか？"):
                return
        self.utts = clips.from_segments(segs, self.var_lang.get())
        self.sel_row = None
        self._sync_all()
        self.var_status.set(f"{len(segs)} 区間を取り込みました。各行に台詞を入力してください。")

    def on_bulk_paste(self):
        dlg = BulkPasteDialog(self)
        if dlg.result is None:
            return
        lines = parse_lines(dlg.result)
        if not lines:
            return
        if not self.utts:
            self.utts = [Utterance(i + 1, None, None, spk, txt, self.var_lang.get())
                         for i, (spk, txt) in enumerate(lines)]
            self._sync_all()
            self.var_status.set(
                f"{len(lines)} 行を台詞だけで作成しました。各行に範囲を割り当ててください。")
            return
        n = clips.distribute_lines(self.utts, lines)
        self._sync_all()
        note = ""
        if len(lines) != len(self.utts):
            note = f" (台詞 {len(lines)} 行 / 表 {len(self.utts)} 行 — 数が合っていません)"
        self.var_status.set(f"{n} 行に台詞を流し込みました。{note}")

    def _current_row(self):
        if self.sel_row is None:
            return None
        for u in self.utts:
            if u.index == self.sel_row:
                return u
        return None

    def _select_row(self, index):
        self.sel_row = index
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)
        self._load_row_to_fields()
        self.wave.set_rows(self.utts, self.sel_row)

    def on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        self.sel_row = int(sel[0])
        self._load_row_to_fields()
        self.wave.set_rows(self.utts, self.sel_row)

    def _load_row_to_fields(self):
        u = self._current_row()
        if u is None:
            return
        self.ent_start.delete(0, "end")
        self.ent_end.delete(0, "end")
        if u.start is not None:
            self.ent_start.insert(0, fmt_ts(u.start))
        if u.end is not None:
            self.ent_end.insert(0, fmt_ts(u.end))
        self.cmb_spk.set(u.speaker)
        self.ent_text.delete(0, "end")
        self.ent_text.insert(0, u.text)
        if u.start is not None and u.end is not None:
            self.wave.set_selection(u.start, u.end)
            self.var_selinfo.set(
                f"選択範囲: {fmt_ts(u.start)} – {fmt_ts(u.end)}  (行 [{u.index}])")

    def _apply_lang(self):
        lang = self.var_lang.get()
        for u in self.utts:
            u.lang = lang
        self._sync_outputs()

    # -- 表・出力の同期 ------------------------------------------------------

    def _sync_all(self):
        clips.renumber(self.utts)
        self._refresh_tree()
        self.wave.set_rows(self.utts, self.sel_row)
        self._sync_outputs()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for u in self.utts:
            self.tree.insert(
                "", "end", iid=str(u.index),
                values=(u.index,
                        fmt_ts(u.start) if u.start is not None else "—",
                        fmt_ts(u.end) if u.end is not None else "—",
                        u.speaker,
                        u.text or "(未入力)"))
        if self.sel_row is not None and self.tree.exists(str(self.sel_row)):
            self.tree.selection_set(str(self.sel_row))

    def _totals(self):
        """(total_sec, frames) — wav があればグリッド選択、なければ JSON の値。"""
        if self.samples is not None:
            sel = self.selected_grid()
            if sel is None:
                return None
            return sel[2], sel[1]
        if self.loaded_tl is not None:
            return self.loaded_tl.total_sec, self.loaded_tl.frames
        return None

    def _sync_outputs(self, *_):
        totals = self._totals()
        if totals is None:
            return
        total, frames = totals
        if self.samples is not None:
            self.wave.set_marker(total)
        n_img = int(self.var_nimg.get())
        wav_name = os.path.basename(self.wav_path) if self.wav_path else (
            os.path.basename(self.loaded_tl.wav_path)
            if self.loaded_tl and self.loaded_tl.wav_path else "-")

        n_empty = sum(1 for u in self.utts if u.start is not None and not u.text)
        n_norange = sum(1 for u in self.utts if u.start is None)
        note = ""
        if n_empty or n_norange:
            parts = []
            if n_empty:
                parts.append(f"台詞未入力 {n_empty} 行")
            if n_norange:
                parts.append(f"範囲未設定 {n_norange} 行")
            note = "※ " + " / ".join(parts) + " が残っている。埋めてから使うこと。"

        sc = render_scaffold(self.utts, total, frames, wav_name, n_img, note)
        pr = render_prompt_skeleton(self.utts, total, n_img)
        for widget, text in ((self.out_scaffold, sc), (self.out_prompt, pr)):
            widget.delete("1.0", "end")
            widget.insert("1.0", text)

        done = sum(1 for u in self.utts if u.usable())
        self.var_status.set(
            f"発話 {len(self.utts)} 行 (入力済み {done}) — "
            f"動画長 {total:.3f} 秒 ({frames} フレーム)"
            + (f"  {note}" if note else ""))

    def copy(self, widget):
        text = widget.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.var_status.set("クリップボードにコピーしました。")

    # -- タイムラインの保存 / 読込 -------------------------------------------

    def current_timeline(self):
        totals = self._totals()
        if totals is None:
            return None
        total, frames = totals
        clips.renumber(self.utts)
        wav_path = self.wav_path or (self.loaded_tl.wav_path if self.loaded_tl else "")
        return Timeline(utterances=list(self.utts), total_sec=total, frames=frames,
                        wav_path=wav_path, n_images=int(self.var_nimg.get()))

    def on_save_tl(self):
        tl = self.current_timeline()
        if tl is None:
            messagebox.showwarning("タイムラインなし",
                                   "先に wav を開いて発話クリップを作ってください。")
            return
        path = filedialog.asksaveasfilename(
            title="タイムラインを保存", defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        tl.save(path)
        self.var_status.set(f"タイムライン保存: {os.path.basename(path)}")

    def on_load_tl(self):
        path = filedialog.askopenfilename(
            title="タイムライン JSON を選択",
            filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
        if not path:
            return
        try:
            tl = Timeline.load(path)
        except Exception as exc:
            messagebox.showerror("読み込みエラー", str(exc))
            return
        self.loaded_tl = tl
        self.samples = None
        self.wav_path = None
        self.utts = list(tl.utterances)
        self.sel_row = None
        self.btn_pad["state"] = "disabled"
        self.btn_play["state"] = "disabled"
        self.btn_stop["state"] = "disabled"
        self.cmb_grid["values"] = []
        self.cmb_grid.set(f"{tl.total_sec:.3f} 秒 / {tl.frames} フレーム (JSON から)")
        self.wave.clear_audio()
        self.var_path.set(f"(JSON) {os.path.basename(path)}")
        self.var_info.set(
            f"動画長 {tl.total_sec:.3f} 秒 / {tl.frames} フレーム / "
            f"発話 {len(tl.utterances)} 件 / 参照画像 {tl.n_images} 枚")
        self.var_nimg.set(tl.n_images)
        if tl.utterances:
            self.var_lang.set(tl.utterances[0].lang)
        self._sync_all()
        self.var_status.set(
            "タイムラインを読み込みました。wav が無いため波形と再生は使えません。")

    # -- [C] 差し替え --------------------------------------------------------

    def on_substitute(self):
        tl = self.current_timeline()
        if tl is None:
            messagebox.showwarning("タイムラインなし",
                                   "先に wav (またはタイムライン JSON) と発話クリップを"
                                   "用意してください。")
            return
        text = self.txt_llm.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("入力なし", "LLM の出力を貼り付けてください。")
            return
        res = substitute(text, tl,
                         snap_shots=self.var_snap.get(),
                         force_na=not self.var_keepna.get())
        if res.needs_mapping:
            maps = MappingDialog(self, res).result
            if maps is None:
                self._show_subst(res)
                self.var_status.set("差し替えは保留されました (対応付け未指定)。")
                return
            ts_map, d_map = maps
            res = substitute(text, tl, ts_map=ts_map, d_map=d_map,
                             snap_shots=self.var_snap.get(),
                             force_na=not self.var_keepna.get())
        self._show_subst(res)
        if res.ok:
            self.var_status.set("差し替え完了。検証 [D] タブで確認してください。")
        else:
            self.var_status.set("差し替えできませんでした。報告を確認してください。")

    def _show_subst(self, res):
        self.txt_subst.delete("1.0", "end")
        self.txt_subst.insert("1.0", res.text if res.ok else "")
        self.txt_subrep.delete("1.0", "end")
        self.txt_subrep.insert("1.0", res.report_text())

    # -- [D] 検証 ------------------------------------------------------------

    def on_pull_subst(self):
        text = self.txt_subst.get("1.0", "end-1c")
        self.txt_val.delete("1.0", "end")
        self.txt_val.insert("1.0", text)

    def on_validate(self):
        tl = self.current_timeline()
        text = self.txt_val.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("入力なし", "検証するプロンプトを貼り付けてください。")
            return
        findings = validate(text, tl, expect_na=not self.var_nona.get())
        self.txt_valres.delete("1.0", "end")
        header = "" if tl is not None else "(タイムラインなし: 書式チェックのみ)\n"
        self.txt_valres.insert("1.0", header + render_report(findings))

    # -- モデル比較 ----------------------------------------------------------

    def on_cmp_paste(self):
        dlg = PasteDialog(self)
        if dlg.result is None:
            return
        name, text = dlg.result
        self.cmp_entries.append((name, text))
        self.lst_cmp.insert("end", name)

    def on_cmp_file(self):
        paths = filedialog.askopenfilenames(
            title="モデル出力ファイルを選択",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        for p in paths:
            try:
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
            except Exception as exc:
                messagebox.showerror("読み込みエラー", f"{p}: {exc}")
                continue
            name = os.path.splitext(os.path.basename(p))[0]
            self.cmp_entries.append((name, text))
            self.lst_cmp.insert("end", name)

    def on_cmp_del(self):
        sel = list(self.lst_cmp.curselection())
        for i in reversed(sel):
            self.lst_cmp.delete(i)
            del self.cmp_entries[i]

    def on_compare(self):
        if not self.cmp_entries:
            messagebox.showwarning("比較対象なし", "モデル出力を追加してください。")
            return
        tl = self.current_timeline()
        reports = compare_outputs(self.cmp_entries, tl,
                                  expect_na=not self.var_nona.get())
        out = render_table(reports) + "\n\n" + render_details(reports)
        if tl is None:
            out = "(タイムラインなし: 書式チェックのみ)\n" + out
        self.txt_cmpres.delete("1.0", "end")
        self.txt_cmpres.insert("1.0", out)


class BulkPasteDialog(tk.Toplevel):
    """台詞を 1 行 1 発話でまとめて貼り付ける (行の並び順に流し込む)。"""

    def __init__(self, master):
        super().__init__(master)
        self.title("台詞を一括貼り付け")
        self.result = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, justify="left", text=(
            "1 行 1 発話。「S2: …」で話者を指定できます。\n"
            "表の行の並び順 (開始時刻順) に上から流し込みます。")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.txt = tk.Text(self, width=70, height=14, wrap="word")
        self.txt.grid(row=1, column=0, sticky="nsew", padx=8)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="e", padx=8, pady=8)
        ttk.Button(bar, text="流し込む", command=self.on_ok).pack(side="left")
        ttk.Button(bar, text="キャンセル", command=self.destroy).pack(
            side="left", padx=(6, 0))

        self.transient(master)
        self.grab_set()
        self.wait_window()

    def on_ok(self):
        text = self.txt.get("1.0", "end-1c")
        if text.strip():
            self.result = text
        self.destroy()


class PasteDialog(tk.Toplevel):
    """名前を付けてモデル出力を貼り付けるだけの小さなダイアログ。"""

    def __init__(self, master):
        super().__init__(master)
        self.title("モデル出力を追加")
        self.result = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="名前:").grid(row=0, column=0, sticky="w",
                                           padx=8, pady=(8, 2))
        self.ent = ttk.Entry(self)
        self.ent.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 2))
        self.ent.insert(0, f"model{master.lst_cmp.size() + 1}")

        self.txt = tk.Text(self, width=80, height=20, wrap="word")
        self.txt.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=8, pady=8)
        ttk.Button(bar, text="追加", command=self.on_ok).pack(side="left")
        ttk.Button(bar, text="キャンセル", command=self.destroy).pack(
            side="left", padx=(6, 0))

        self.transient(master)
        self.grab_set()
        self.wait_window()

    def on_ok(self):
        text = self.txt.get("1.0", "end-1c")
        name = self.ent.get().strip() or "model"
        if text.strip():
            self.result = (name, text)
        self.destroy()


class MappingDialog(tk.Toplevel):
    """個数不一致のときに、人間が発話と出現の対応を選ぶダイアログ。

    無言で辻褄を合わせるのが一番危険なので、自動では捨てない (仕様書)。
    """

    SKIP = "— 置換しない —"

    def __init__(self, master, res):
        super().__init__(master)
        self.title("対応付けの確認")
        self.result = None
        self.res = res

        utts = res.utterances
        need_ts = len(res.at_occs) != len(utts)
        need_d = len(res.d_occs) != len(utts)

        ttk.Label(self, text=(
            "検出された出現と実測の発話の個数が食い違っています。\n"
            "発話ごとに、どの出現を置き換えるかを選んでください。"),
            justify="left").grid(row=0, column=0, columnspan=3,
                                 sticky="w", padx=8, pady=8)

        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=8)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(frame, text="発話 (実測)").grid(row=0, column=0, sticky="w")
        if need_ts:
            ttk.Label(frame, text=f"At 時刻の出現 ({len(res.at_occs)} 個)").grid(
                row=0, column=1, sticky="w", padx=(10, 0))
        if need_d:
            ttk.Label(frame, text=f"<d> の出現 ({len(res.d_occs)} 個)").grid(
                row=0, column=2, sticky="w", padx=(10, 0))

        ts_opts = [self.SKIP] + [
            f"[{i}] {o.raw} … {o.context[:36]}"
            for i, o in enumerate(res.at_occs, 1)]
        d_opts = [self.SKIP] + [
            f"[{i}] ({o.speaker or '?'}) {o.spoken[:24]}"
            for i, o in enumerate(res.d_occs, 1)]

        self.ts_boxes = []
        self.d_boxes = []
        for i, u in enumerate(utts):
            label = f"({i+1}) {fmt_ts(u.start)} ({u.speaker}) {u.text[:20]}"
            ttk.Label(frame, text=label).grid(row=i + 1, column=0, sticky="w")
            if need_ts:
                cb = ttk.Combobox(frame, state="readonly", width=44, values=ts_opts)
                cb.current(min(i + 1, len(ts_opts) - 1) if i < len(res.at_occs) else 0)
                cb.grid(row=i + 1, column=1, sticky="w", padx=(10, 0), pady=1)
                self.ts_boxes.append(cb)
            if need_d:
                cb = ttk.Combobox(frame, state="readonly", width=36, values=d_opts)
                cb.current(min(i + 1, len(d_opts) - 1) if i < len(res.d_occs) else 0)
                cb.grid(row=i + 1, column=2, sticky="w", padx=(10, 0), pady=1)
                self.d_boxes.append(cb)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        ttk.Button(bar, text="この対応で差し替える", command=self.on_ok).pack(side="left")
        ttk.Button(bar, text="キャンセル", command=self.destroy).pack(
            side="left", padx=(6, 0))

        self.transient(master)
        self.grab_set()
        self.wait_window()

    def _collect(self, boxes):
        out = []
        for cb in boxes:
            i = cb.current()   # 0 = SKIP
            out.append(i)      # SKIP は 0、出現は 1 始まりでそのまま対応
        return out

    def on_ok(self):
        ts_map = self._collect(self.ts_boxes) if self.ts_boxes else None
        d_map = self._collect(self.d_boxes) if self.d_boxes else None
        for m, label in ((ts_map, "At 時刻"), (d_map, "<d>")):
            if m is None:
                continue
            picked = [v for v in m if v != 0]
            if len(picked) != len(set(picked)):
                messagebox.showwarning("重複", f"{label} の出現が重複して選ばれています。",
                                       parent=self)
                return
        self.result = (ts_map, d_map)
        self.destroy()


def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1360x900")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    app = App(root)

    def on_close():
        app.player.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
