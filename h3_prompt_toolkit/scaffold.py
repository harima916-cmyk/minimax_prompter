# -*- coding: utf-8 -*-
"""固定枠テキストと Ref2VA 骨組みの生成。h3_audio_prompter.py から移植。

出力テキストは元実装と同一 (行アクセスを dict → dataclass に替えただけ)。
"""

from __future__ import annotations

from .grid import FPS
from .timeline import fmt_ts, Timeline

# ComfyUI 側の設定と食い違うと事故になるので、ツール側に持たせて出力に併記する。
RECOMMENDED_SETTINGS = {
    "n_ctx": 16384,        # 16k 以上を推奨 (公式ガイド + キャプション + タイムラインで 7680 では溢れる)
    "temperature": 0.2,    # 構造化出力なので高くしない。0.2 前後から
    "fps": FPS,            # 24 固定
    "lang": "Japanese",    # 言語タグ既定
}


def render_scaffold(rows, total_sec, frames, wav_name, n_images, mode_note):
    """LLM に渡す「固定枠」テキスト。rows は Utterance の列。"""
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
        if r.start is None:
            L.append(f"  [{r.index}] 区間未検出  ({r.speaker})  {r.text}")
            continue
        if not r.text:
            L.append(f"  [{r.index}] {fmt_ts(r.start)} – {fmt_ts(r.end)}  "
                     f"({r.speaker})  ※台詞テキスト未入力")
            continue
        L.append(f"  [{r.index}] {fmt_ts(r.start)} – {fmt_ts(r.end)}  "
                 f"({r.speaker})")
        L.append(f"        {r.speaker} says, <d>[{r.lang}] {r.text}</d>")
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
    """H3 に貼る Ref2VA プロンプトの骨組み。rows は Utterance の列。"""
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
        if r.start is None or not r.text:
            continue
        L.append(f"At {fmt_ts(r.start)}, [{r.speaker} の動作と表情、"
                 f"カメラの状態]。{r.speaker} says, "
                 f"<d>[{r.lang}] {r.text}</d>.")
        L.append(f"[{fmt_ts(r.end)} 以降の間の動き]")
    L.append(f"[{fmt_ts(total_sec)} で終わるまでの締めの動作]")
    L.append("")
    L.append("overall_soundscape: N/A")
    L.append("non_diegetic_music: N/A")
    return "\n".join(L)


def render_scaffold_from_timeline(tl: Timeline, wav_name: str, mode_note: str = ""):
    return render_scaffold(tl.utterances, tl.total_sec, tl.frames,
                           wav_name, tl.n_images, mode_note)


def render_skeleton_from_timeline(tl: Timeline):
    return render_prompt_skeleton(tl.utterances, tl.total_sec, tl.n_images)


def render_settings_note(settings: dict | None = None) -> str:
    """運用設定の併記用テキスト。LLM への貼り付け対象ではなく操作者向け。"""
    s = dict(RECOMMENDED_SETTINGS)
    if settings:
        s.update(settings)
    return "\n".join([
        "=== 運用設定 (ComfyUI 側と一致させること) ===",
        f"n_ctx: {s['n_ctx']} 以上  (7680 では公式ガイド+キャプション+タイムラインで溢れる)",
        f"temperature: {s['temperature']} 前後から  (構造化出力なので高くしない)",
        f"fps: {s['fps']} 固定",
        f"言語タグ: {s['lang']} 既定",
    ])
