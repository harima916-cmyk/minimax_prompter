# minimax_prompter — h3-prompt-toolkit

MiniMax-H3 の**強制音声リップシンク動画**用プロンプトを組み立てるツール群。
仕様は [h3_prompt_toolkit_spec.md](h3_prompt_toolkit_spec.md) を参照。

wav の波形から発話タイミングを**実測**し、LLM にはタイムスタンプを推測させない。
LLM が書き崩した時刻・台詞は生成後に**機械的に差し替え**、最終プロンプトを**機械検証**する。

```
wav + 台詞テキスト
      ↓  [A] 測定            (audio / segments / grid)
実測タイムライン
      ↓
      ├─→ [B] 固定枠テキスト  (scaffold) ──→ ComfyUI の Writer/Rewriter へ手貼り
      │                                          ↓ Ref2VA 出力 (6 フィールド)
      └──────────────────────→ [C] 差し替え (substitute) ←┘
                                    ↓
                              [D] 検証 (validate / compare)
                                    ↓
                        最終プロンプト → Input Text ノード
```

依存は **numpy** (GUI は加えて tkinter) のみ。ComfyUI 環境には一切依存しない。

## 使い方

### GUI — 手動クリップ方式

```bash
python -m h3_prompt_toolkit
```

台詞の入力は「**波形から自分で範囲を選んで、その範囲の文字を打つ**」方式。
右側のタブは 固定枠 / 骨組み / **差し替え [C] / 検証 [D] / モデル比較 / 運用設定**。

1. **wav を開く** — 波形が表示され、自動検出の区間が*下書きの行*として表に入る
   (台詞は空。自動検出はあくまで補助で、行はすべて手で直せる)
2. **行をクリック → ▶ 選択範囲を再生** で何を言っているか確かめ、
   下の「台詞」欄に文字を打って **「選択行を更新」**
3. 区間の調整は 3 通り:
   **選択範囲の端 (青いつまみ) をドラッグ**して微調整 /
   波形を新しくドラッグして選び直し /
   「開始」「終了」欄に数値を直接入力 (`0:01.234` でも `1.234` 秒でも可。
   Enter で波形に反映)。直したら「選択行を更新」。
   検出漏れは範囲をドラッグ →「**＋ 選択範囲から行を追加**」。
   ホイールでズーム、クリックで行選択。話者は行ごとに S1/S2… を指定
4. 台本が手元にあるなら「**台詞を一括貼り付け…**」で行の並びに流し込める
5. 右側の「LLM に渡す固定枠」タブの **「固定枠＋骨組みをまとめてコピー」** で
   コピーして ComfyUI 側の LLM へ。骨組みには発話 1 つにつき 1 行の
   `<d>` が入っているので、必ず両方セットで貼る (固定枠だけだと LLM が
   台詞を結合・省略して話数が合わなくなる)
6. LLM の出力を「**差し替え [C]**」タブに貼って実行
   — 個数が食い違うときは対応付けダイアログが開く (無言で捨てない)
7. 「**検証 [D]**」で機械チェックし、問題がなければ Input Text ノードへ

範囲再生は Windows では標準機能 (winsound) で鳴る。macOS は afplay、
Linux は paplay / aplay / ffplay のいずれかがあれば使われる (無ければ再生
ボタンだけ無効になり、他の機能はそのまま使える)。

「パディング済み wav を書き出す」は、wav と同じ場所に **`〇〇_141f.txt`**
(貼り付け用メモ) も書き出す。ComfyUI の Float (Duration) に入れる秒数と、
Float ウィジェットが小数第 1 位までしか受けない場合の安全値
(切り捨て 1 桁。公式テンプレートの Math Expression が同じフレーム数に
丸め上げることを保証した値)、length 直接入力用のフレーム数が書いてある。

### CLI (バッチ)

```bash
# [A] 実測してタイムラインを保存
python -m h3_prompt_toolkit.cli measure --wav voice.wav --lines lines.txt --save-timeline tl.json

# [A] 17k+5 フレーム長へ無音パディングした wav を書き出す
python -m h3_prompt_toolkit.cli pad --wav voice.wav

# [B] LLM に渡す固定枠と骨組み (stdout)。運用設定は stderr に併記
python -m h3_prompt_toolkit.cli scaffold --timeline tl.json

# [C] LLM 出力の時刻・台詞を実測値へ差し替え
python -m h3_prompt_toolkit.cli substitute --timeline tl.json llm_output.txt -o final.txt
#   個数不一致なら exit 2 で差分を提示 → --ts-map 1,3 --d-map 1,2 のように人間が対応を指定

# [D] 最終プロンプトの機械検証 (エラーがあれば exit 1)
python -m h3_prompt_toolkit.cli validate --timeline tl.json final.txt

# モデル比較 (同一入力に対する複数モデルの出力を項目別に採点)
python -m h3_prompt_toolkit.cli compare --timeline tl.json 9b=out_9b.txt omni=out_omni.txt
```

`compare` の出力例:

```
項目          9b    omni
------------------------
フィールド    OK    OK
台詞の同一性  OK    NG
音響欄 N/A    OK    NG
実測との一致  OK    warn
------------------------
エラー/警告   0/0   2/3
```

## [C] 差し替えパスがやること

- `At M:SS.mmm,` のプレーンな時刻 → 実測の発話開始時刻へ (出現順 1:1)
- `<d>[Lang] …</d>` の中身 → 手入力した台詞そのものへ復元
- `[Shot N] At M:SS.mmm,` のショット切り替え時刻 → 最寄りの発話境界へスナップ
- `overall_soundscape` / `non_diegetic_music` → `N/A` に強制
  (音声 latent が凍結されているので出力音声に効かず、映像側の条件付けを濁らせるだけ)
- 個数が食い違う場合は**自動で捨てず**、差分を提示して人間に選ばせる
- `<d>` の外の話者同定情報 (音域・音色など) は触らない (不一致は報告のみ)

## [D] 検証項目

| 項目 | 内容 |
|---|---|
| フィールド | Ref2VA の 6 フィールドが揃っているか |
| 時刻書式 | すべて `M:SS.mmm` (ミリ秒 3 桁固定) か。`<d>` タグの均衡 |
| 台詞の同一性 | `<d>` の中身が入力と完全一致するか (句読点改変は警告、言い換えはエラー) |
| 単調性 | タイムスタンプが昇順か |
| 尺 | 最終時刻が動画長を超えていないか |
| 話者 ID | `S1`/`S2` の対応が入力と一致するか |
| 参照タグ | `<Picture N>` / `<Audio 1>` が実際の接続数と整合するか |
| ショット構造 | `[Shot 1]` は時刻なし、以降は `At` 付き、番号が連続か |
| 音響欄 N/A | soundscape / music が `N/A` のままか |
| 実測との一致 | `At` 時刻が実測の発話開始と一致するか (差し替えで修正可) |

## 構成

```
h3_prompt_toolkit/
├── audio.py        # RIFF 読み書き (PCM 8/16/24/32bit, IEEE float, EXTENSIBLE)
├── segments.py     # RMS ベースの発話区間検出 (10ms hop / 25ms 窓)
├── grid.py         # 17k+5 フレームグリッド @ 24fps
├── timeline.py     # Utterance / Timeline、台詞パース、JSON 入出力
├── scaffold.py     # [B] 固定枠と骨組みの生成、運用設定の併記
├── ref2va.py       # Ref2VA 出力のパース基盤 (フィールド / 時刻 / <d> / [Shot N])
├── substitute.py   # [C] 差し替え
├── validate.py     # [D] 検証
├── compare.py      # モデル比較
├── clips.py        # 手動クリップ方式の支援 (波形エンベロープ / 区間再生 / 行管理)
├── cli.py          # バッチ用
└── gui.py          # Tkinter (波形クリップ編集 + 各タブ)
tests/
└── fixtures/       # LLM 出力の実例を蓄積して回帰テストにする
```

[A][B] のロジックは動作確認済みの `h3_audio_prompter.py` から**書き直さず移植**したもの。
単一ファイル版もそのまま残してある。

## テスト

```bash
python -m unittest discover -s tests
```

`tests/fixtures/` に実際の LLM 出力を貯めて、崩れ方のパターンごとにケースを増やす。

## 参考資料

- [pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI](https://github.com/pytraveler/MiniMax-H3-Prompt-Rewriter-ComfyUI)
  — ComfyUI 側のプロンプト生成ノード群。6 フィールド構成と self-check の参照実装
- KiraNugget's Minimax H3 Auto-Prompter ワークフロー (minimaxh3AutoPrompter)
  — Ref2VA の書式ルール (時刻書式 / ショット構文 / カメラ語彙 / retention 語彙) の出典
