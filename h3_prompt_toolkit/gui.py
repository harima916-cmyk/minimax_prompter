# -*- coding: utf-8 -*-
"""Tkinter GUI。h3_audio_prompter.py の UI を踏襲し、
[C] 差し替え / [D] 検証 / モデル比較 のタブを追加したもの。

依存は numpy と tkinter のみ。ComfyUI 環境には一切依存しない。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .grid import FPS, grid_candidates
from .audio import read_wav, read_wav_raw_stereo, write_wav_pcm16, pad_to_seconds
from .segments import detect_segments
from .timeline import Timeline, build_timeline, parse_lines, fmt_ts
from .scaffold import render_scaffold, render_prompt_skeleton, render_settings_note
from .substitute import substitute
from .validate import validate, render_report
from .compare import compare_outputs, render_table, render_details

APP_TITLE = "MiniMax-H3 強制音声プロンプトビルダー"


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
        self.loaded_tl = None      # JSON から読んだタイムライン (wav なし運用)
        self.cmp_entries = []      # [(名前, テキスト)]

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
        t.rowconfigure(6, weight=2)

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
        self.var_status = tk.StringVar(value="wav を開くか、タイムライン JSON を読み込んでください。")
        ttk.Label(self, textvariable=self.var_status, foreground="#444").grid(
            row=4, column=0, sticky="w", pady=(6, 0))

    # -- [A][B] 動作 (h3_audio_prompter.py を踏襲) ---------------------------

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

    # -- タイムラインの保存 / 読込 -------------------------------------------

    def current_timeline(self):
        """[C][D] が使う現在のタイムライン。wav 実測を優先、無ければ JSON。"""
        if self.samples is not None:
            sel = self.selected_grid()
            if sel is None:
                return None
            _, frames, target = sel
            lines = parse_lines(self.txt_lines.get("1.0", "end"))
            rows = build_timeline(self.segments, lines, self.var_lang.get())
            return Timeline(utterances=rows, total_sec=target, frames=frames,
                            wav_path=self.wav_path or "",
                            n_images=int(self.var_nimg.get()))
        return self.loaded_tl

    def on_save_tl(self):
        tl = self.current_timeline()
        if tl is None:
            messagebox.showwarning("タイムラインなし",
                                   "先に wav を開いて台詞を入力してください。")
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
        self.segments = [(u.start, u.end) for u in tl.utterances
                         if u.start is not None]
        self.btn_pad["state"] = "disabled"
        self.cmb_grid["values"] = []
        self.cmb_grid.set(f"{tl.total_sec:.3f} 秒 / {tl.frames} フレーム (JSON から)")
        self.var_path.set(f"(JSON) {os.path.basename(path)}")
        self.var_info.set(
            f"動画長 {tl.total_sec:.3f} 秒 / {tl.frames} フレーム / "
            f"発話 {len(tl.utterances)} 件 / 参照画像 {tl.n_images} 枚")
        self.var_nimg.set(tl.n_images)
        if tl.utterances:
            self.var_lang.set(tl.utterances[0].lang)
        self.txt_lines.delete("1.0", "end")
        self.txt_lines.insert("1.0", "\n".join(
            f"{u.speaker}: {u.text}" if u.speaker != "S1" else u.text
            for u in tl.utterances if u.text))
        wav_name = os.path.basename(tl.wav_path) if tl.wav_path else "-"
        sc = render_scaffold(tl.utterances, tl.total_sec, tl.frames,
                             wav_name, tl.n_images, "")
        pr = render_prompt_skeleton(tl.utterances, tl.total_sec, tl.n_images)
        for widget, text in ((self.out_scaffold, sc), (self.out_prompt, pr)):
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
        self.var_status.set(
            "タイムラインを読み込みました。wav なしのため再検出はできません。")

    # -- [C] 差し替え --------------------------------------------------------

    def on_substitute(self):
        tl = self.current_timeline()
        if tl is None:
            messagebox.showwarning("タイムラインなし",
                                   "先に wav (またはタイムライン JSON) と台詞を用意してください。")
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
        n = len(self.res.utterances)
        ts_map = self._collect(self.ts_boxes) if self.ts_boxes else None
        d_map = self._collect(self.d_boxes) if self.d_boxes else None
        for m, occs, label in ((ts_map, self.res.at_occs, "At 時刻"),
                               (d_map, self.res.d_occs, "<d>")):
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
    root.geometry("1280x860")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
