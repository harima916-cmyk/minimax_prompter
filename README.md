# minimax_prompter — h3-prompt-toolkit

MiniMax-H3 の**強制音声リップシンク動画**用プロンプトを作るための最小ツール。

やることは 3 つだけ。

1. **手動クリップ** — 波形をドラッグして発話範囲を選び、その範囲の台詞を手で入力する
   (自動検出は意味で区切れないため廃止した。区切るのは人間)
2. **仕様書に追記** — 実測タイムラインを「ブリーフ」にして、リポジトリの
   [minimax_ref2v_rule.txt](minimax_ref2v_rule.txt) (ローカル LLM 用のプロンプト
   生成仕様書) に追記した LLM プロンプトを 1 発コピーで作る
3. **機械修正と検証** — LLM 出力の台詞・時刻を実測値へ差し替え [C] し、検証 [D] する

```
wav ── 手動クリップ ──→ 実測タイムライン
                             │
   minimax_ref2v_rule.txt ＋ ブリーフ ──→ ローカル LLM ──→ Ref2VA プロンプト
                                                              │
                       [C] 差し替え ←──────────────────────────┘
                             │
                       [D] 検証 ──→ ComfyUI の Input Text へ
```

依存は **numpy** (GUI は加えて tkinter) のみ。ComfyUI 環境には一切依存しない。

## GUI

```bash
python -m h3_prompt_toolkit
```

1. **wav を開く** — 波形が表示される (表は空から始まる)
2. **波形をドラッグ**して範囲を選び、**▶ 選択範囲を再生**で聞いて確かめ、
   台詞を入力して**「＋ 選択範囲から行を追加」**。これを発話の数だけ繰り返す
   - 範囲の微調整: 端 (青いつまみ) をドラッグ / 開始・終了欄に数値入力
     (`0:01.234` でも `1.234` 秒でも可、Enter で反映) / ホイールでズーム
   - 台本があるなら「台詞を一括貼り付け…」で行の並びに流し込める
3. **「パディング済み wav を書き出す」** — 17k+5 フレーム長に無音パディングした
   wav と、ComfyUI の Float (Duration) に入れる数値のメモ (`〇〇_141f.txt`。
   小数第 1 位制限用の安全値も併記) が書き出される
4. **「LLM プロンプト」タブ** — 参照画像の枚数ぶん `<Picture N> is …` の説明を
   英語で書き (音声の 1 行は既定文あり)、
   **「仕様書＋ブリーフをまとめてコピー」** → ローカル LLM に貼って実行。
   仕様書はリポジトリの `minimax_ref2v_rule.txt` を自動で見つける
   (別の場所なら「仕様書を開く…」)
5. **「差し替え [C]」タブ** — LLM の出力を貼って実行。`<d>` の台詞を入力どおりに
   復元し、`At M:SS.mmm` があれば実測へ、soundscape/music を N/A に強制する。
   個数が食い違うときだけ対応付けダイアログが開く (無言で捨てない)
6. **「検証 [D]」タブ** — 機械チェックして問題がなければ、結果をコピーして
   ComfyUI の Input Text ノードへ

範囲再生は Windows は標準機能 (winsound)、macOS は afplay、Linux は
paplay / aplay / ffplay のいずれかがあれば使える (無ければ再生ボタンだけ無効)。

タイムラインは JSON に保存/読込できる (参照の説明も一緒に保存される)。

## ブリーフに入るもの

仕様書の §8 (forced-audio / TTS-driven mode) がそのまま読める形で追記する:

- 強制音声ワークフローである宣言 (仕様書がモードを切り替える合図)
- 参照の説明 (`<Picture N> is …` / `<Audio 1> is …`)
- 実効尺 (`5.875 s = 141 frames @ 24 fps (17k+5 grid)`) と無音尾部の長さ
- **実測の逐語トランスクリプト** (`[1] 0:00.512 - 0:02.104 (S1) <d>[Japanese] …</d>`)

## [D] 検証項目

フィールド / 時刻書式 (`M:SS.mmm`) / 台詞の同一性 (句読点改変は警告、言い換えは
エラー) / 単調性 / 尺 / 話者 ID / 参照タグ / ショット構造 / 音響欄 N/A /
実測との一致。検出は報告するだけで、直すのは [C] の仕事。

## 構成

```
h3_prompt_toolkit/
├── audio.py        # RIFF 読み書き (PCM 8/16/24/32bit, IEEE float, EXTENSIBLE)
├── grid.py         # 17k+5 フレームグリッド @ 24fps、Duration の 1 桁安全値
├── clips.py        # 波形エンベロープ / 区間再生 / 行管理
├── timeline.py     # Utterance / Timeline、JSON 入出力
├── scaffold.py     # ブリーフ生成、パディングのメモ (旧: 固定枠/骨組みも残置)
├── ref2va.py       # Ref2VA 出力のパース基盤 (フィールド / 時刻 / <d> / [Shot N])
├── substitute.py   # [C] 差し替え
├── validate.py     # [D] 検証
├── segments.py     # (廃止) RMS 自動検出。GUI からは外した。CLI/ライブラリに残置
├── compare.py      # モデル比較 (CLI のみ)
├── cli.py          # バッチ用 (measure / pad / scaffold / substitute / validate / compare)
└── gui.py          # Tkinter (最小構成)
tests/
└── fixtures/       # LLM 出力の実例を蓄積して回帰テストにする
```

単一ファイルの旧版 `h3_audio_prompter.py` と仕様書
[h3_prompt_toolkit_spec.md](h3_prompt_toolkit_spec.md) もそのまま残してある。

## テスト

```bash
python -m unittest discover -s tests
```
