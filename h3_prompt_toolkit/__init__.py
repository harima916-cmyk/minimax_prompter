"""h3-prompt-toolkit — MiniMax-H3 強制音声リップシンク用プロンプト組み立てツール群。

仕様は h3_prompt_toolkit_spec.md を参照。ComfyUI 環境には一切依存しない。

    [A] audio / segments / grid  … wav の測定とフレームグリッド
    [B] timeline / scaffold      … 固定枠テキストと骨組みの生成
    [C] substitute               … LLM 出力のタイムスタンプ・台詞の差し替え
    [D] validate / compare       … 最終プロンプトの機械検証とモデル比較
"""

__version__ = "0.1.0"
