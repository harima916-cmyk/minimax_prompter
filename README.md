# minimax_prompter — h3-prompt-toolkit

MiniMax-H3 の**強制音声リップシンク動画**用プロンプトを作るための最小ツール。

やることは 3 つだけ。

1. **手動クリップ** — 波形をドラッグして発話範囲を選び、その範囲の台詞を手で入力する
   (自動検出は意味で区切れないため廃止した。区切るのは人間)
2. **仕様書に追記して LLM へ** — 実測タイムラインを「ブリーフ」にして、
   [minimax_ref2v_rule.txt](minimax_ref2v_rule.txt) (ローカル LLM 用の仕様書) に
   追記し、**LM Studio へワンボタンで送る**(コピペと画像添付も残してある)
3. **機械修正と検証** — LLM 出力の台詞・時刻を実測値へ差し替え [C] し、検証 [D] する

```
wav ── 手動クリップ ──→ 実測タイムライン
                             │
   minimax_ref2v_rule.txt ＋ ブリーフ ＋ 参照画像
                             │  ▶ LM Studio (Qwen3.8-27B)
                             ▼
                       [C] 差し替え → [D] 検証 → ComfyUI の Input Text へ
```

依存は **numpy** (GUI は加えて tkinter) のみ。LM Studio 連携も標準ライブラリだけ。
ComfyUI 環境には一切依存しない。

改善方針は [docs/spec_v2.md](docs/spec_v2.md)。

## GUI

```bash
python -m h3_prompt_toolkit
```

1. **wav を開く** — 波形が表示される (表は空から始まる)
2. **波形をドラッグ**して範囲を選び、**▶ 選択範囲を再生**で聞いて確かめ、
   台詞を入力して**「＋ 選択範囲から行を追加」**。これを発話の数だけ繰り返す
   - 範囲の微調整: 端 (青いつまみ) をドラッグ / 開始・終了欄に数値入力
     (`00:01.234` でも `1.234` 秒でも可、Enter で反映) / ホイールでズーム
   - 台本があるなら「台詞を一括貼り付け…」で行の並びに流し込める
3. **「パディング済み wav を書き出す」** — 17k+5 フレーム長に無音パディングした
   wav と、ComfyUI の Float (Duration) に入れる数値のメモ (`〇〇_141f.txt`) が
   書き出される。**案内される値は切り捨て 1 桁**(v2 の `ceil(a*24)` では
   真値 3 桁が誤差で次の枠に飛ぶため)
4. **「LLM プロンプト」タブ**
   - 参照ごとに `<Picture N> is …` の説明を英語で書き、「画像…」で実ファイルを指定
   - **どういう動画にしたいか**(シナリオ)を日本語で書く
   - ワークフローで `MiniMaxH3AddGuide` を使うなら **First-frame anchor** を ON
   - **「▶ LM Studio に送って [C][D] まで実行」** — 仕様書を system、ブリーフと
     画像を user にして送り、応答をそのまま差し替え → 検証まで通す
     (送信先とモデル名は「送信先…」で変更、`settings.json` に保存)
   - 手貼りしたいときは「仕様書＋ブリーフをまとめてコピー」
5. **「差し替え [C]」** — `<think>`・コードフェンス・前置きを剥がし、`<d>` の台詞を
   入力どおりに復元、`At M:SS.mmm` を実測へ、時刻の書式を `MM:SS.mmm` に統一、
   soundscape/music を N/A に強制。個数が食い違うときだけ対応付けダイアログが開く
6. **「検証 [D]」** — 機械チェックして問題がなければ、結果をコピーして
   ComfyUI の Input Text ノードへ

範囲再生は Windows は標準機能 (winsound)、macOS は afplay、Linux は
paplay / aplay / ffplay のいずれかがあれば使える。

タイムラインは JSON に保存/読込できる (参照の説明・画像パス・シナリオ・
anchor 設定も一緒に保存される)。

## ブリーフに入るもの

仕様書の §8 (forced-audio / TTS-driven mode) がそのまま読める形で追記する:

- ワークフロー名と、強制音声ワークフローである宣言 (モード切替の合図)
- First-frame anchor の ON/OFF
- シナリオ (書いた内容そのまま)
- 参照の説明と、添付画像の実ファイル名
- 実効尺 (`5.875 s = 141 frames @ 24 fps (17k+5 grid)`)、TTS 元尺、無音尾部、
  `Duration is already fixed by the toolkit; do not suggest one.`
- **実測の逐語トランスクリプト** (`[1] 00:00.512 - 00:02.104 (S1) <d>[Japanese] …</d>`)

## [D] 検証項目

| 項目 | 内容 |
|---|---|
| フィールド | Ref2VA の 6 フィールドが揃っているか |
| 出力の包み | `<think>` / コードフェンスの残り (ERROR)、前置き (WARN) |
| タスク種別 | `[...]` の角括弧と `audio reuse` があるか |
| 音声マーカー | `<Audio 1>: fully_copy` か |
| 時刻書式 | `M:SS.mmm` / `MM:SS.mmm` か。`<d>` タグの均衡 |
| 台詞の同一性 | `<d>` が入力と完全一致か (句読点改変は警告、言い換えはエラー) |
| 単調性 / 尺 | 昇順か。実効尺を超えていないか |
| 話者ID / 参照タグ | 入力の話者、接続した画像・音声と整合するか |
| ショット構造 | `[Shot 1]` は時刻なし、以降は `At` 付き、番号が連続か |
| 音響欄 N/A | soundscape / music が `N/A` か |
| 実測との一致 | `At` 時刻が実測の発話開始と一致するか |
| 解像度非依存 | `768p` `0.4 MP` `draft` など、精修パスで使い回せなくなる語 |
| アンカー整合 | anchor ON なら `keyframe completion` と `<Picture 1>` の保持 |
| 否定語 | `no` `not` `without` など (否定分岐が無いので逆効果) |
| 軟化語 | `soft focus` `bokeh` `motion blur` など (口元が潰れる) |
| 描写の長さ | 200 語未満 / 600 語超 (目安 350-500) |
| リップシンク句 | 発話があるのに `synchroniz` の一文が無い (情報) |

検出は報告するだけで、直すのは [C] の仕事。

## 構成

```
h3_prompt_toolkit/
├── audio.py        # RIFF 読み書き (PCM 8/16/24/32bit, IEEE float, EXTENSIBLE)
├── grid.py         # 17k+5 グリッド、ceil/round 丸め、Duration の 1 桁安全値
├── clips.py        # 波形エンベロープ / 区間再生 / 行管理
├── timeline.py     # Utterance / Timeline、JSON 入出力
├── scaffold.py     # ブリーフ生成、パディングのメモ、運用設定
├── ref2va.py       # Ref2VA 出力のパース基盤と包み剥がし
├── substitute.py   # [C] 差し替え
├── validate.py     # [D] 検証
├── llm_client.py   # LM Studio (OpenAI 互換 API) 送信
├── compare.py      # モデル比較 (CLI のみ)
├── cli.py          # バッチ用 (pad / scaffold / substitute / validate / compare / settings)
└── gui.py          # Tkinter (最小構成)
docs/spec_v2.md     # 改善仕様書 v2
legacy/             # 旧版 (単一ファイル GUI と、廃止した RMS 自動検出)
tests/
└── fixtures/qwen38/  # Qwen3.8 の実出力を貯めて回帰テストにする
```

## テスト

```bash
python -m unittest discover -s tests
```

`tests/fixtures/qwen38/` に実出力 (.txt) と Timeline (.json) を同名で置くと、
`[C] → [D] でエラー 0` が自動で確認される。置き方は同ディレクトリの README を参照。
