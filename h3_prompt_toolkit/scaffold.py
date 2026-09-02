# -*- coding: utf-8 -*-
"""固定枠テキストと Ref2VA 骨組みの生成。h3_audio_prompter.py から移植。

出力テキストは元実装と同一 (行アクセスを dict → dataclass に替えただけ)。
"""

from __future__ import annotations

from .grid import FPS, comfy_float_hint
from .timeline import fmt_ts, Timeline

# LLM 側 (LM Studio + Qwen3.8-27B) の設定。ツール側に持たせて出力に併記する。
RECOMMENDED_SETTINGS = {
    "model": "qwen3.8-27b",      # vision。参照画像を添付して読ませる
    "temperature": 0.7,
    "top_p": 0.9,
    "reasoning_effort": "low",   # low〜medium
    "n_ctx": 16384,              # 仕様書 + ブリーフが入る余裕を見る
    "fps": FPS,                  # 24 固定
    "lang": "Japanese",          # 言語タグ既定
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
    L.append("[reference generation + audio reuse] [目標映像の 1 段落要約。"
             "どの参照が何を規定するかを明示する]")
    L.append("")
    L.append("retention_analysis:")
    for i in range(1, n_images + 1):
        L.append(f"<Picture {i}>: fully_preserved — [何をどう保持するか]")
    L.append("<Audio 1>: fully_copy — "
             "音声信号をそのまま最終音声トラックとして再利用する。")
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


# ---------------------------------------------------------------------------
# 参照の説明ヘッダ。LLM 貼り付けの先頭に置く「<Picture N> is …」の行。
# 画像は LLM が直接見るので短くてよい。音声は画像のように入力できない
# ため、この行だけがモデルへ音声の存在を伝える。
# ---------------------------------------------------------------------------

DEFAULT_PIC1_DESC = "the main character"
DEFAULT_PIC_DESC = "a reference"
DEFAULT_AUDIO_DESC = ("the forced audio track, reused as-is; the voice of the "
                      "character in <Picture 1> (S1)")


def _sentence(text, fallback):
    t = (text or "").strip() or fallback
    if t[-1] not in ".!?。":
        t += "."
    return t


def render_reference_header(pic_texts, audio_text) -> str:
    """['説明1', '説明2'], '音声の説明' → 貼り付け先頭のヘッダ行。"""
    L = []
    for i, t in enumerate(pic_texts, 1):
        fallback = DEFAULT_PIC1_DESC if i == 1 else DEFAULT_PIC_DESC
        L.append(f"<Picture {i}> is {_sentence(t, fallback)}")
    L.append(f"<Audio 1> is {_sentence(audio_text, DEFAULT_AUDIO_DESC)}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 英語版 (LLM 向け既定)。H3 も書き換え LLM も英語プロンプト前提のため、
# LLM に渡すテキストは英語で出す。内容・構造は日本語版と 1:1 対応で、
# 台詞と [Japanese] タグはそのまま保持する。
# ---------------------------------------------------------------------------

def render_scaffold_en(rows, total_sec, frames, wav_name, n_images, mode_note):
    L = []
    L.append("=== FIXED FACTS / DO NOT CHANGE ===")
    L.append(f"Video length: {total_sec:.3f} s = {frames} frames @ {FPS}fps")
    L.append(f"Audio file: {wav_name}  (forced audio; the output audio is exactly this waveform)")
    L.append(f"Reference images: {n_images} → <Picture 1>..<Picture {n_images}>")
    L.append("Audio reference: <Audio 1> (= identical to the forced audio)")
    L.append("")
    L.append("Utterance timeline:")
    if not rows:
        L.append("  (no utterances)")
    for r in rows:
        if r.start is None:
            L.append(f"  [{r.index}] span not detected  ({r.speaker})  {r.text}")
            continue
        if not r.text:
            L.append(f"  [{r.index}] {fmt_ts(r.start)} – {fmt_ts(r.end)}  "
                     f"({r.speaker})  *dialogue text not entered*")
            continue
        L.append(f"  [{r.index}] {fmt_ts(r.start)} – {fmt_ts(r.end)}  "
                 f"({r.speaker})")
        L.append(f"        {r.speaker} says, <d>[{r.lang}] {r.text}</d>")
    L.append("")
    L.append("=== INSTRUCTIONS ===")
    L.append("The utterance timeline above was measured from the actual audio waveform and is final.")
    L.append("Strictly obey all of the following:")
    L.append("- Never change, add, or remove any timestamp.")
    L.append("- Never alter the contents of any <d> tag — no translation, no summarizing, no rewording.")
    L.append("- Keep the speaker ID (S1, S2...) assignments exactly as given.")
    L.append("- The output audio is replaced by the forced audio, so keep")
    L.append("  overall_soundscape and non_diegetic_music as N/A.")
    L.append("- The speaker's mouth moves during each utterance span and stays closed during silence.")
    L.append("- Fill in ONLY the visual description, by replacing the [ ] parts of the skeleton below.")
    L.append("- Output the final prompt only — no explanations, no reasoning, no extra headings.")
    if mode_note:
        L.append(f"- NOTE: {mode_note}")
    return "\n".join(L)


def render_prompt_skeleton_en(rows, total_sec, n_images):
    L = []
    L.append("subject_definitions:")
    for i in range(1, n_images + 1):
        role = ("the primary identity anchor" if i == 1 else "a reference element")
        L.append(f"<Picture {i}> (ref_image_{i-1}): [definition of {role} — "
                 f"body type, hair, facial features, outfit, color palette; "
                 f"exclude the background]")
    L.append("<Audio 1> (ref_audio_0): [voice profile of the speaker(s) — "
             "pitch range, timbre, speaking rate, on-screen or not. "
             "Do not write the dialogue content here]")
    L.append("")
    L.append("summary:")
    L.append("[reference generation + audio reuse] [one-paragraph summary of the "
             "target video, stating which reference governs what]")
    L.append("")
    L.append("retention_analysis:")
    for i in range(1, n_images + 1):
        L.append(f"<Picture {i}>: fully_preserved — [what is preserved, and how]")
    L.append("<Audio 1>: fully_copy — the audio signal is reused 1:1 as the "
             "target video's complete final audio track.")
    L.append("")
    L.append("detailed_description:")
    L.append("[Shot 1] [overall visual style and initial composition; "
             "describe camera work as type + amplitude + speed]")
    for r in rows:
        if r.start is None or not r.text:
            continue
        L.append(f"At {fmt_ts(r.start)}, [{r.speaker}'s action and expression, "
                 f"camera state]. {r.speaker} says, "
                 f"<d>[{r.lang}] {r.text}</d>.")
        L.append(f"[movement during the pause after {fmt_ts(r.end)}]")
    # "ends at <時刻>" と書くと差し替え対象の "At 時刻" として数えられて
    # しまう (LLM が言い回しを真似ても事故る) ので until を使う
    L.append(f"[closing action until {fmt_ts(total_sec)}, the end of the video]")
    L.append("")
    L.append("overall_soundscape: N/A")
    L.append("non_diegetic_music: N/A")
    return "\n".join(L)


def render_scaffold_for_llm(rows, total_sec, frames, wav_name, n_images,
                            mode_note, out_lang="en"):
    """LLM に渡す固定枠。out_lang: "en" (既定) か "ja"。"""
    fn = render_scaffold_en if out_lang == "en" else render_scaffold
    return fn(rows, total_sec, frames, wav_name, n_images, mode_note)


def render_skeleton_for_llm(rows, total_sec, n_images, out_lang="en"):
    """LLM に渡す骨組み。out_lang: "en" (既定) か "ja"。"""
    fn = render_prompt_skeleton_en if out_lang == "en" else render_prompt_skeleton
    return fn(rows, total_sec, n_images)


def render_scaffold_from_timeline(tl: Timeline, wav_name: str,
                                  mode_note: str = "", out_lang: str = "en"):
    return render_scaffold_for_llm(tl.utterances, tl.total_sec, tl.frames,
                                   wav_name, tl.n_images, mode_note, out_lang)


def render_skeleton_from_timeline(tl: Timeline, out_lang: str = "en"):
    return render_skeleton_for_llm(tl.utterances, tl.total_sec, tl.n_images,
                                   out_lang)


def render_duration_note(wav_name: str, frames: int) -> str:
    """パディング済み wav に添える、ComfyUI へ貼り付ける数値のメモ。

    Float (Duration) → Math Expression (ceil で 17k+5 へ切り上げ) → length
    という配線が前提。案内する値は切り捨て 1 桁に統一する: 真値 (3 桁) は
    浮動小数点の誤差で ceil が 1 フレーム余分に上がり、次の枠へ飛ぶことが
    ある (5.875 → 141.00000000000003 → 158f)。
    """
    exact = frames / FPS
    hint = comfy_float_hint(frames)
    return "\n".join([
        f"音声ファイル: {wav_name}",
        f"動画長: {exact:.3f} 秒 = {frames} フレーム @ {FPS}fps",
        "",
        f"ComfyUI の Float (Duration) に入れる値: {hint:.1f}",
        f"  (Math Expression が {frames} フレームに丸め上げる。"
        f"真値 {exact:.3f} は誤差で次の枠に飛ぶことがあるので使わない)",
        "",
        "length を直接入力するワークフローの場合は、秒ではなく"
        f"フレーム数 {frames} を入れること。",
    ])


WORKFLOW_NAME = "video_minimax_h3_r2v_forced_audio_lora_v2"


def render_brief(utts, total_sec, frames, wav_name,
                 pic_texts=(), audio_text="", scenario="",
                 anchor=False, workflow=WORKFLOW_NAME) -> str:
    """仕様書 (minimax_ref2v_rule.txt) に追記するブリーフ。

    仕様書は「ユーザーが強制音声ワークフローだと言ったらモードを切り替え、
    <d> は逐語トランスクリプト」という契約なので、ここでは
    ① 強制音声である宣言とワークフロー名 ② First-frame anchor の ON/OFF
    ③ シナリオ (ユーザーの要望そのまま) ④ 参照の説明 ⑤ 実効尺 (17k+5)
    ⑥ 実測トランスクリプト を列挙する。
    シナリオは日本語のままでよい — 英語にするのは LLM の仕事。
    """
    usable = [u for u in utts if u.start is not None and u.text]
    L = []
    L.append("# BRIEF — forced-audio job (measured by h3-prompt-toolkit)")
    L.append("")
    L.append(f"ComfyUI workflow: {workflow}")
    L.append("This job uses the forced-audio / TTS-driven workflow: the supplied "
             "audio file is encoded into the audio latent and frozen with a zero "
             "noise mask, so it becomes the output track verbatim and the video "
             "must lip-sync to it.")
    if anchor:
        L.append("First-frame anchor: ON (MiniMaxH3AddGuide). <Picture 1> is the "
                 "actual first frame of the target video, so the task type "
                 "includes keyframe completion and [Shot 1] begins from it.")
    else:
        L.append("First-frame anchor: OFF. No picture is a concrete frame anchor, "
                 "so do not use the keyframe completion task type.")
    L.append("")
    scenario = (scenario or "").strip()
    if scenario:
        L.append("Scenario — what the user wants (in their own words; may be "
                 "in Japanese):")
        L.extend(scenario.splitlines())
    else:
        L.append("Scenario: not specified — fill the gap with your own craft "
                 "choices within the transcript, references, and duration below.")
    L.append("")
    L.append("References:")
    for line in render_reference_header(list(pic_texts), audio_text).splitlines():
        L.append(f"- {line}")
    L.append("")
    L.append(f"Forced audio: {wav_name} — effective duration "
             f"{total_sec:.3f} s = {frames} frames @ {FPS} fps (17k+5 grid). "
             f"Every cut timestamp must fall strictly inside it.")
    if usable:
        tail = total_sec - max(u.end for u in usable if u.end is not None)
        L.append(f"Silent tail after the last utterance: {max(0.0, tail):.3f} s.")
    L.append("")
    L.append("Verbatim transcript, measured from the waveform "
             "(FIXED — reproduce each <d> exactly):")
    if not usable:
        L.append("(no utterances entered yet)")
    for u in usable:
        L.append(f"[{u.index}] {fmt_ts(u.start)} - {fmt_ts(u.end)} "
                 f"({u.speaker}) <d>[{u.lang}] {u.text}</d>")
    L.append("")
    L.append("Mouths move only inside these spans; lips stay closed elsewhere.")
    return "\n".join(L)


def render_settings_note(settings: dict | None = None) -> str:
    """運用設定の併記用テキスト。LLM への貼り付け対象ではなく操作者向け。"""
    s = dict(RECOMMENDED_SETTINGS)
    if settings:
        s.update(settings)
    return "\n".join([
        "=== 運用設定 ===",
        f"LLM: {s['model']}  (LM Studio。vision — 参照画像を添付して読ませる)",
        f"temperature: {s['temperature']} / top_p: {s['top_p']}",
        f"reasoning_effort: {s['reasoning_effort']}  (low〜medium)",
        f"n_ctx: {s['n_ctx']} 以上  (仕様書 + ブリーフが入る余裕を見る)",
        f"fps: {s['fps']} 固定",
        f"言語タグ: {s['lang']} 既定",
    ])
