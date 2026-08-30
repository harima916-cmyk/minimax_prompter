# -*- coding: utf-8 -*-
"""バッチ用 CLI。

    python -m h3_prompt_toolkit.cli measure    --wav voice.wav --lines lines.txt --save-timeline tl.json
    python -m h3_prompt_toolkit.cli pad        --wav voice.wav
    python -m h3_prompt_toolkit.cli scaffold   --timeline tl.json
    python -m h3_prompt_toolkit.cli substitute --timeline tl.json llm_output.txt -o final.txt
    python -m h3_prompt_toolkit.cli validate   --timeline tl.json final.txt
    python -m h3_prompt_toolkit.cli compare    --timeline tl.json 9b=out_9b.txt omni=out_omni.txt
    python -m h3_prompt_toolkit.cli settings

差し替え・検証の入力は引数のファイル、または省略時 stdin。
人間向けの経過報告は stderr、成果物は stdout に出る。

終了コード: 0 = 成功 / 1 = エラー (検証ではエラー検出あり) /
2 = 差し替えの個数不一致 (--ts-map / --d-map での指定が必要)
"""

from __future__ import annotations

import argparse
import os
import sys

from .grid import (FPS, snap_up, grid_candidates, grid_frames, grid_seconds,
                   comfy_float_hint)
from .timeline import Timeline, build_timeline, parse_lines, fmt_ts, DEFAULT_LANG
from .scaffold import (render_scaffold_from_timeline, render_skeleton_from_timeline,
                       render_reference_header, render_settings_note,
                       render_duration_note)
from .substitute import substitute
from .validate import validate, render_report, counts
from .compare import compare_outputs, render_table, render_details


def _err(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------
# タイムラインの組み立て / 読み込み
# ---------------------------------------------------------------------------

def _add_source_args(p, need_lines=True):
    g = p.add_argument_group("タイムラインの入力")
    g.add_argument("--timeline", metavar="JSON",
                   help="measure --save-timeline で保存した JSON")
    g.add_argument("--wav", metavar="WAV", help="音声ファイル (JSON の代わりに実測する)")
    if need_lines:
        g.add_argument("--lines", metavar="TXT",
                       help="台詞ファイル (1 行 1 発話 / 「S2: …」で話者指定)")
    g.add_argument("--lang", default=DEFAULT_LANG, help=f"言語タグ (既定 {DEFAULT_LANG})")
    g.add_argument("--n-images", type=int, default=2, help="参照画像の枚数 (既定 2)")
    g.add_argument("--grid-k", type=int, default=None,
                   help="尺のグリッド係数 k (17k+5 フレーム)。省略時は収まる最小")
    d = p.add_argument_group("発話区間検出")
    d.add_argument("--thresh-db", type=float, default=-40.0)
    d.add_argument("--min-silence-ms", type=int, default=250)
    d.add_argument("--min-speech-ms", type=int, default=120)
    d.add_argument("--pad-ms", type=int, default=40)


def _measure(args):
    """wav と台詞から Timeline を実測で組み立てる。(tl, dur) を返す。"""
    from .audio import read_wav
    from .segments import detect_segments

    samples, sr, _ = read_wav(args.wav)
    dur = len(samples) / sr
    segs = detect_segments(samples, sr,
                           thresh_db=args.thresh_db,
                           min_silence_ms=args.min_silence_ms,
                           min_speech_ms=args.min_speech_ms,
                           pad_ms=args.pad_ms)
    lines = []
    if getattr(args, "lines", None):
        with open(args.lines, encoding="utf-8") as fh:
            lines = parse_lines(fh.read())
    utts = build_timeline(segs, lines, args.lang)

    if args.grid_k is not None:
        k = args.grid_k
        frames, total = grid_frames(k), grid_seconds(k)
    else:
        k, frames, total = snap_up(dur)
    if total < dur - 1e-9:
        _err(f"⚠ 指定の尺 {total:.3f}s は音声 {dur:.3f}s より短いです")

    tl = Timeline(utterances=utts, total_sec=total, frames=frames,
                  wav_path=args.wav, n_images=args.n_images)
    return tl, dur


def _load_timeline(args, need_lines=True):
    if args.timeline:
        return Timeline.load(args.timeline)
    if args.wav:
        return _measure(args)[0]
    _err("--timeline か --wav のどちらかを指定してください。")
    sys.exit(1)


def _read_input(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_output(path, text):
    if path in (None, "-"):
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        _err(f"書き出し: {path}")


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def cmd_measure(args):
    tl, dur = _measure(args)
    _err(f"{args.wav}: {dur:.3f} 秒 → 動画長 {tl.total_sec:.3f} 秒 "
         f"({tl.frames} フレーム @ {FPS}fps)")
    cands, k0 = grid_candidates(dur)
    _err("グリッド候補: " + ", ".join(
        f"{sec:.3f}s/{fr}f" + ("*" if fr == tl.frames else "")
        for _, fr, sec in cands))
    for u in tl.utterances:
        if u.start is None:
            print(f"[{u.index}] 区間未検出           ({u.speaker}) {u.text}")
        else:
            print(f"[{u.index}] {fmt_ts(u.start)} – {fmt_ts(u.end)} ({u.speaker}) {u.text}")
    n_seg = sum(1 for u in tl.utterances if u.start is not None)
    n_lin = sum(1 for u in tl.utterances if u.text)
    if n_seg != n_lin:
        _err(f"⚠ 台詞 {n_lin} 行に対し検出区間 {n_seg} 個。対応を確認してください。")
    if args.save_timeline:
        tl.save(args.save_timeline)
        _err(f"タイムライン保存: {args.save_timeline}")
    if args.json:
        print(tl.to_json())
    return 0


def cmd_pad(args):
    from .audio import read_wav, read_wav_raw_stereo, write_wav_pcm16, pad_to_seconds

    samples, sr0, _ = read_wav(args.wav)
    dur = len(samples) / sr0
    if args.grid_k is not None:
        k = args.grid_k
        frames, target = grid_frames(k), grid_seconds(k)
    else:
        k, frames, target = snap_up(dur)

    arr, sr = read_wav_raw_stereo(args.wav)
    out, trimmed = pad_to_seconds(arr, sr, target)
    if trimmed > 0 and not args.force:
        _err(f"音声のほうが {trimmed:.3f} 秒長いため末尾の切り詰めが必要です。"
             "--force で実行します。")
        return 1
    base, _ext = os.path.splitext(args.wav)
    dst = args.out or f"{base}_{frames}f.wav"
    write_wav_pcm16(dst, out, sr)
    note = render_duration_note(os.path.basename(dst), frames)
    note_path = os.path.splitext(dst)[0] + ".txt"
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write(note + "\n")
    _err(f"書き出し: {dst}  ({target:.3f} 秒 / {frames} フレーム / 16bit PCM)")
    _err(f"貼り付け用の数値: {note_path}")
    _err("")
    _err(note)
    return 0


def cmd_scaffold(args):
    tl = _load_timeline(args)
    wav_name = os.path.basename(tl.wav_path) if tl.wav_path else "-"
    n_seg = sum(1 for u in tl.utterances if u.start is not None)
    n_lin = sum(1 for u in tl.utterances if u.text)
    note = ""
    if n_lin and n_seg and n_lin != n_seg:
        note = (f"※ 台詞 {n_lin} 行に対し検出区間 {n_seg} 個。"
                "対応がずれている可能性があるので確認すること。")

    # 参照の説明: コマンドライン指定 > タイムライン JSON に保存された値
    saved = tl.ref_texts or {}
    pics = list(args.pic_desc or saved.get("pictures") or [])
    audio = args.audio_desc or saved.get("audio") or ""
    if pics or audio:
        pics += [""] * (tl.n_images - len(pics))
        print(render_reference_header(pics[:tl.n_images], audio))
        print()

    print(render_scaffold_from_timeline(tl, wav_name, note,
                                        out_lang=args.out_lang))
    print()
    print(render_skeleton_from_timeline(tl, out_lang=args.out_lang))
    _err("")
    _err(render_settings_note())
    return 0


def cmd_substitute(args):
    tl = _load_timeline(args)
    text = _read_input(args.input)
    ts_map = [int(x) for x in args.ts_map.split(",")] if args.ts_map else None
    d_map = [int(x) for x in args.d_map.split(",")] if args.d_map else None
    res = substitute(text, tl, ts_map=ts_map, d_map=d_map,
                     snap_shots=not args.no_snap_shots,
                     force_na=not args.keep_soundscape)
    _err(res.report_text())
    if res.needs_mapping:
        return 2
    if not res.ok:
        return 1
    _write_output(args.out, res.text)
    return 0


def cmd_validate(args):
    tl = None
    if args.timeline or args.wav:
        tl = _load_timeline(args)
    else:
        _err("(タイムラインなし: 書式チェックのみ実行します)")
    text = _read_input(args.input)
    findings = validate(text, tl, expect_na=not args.no_na)
    print(render_report(findings))
    return 1 if counts(findings)[0] else 0


def cmd_compare(args):
    tl = None
    if args.timeline or args.wav:
        tl = _load_timeline(args)
    named = []
    for spec in args.outputs:
        if "=" in spec:
            name, path = spec.split("=", 1)
        else:
            name, path = os.path.splitext(os.path.basename(spec))[0], spec
        with open(path, encoding="utf-8") as fh:
            named.append((name, fh.read()))
    reports = compare_outputs(named, tl, expect_na=not args.no_na)
    print(render_table(reports))
    if not args.quiet:
        print()
        print(render_details(reports))
    return 0


def cmd_settings(_args):
    print(render_settings_note())
    return 0


# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="h3_prompt_toolkit",
        description="MiniMax-H3 強制音声リップシンク用プロンプト組み立てツール (バッチ)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("measure", help="[A] wav から発話タイムラインを実測する")
    m.add_argument("--wav", required=True)
    m.add_argument("--lines", metavar="TXT", help="台詞ファイル (1 行 1 発話)")
    m.add_argument("--lang", default=DEFAULT_LANG)
    m.add_argument("--n-images", type=int, default=2)
    m.add_argument("--grid-k", type=int, default=None)
    m.add_argument("--thresh-db", type=float, default=-40.0)
    m.add_argument("--min-silence-ms", type=int, default=250)
    m.add_argument("--min-speech-ms", type=int, default=120)
    m.add_argument("--pad-ms", type=int, default=40)
    m.add_argument("--save-timeline", metavar="JSON")
    m.add_argument("--json", action="store_true", help="タイムライン JSON を stdout へ")
    m.set_defaults(func=cmd_measure)

    d = sub.add_parser("pad", help="[A] 17k+5 フレーム長へ無音パディングした wav を書き出す")
    d.add_argument("--wav", required=True)
    d.add_argument("--grid-k", type=int, default=None)
    d.add_argument("--out", metavar="WAV")
    d.add_argument("--force", action="store_true", help="音声が長い場合に末尾を切り詰める")
    d.set_defaults(func=cmd_pad)

    s = sub.add_parser("scaffold", help="[B] LLM に渡す固定枠と骨組みを生成する")
    _add_source_args(s)
    s.add_argument("--out-lang", choices=("en", "ja"), default="en",
                   help="LLM 向けテキストの言語 (既定 en。台詞は言語タグのまま)")
    s.add_argument("--pic-desc", action="append", metavar="TEXT",
                   help="参照画像の説明 (画像の枚数だけ繰り返し指定)")
    s.add_argument("--audio-desc", metavar="TEXT", help="<Audio 1> の説明")
    s.set_defaults(func=cmd_scaffold)

    c = sub.add_parser("substitute", help="[C] LLM 出力の時刻と台詞を実測値に差し替える")
    _add_source_args(c)
    c.add_argument("input", nargs="?", metavar="LLM_OUT.txt", help="省略時 stdin")
    c.add_argument("-o", "--out", metavar="FILE", help="省略時 stdout")
    c.add_argument("--ts-map", metavar="1,2,4",
                   help="発話順に対応する \"At\" 出現番号 (0=置換しない)")
    c.add_argument("--d-map", metavar="1,2,3",
                   help="発話順に対応する <d> 出現番号 (0=置換しない)")
    c.add_argument("--no-snap-shots", action="store_true",
                   help="[Shot N] 切り替え時刻の発話境界スナップを行わない")
    c.add_argument("--keep-soundscape", action="store_true",
                   help="overall_soundscape / non_diegetic_music を N/A に強制しない")
    c.set_defaults(func=cmd_substitute)

    v = sub.add_parser("validate", help="[D] 最終プロンプトを機械的に検証する")
    _add_source_args(v)
    v.add_argument("input", nargs="?", metavar="PROMPT.txt", help="省略時 stdin")
    v.add_argument("--no-na", action="store_true",
                   help="soundscape/music の N/A 要求を情報扱いにする")
    v.set_defaults(func=cmd_validate)

    o = sub.add_parser("compare", help="複数モデルの出力を検証して合否を並べる")
    _add_source_args(o)
    o.add_argument("outputs", nargs="+", metavar="NAME=FILE",
                   help="比較する出力 (NAME= は省略可)")
    o.add_argument("--no-na", action="store_true")
    o.add_argument("--quiet", action="store_true", help="合否表のみ (詳細を出さない)")
    o.set_defaults(func=cmd_compare)

    t = sub.add_parser("settings", help="ComfyUI 側と合わせる運用設定を表示する")
    t.set_defaults(func=cmd_settings)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
