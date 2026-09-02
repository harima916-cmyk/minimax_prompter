# h3-prompt-toolkit 改善仕様書 v2

`harima916-cmyk/minimax_prompter` を、ComfyUI ワークフロー
`video_minimax_h3_r2v_forced_audio_lora_v2` と LM Studio + Qwen3.8-27B の
運用に合わせて更新するための仕様。旧 `h3_prompt_toolkit_spec.md`（Rewriter
ノード＋9B モデル前提の初期計画）はこの文書で置き換える。

---

## 0. 前提

| 要素 | 現在の役割 |
|---|---|
| ComfyUI ワークフロー v2 | `Float (Duration)` → `ceil(a*24)` を 17k+5 に切り上げ。音声 latent は mask=0 で凍結。任意で精修パス（同じプロンプトを 0.9MP で再利用）と First-frame anchor（`MiniMaxH3AddGuide`） |
| 仕様書 `minimax_ref2v_rule.txt` v2 | LLM のシステムプロンプト。強制音声モードは `audio reuse` / `fully_copy` を FIXED、精修パス向けに解像度非依存、anchor ON 時は `keyframe completion` |
| LLM | LM Studio + Qwen3.8-27B（vision。参照画像 2 枚を添付）。reasoning_effort low〜medium、temp 0.7 / top_p 0.9 |
| このツール | 波形から実測タイムラインを作り、仕様書＋ブリーフを組み立て、LLM 出力を機械修正 [C] して検証 [D] する。ComfyUI には依存しない |

**変わらない方針**: 台詞は人間が入力する（音声認識はしない）。個数が食い違ったら黙って捨てず人間に選ばせる。検証は報告するだけで直すのは [C]。

---

## 1. 適用済み（パッチ 0001）

以下は実装・テスト済み。この文書では「前提」として扱う。

| # | 内容 | 変更箇所 |
|---|---|---|
| A1 | `template_frames(seconds, rounding="ceil")`。既定 ceil（v2）、`"round"` で公式テンプレート | `grid.py`, `tests/test_grid.py` |
| A2 | Duration の案内を切り捨て 1 桁値のみに統一（真値 3 桁の四捨五入は ceil で次の枠に飛ぶ） | `scaffold.render_duration_note`, `gui.py` |
| A3 | `ref2va.strip_wrappers()`: `<think>` ブロック・コードフェンス・先頭の前置きを除去。[C] の先頭で自動適用、[D] は残存をエラー | `ref2va.py`, `substitute.py`, `validate.py` |
| A4 | [D] に `task_type`（`audio reuse` 必須）、`audio_marker`（`<Audio 1>: fully_copy` 必須）、`resolution`（解像度・下書き語の警告）を追加 | `validate.py` |
| A5 | ブリーフにワークフロー名と First-frame anchor ON/OFF 行を追加。GUI にチェックボックス、JSON に `ref_texts.anchor` | `scaffold.render_brief`, `gui.py` |
| A6 | 旧 skeleton の `<Audio 1>: audio reuse — fully_preserved` を `fully_copy` に修正、summary を `[reference generation + audio reuse]` に | `scaffold.py` |
| A7 | `RECOMMENDED_SETTINGS` を Qwen3.8 前提に（model / temp 0.7 / top_p / reasoning_effort） | `scaffold.py`, README |
| A8 | 同梱 `minimax_ref2v_rule.txt` を v2 に差し替え | ルート |

---

## 2. 未適用の改善（優先度順）

各項目は「変更 / 理由 / 受け入れ条件」の 3 点で書く。受け入れ条件は
そのままテストにする。

### P1. 時刻書式を公式ガイドの `MM:SS.mmm` に揃える

**変更**: `timeline.fmt_ts` の分を 2 桁ゼロ埋め（`0:03.500` → `00:03.500`）。
`ref2va.TS_STRICT` は既に両方を受けるので変更不要。

**理由**: 公式プロンプトガイドと ComfyUI テンプレートの例は全て `00:03.000`
形式。H3 のテキストエンコーダは書式をトークンとして読むので、学習分布に
寄せておく方が安全。今は仕様書（`00:`）とツール出力（`0:`）が食い違っている。

**受け入れ条件**:
- `fmt_ts(3.5) == "00:03.500"`、`fmt_ts(65.0) == "01:05.000"`
- [C] で差し替えた時刻が `MM:SS.mmm` になる（`test_substitute` の期待値を更新）
- 既存フィクスチャ（`0:` 形式）は [D] で引き続きエラーにならない

### P2. anchor ON 時の [D] チェック

**変更**: `validate()` が `tl.ref_texts.get("anchor")` を読み、True のとき:
- `summary` のタスク種別に `keyframe completion` が無ければ ERROR
- `retention_analysis` に `<Picture 1>` の独立行があり `fully_preserved` を
  含まなければ ERROR
- `detailed_description` の `[Shot 1]` 直後 200 字以内に `begins from
  <Picture 1>` 系の句（`begins from` / `starts from` / `opens on`）が
  無ければ WARN
- anchor OFF のとき `keyframe completion` があれば WARN（仕様書のモードと
  ワークフローの設定が食い違っている）

**理由**: 仕様書 v2 は anchor ON の書き方を定めたが、ツール側で検証されない。
ワークフローで AddGuide を有効にしたのにプロンプトが `reference generation`
のままだと、キーフレームと参照が矛盾する。

**受け入れ条件**: `CHECKS` に `("anchor", "アンカー整合")` を追加。
フィクスチャ `ref2va_anchor_on.txt` を作り、anchor=True の Timeline で ok、
anchor=False で WARN になること。

### P3. 仕様書 §6 / §10 の機械チェックを [D] に追加

**変更**: 全て WARN、`detailed_description` の `<d>` 外を対象:
- `negation`: `\b(no|not|never|without|don't|do not|avoid)\b` → 「否定語」
  （仕様書: 否定は逆効果。`N/A` の escape hatch は対象外）
- `softness`: `shallow depth of field|soft focus|bokeh|dreamy|hazy|misty|
  diffused light|motion blur|vintage film|Instagram Live` → 「軟化語」
- `length`: `detailed_description` の英単語数が 200 未満 / 600 超で WARN
  （仕様書の目安 350-500）
- `lipsync_cue`: 発話があるのに `synchroniz` を含む文が無ければ INFO

**理由**: 仕様書のセルフチェック 6 / 10b / 語数は LLM に頼っているが、
機械で見られる。特に否定語は「言うと出てくる」ので事故が大きい。

**受け入れ条件**: good フィクスチャは全て ok。各語を 1 つ混ぜたフィクスチャで
該当チェックだけ WARN になる。

### P4. LM Studio へ直接送る（コピペと画像添付をなくす）

**変更**: LLM プロンプトタブに「LM Studio に送る」ボタン。
- LM Studio の OpenAI 互換 API（既定 `http://localhost:1234/v1/chat/completions`）
  に、system = 仕様書全文、user = ブリーフ ＋ 参照画像（base64、`image_url`）
  で POST。依存は標準ライブラリ（`urllib`, `base64`, `json`）のみ
- 参照画像のパスは LLM プロンプトタブに「画像 N を選ぶ…」で指定し、
  Timeline JSON の `ref_texts.image_paths` に保存
- `RECOMMENDED_SETTINGS` の temperature / top_p を送る。`reasoning_effort` は
  リクエストの `reasoning_effort` フィールドで送る（LM Studio が無視しても可）
- 応答は [C] タブの入力欄に入れ、そのまま [C] → [D] を自動実行
- 失敗（接続不可・非 200）は messagebox で表示し、従来のコピー経路は残す
- エンドポイント URL とモデル名は `settings.json`（ツール直下）に保存

**理由**: 今の運用は「まとめてコピー → LM Studio に貼る → 画像 2 枚を手で添付
→ 出力をコピー → [C] に貼る」で、画像添付の抜けと貼り忘れが一番起きやすい。
ワンボタンにすると [C][D] まで一気に通る。

**受け入れ条件**:
- `llm_client.py` を新設。`build_request(spec, brief, image_paths, settings)`
  は純関数で、画像が無い場合はテキストのみの messages を返す（単体テスト）
- 実 API を叩くテストは書かない。`send()` は `urllib` を注入可能にして
  モックで 200 / 接続失敗の両方を確認
- GUI からの呼び出しは別スレッドで行い、UI を固めない

### P5. ブリーフに画像ファイル名と TTS 元尺を入れる

**変更**: `render_brief` に
- `Reference images: <Picture 1> = chr1047_face.png, <Picture 2> = …`
  （P4 の image_paths があれば自動、無ければ省略）
- `TTS length before padding: 4.812 s`（パディング前の wav 長）

**理由**: 画像名は LLM が添付画像とラベルを取り違えたときの手がかりになる。
TTS 元尺は仕様書 v2 の「Duration 提案」節が使うが、ツールを通す場合は
Duration が既に決まっているので、同時に
`Duration is already fixed by the toolkit; do not suggest one.` を 1 行入れて
LLM の余計な assumption 行を抑える。

**受け入れ条件**: `test_scaffold` に両行の有無を確認するケースを追加。

### P6. `h3_prompt_toolkit_spec.md` の整理

**変更**: 旧仕様書を削除し、本文書を `docs/spec_v2.md` として置く。README の
「構成」節から旧ファイルへのリンクを外す。`segments.py`（廃止済み RMS 検出）
と `h3_audio_prompter.py`（単一ファイル旧版）は `legacy/` に移動し、
`cli.py` の `measure` サブコマンドから外す（`pad` 以降は残す）。

**理由**: README と旧仕様書の記述（Rewriter ノード、9B モデル、自動検出）が
食い違っていて、次に読む人（将来の自分を含む）が迷う。

**受け入れ条件**: `python -m unittest discover -s tests` が通る。
`python -m h3_prompt_toolkit.cli --help` に `measure` が出ない。

### P7. Qwen3.8 の実出力をフィクスチャに蓄積する

**変更**: `tests/fixtures/qwen38/` を作り、実際の LLM 出力（[C] 前の生テキスト）
を日付付きで保存。各ファイルに対応する Timeline JSON も置き、
`test_fixtures_qwen38.py` が「[C] → [D] でエラー 0」を全件に対して確認する。

**理由**: これが README の言う「一番育つ場所」。Qwen3.8 特有の崩れ方
（フェンス、`<think>` 残り、`Assumption:` 行の位置、`00:` と `0:` の混在）は
実物でしか捕まえられない。

**受け入れ条件**: フィクスチャが 0 件でもテストが通る（skip ではなく空で成功）。
1 件でもエラーが出たら、そのファイル名と [D] レポートを表示して失敗する。

---

## 3. 触らないもの

- 台詞の手入力方式（音声認識・強制アラインメントは入れない）
- ComfyUI カスタムノード化（単体 GUI とコピペ／API で完結させる）
- `substitute.py` の対応付けロジック（個数不一致は人間に選ばせる）
- `audio.py` の RIFF 実装（動作確認済み。触る理由がない）

---

## 4. 実装順

P1 → P2 → P3 は `validate.py` / `timeline.py` だけで完結し、それぞれ 1 コミット。
P4 は新規モジュールなので独立して進められる。P5 は P4 の image_paths に
依存するので後。P6 / P7 は最後。

各コミットで `python -m unittest discover -s tests` を通し、フィクスチャを
足したら README の [D] 検証項目一覧を更新する。
