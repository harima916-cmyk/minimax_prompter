# h3-prompt-toolkit 仕様書

MiniMax-H3 の強制音声リップシンク動画を作るための、プロンプト組み立てツール群。

## 前提となる外部要素

| 要素 | 役割 |
|---|---|
| IrodoriTTS | 台詞音声 (wav) を生成。テキストとタイミングは出力されないので、テキストは手入力、タイミングは波形から測定する |
| `pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI` | ComfyUI 側のプロンプト生成ノード群。Prompt Writer (Ref2VA) / Rewriter Omni / 各種 Caption |
| R2V 強制音声ワークフロー | `MiniMaxH3ReferenceToVideo` + `LTXVSeparateAVLatent` / `SetLatentNoiseMask` で音声 latent を凍結して生成 |

このツール群は ComfyUI の外で動く。ComfyUI 環境には一切依存させない。

## 全体の流れ

```
wav + 台詞テキスト
      ↓  [A] 測定
実測タイムライン (発話区間 + 話者 + 台詞)
      ↓
      ├─→ [B] 固定枠テキスト ──→ ComfyUI の Writer/Rewriter ノードへ手貼り
      │                                    ↓ Ref2VA 出力 (6 フィールド)
      └─────────────────────→ [C] 差し替え ←┘
                                    ↓
                              [D] 検証
                                    ↓
                        最終プロンプト → Input Text ノード
```

## 現状

`h3_audio_prompter.py` に [A] [B] が実装済み。単一ファイルの Tkinter アプリ。
依存は numpy と tkinter のみ。以下を含む。

- 自前の RIFF パーサ (PCM 8/16/24/32bit, IEEE float 32/64, WAVE_FORMAT_EXTENSIBLE 対応)
- RMS ベースの発話区間検出 (10ms hop / 25ms窓、しきい値・無音長を可変)
- フレームグリッド計算 (`17k+5` フレーム @ 24fps) と無音パディング wav の書き出し
- 固定枠テキストと Ref2VA 骨組みの生成

**このロジックは動作確認済みなので、書き直さず移植すること。**

## これから作るもの

### [C] 差し替えパス

LLM が生成した Ref2VA 出力を受け取り、タイムスタンプと台詞を実測値に置換する。

書き換え器は 6 フィールドを一気に書き切るよう学習されているため、
「時刻を空けておけ」という指示は効かない。生成させてから機械的に直すのが確実。

**置換対象**

1. `At M:SS.mmm,` 形式のタイムスタンプ → 実測の発話開始時刻
2. `<d>[Lang] ... </d>` の中身 → 手入力した台詞そのもの
3. `[Shot N] At M:SS.mmm,` のショット切り替え時刻 → 発話境界にスナップ (要検討)

**対応付け**

出現順に 1:1 で対応させる。個数が食い違う場合は自動で捨てず、
差分を提示して人間に選ばせる。無言で辻褄を合わせるのが一番危険。

**注意**

- `<d>` の外に書かれた話者同定情報 (音域・音色・画面内か否か) は触らない
- `overall_soundscape` / `non_diegetic_music` は `N/A` に強制する
  (音声 latent が凍結されているので出力音声に効かず、映像側の条件付けを濁らせるだけ)

### [D] 検証パス

最終プロンプトを機械的にチェックする。モデル選定の判定にもそのまま使う。

| 項目 | 内容 |
|---|---|
| フィールド | `subject_definitions` / `summary` / `retention_analysis` / `detailed_description` / `overall_soundscape` / `non_diegetic_music` が揃っているか |
| 時刻書式 | すべて `M:SS.mmm` (ミリ秒3桁固定) か |
| 台詞の同一性 | `<d>` の中身が入力と完全一致するか。翻訳・要約・句読点の改変を検出 |
| 単調性 | タイムスタンプが昇順か |
| 尺 | 最終時刻が動画長を超えていないか |
| 話者 ID | `S1`/`S2` の対応が入力と一致するか |
| 参照タグ | `<Picture N>` / `<Audio 1>` が実際の接続数と整合するか |

### モデル比較モード

同一入力に対する複数モデルの出力を [D] にかけ、項目ごとの合否を並べて表示する。

想定する比較対象:

- 無検閲 9B GGUF (Q8) — 本命。Prompt Writer (Ref2VA) に挿す
- 素の Qwen3.5-9B-Instruct — 書式追従の基準線
- Rewriter Omni (Qwen2.5-Omni-7B + LoRA) — Ref2VA で実際に学習された唯一の選択肢。答え合わせ用

## モジュール構成案

```
h3_prompt_toolkit/
├── audio.py        # RIFF 読み書き、リサンプル不要
├── segments.py     # 発話区間検出
├── grid.py         # 17k+5 フレームグリッド、パディング
├── timeline.py     # Utterance / Timeline データ構造、台詞テキストのパース
├── scaffold.py     # 固定枠テキストと骨組みの生成
├── substitute.py   # [C] 差し替え
├── validate.py     # [D] 検証
├── compare.py      # モデル比較
├── cli.py          # バッチ用
└── gui.py          # Tkinter。既存 UI を踏襲
tests/
└── fixtures/       # 実際の LLM 出力を蓄積し、回帰テストにする
```

## データ構造

```python
@dataclass
class Utterance:
    index: int
    start: float | None   # 秒。未検出は None
    end: float | None
    speaker: str          # "S1"
    text: str             # 台詞そのもの
    lang: str             # "Japanese"

@dataclass
class Timeline:
    utterances: list[Utterance]
    total_sec: float      # グリッドにスナップ済み
    frames: int
    wav_path: str
    n_images: int
```

## 設定として持たせるもの

ComfyUI 側の設定と食い違うと事故になるので、ツール側に持たせて出力に併記する。

- `n_ctx`: 16k 以上を推奨 (公式ガイド + キャプション + タイムラインで 7680 では溢れる)
- `temperature`: 0.2 前後から。構造化出力なので高くしない
- fps: 24 固定
- 言語タグ: `Japanese` 既定

## テスト方針

- グリッド計算と時刻整形は純関数なので単体テストで固める
- 区間検出は合成波形 (トーン + 無音) で回帰テストを書く。実 wav はリポジトリに入れない
- 差し替えと検証は `tests/fixtures/` に実際の LLM 出力を貯めて、
  崩れ方のパターンごとにケースを増やす。ここが一番育つ場所

## 非目標

- 音声認識・強制アラインメントは行わない。台詞は手入力する前提
- ComfyUI のカスタムノード化はしない。単体 GUI とコピペで完結させる
- 動画生成そのものには関与しない
