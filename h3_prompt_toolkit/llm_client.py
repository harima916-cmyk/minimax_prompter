# -*- coding: utf-8 -*-
"""LM Studio (OpenAI 互換 API) へプロンプトを送る。

依存は標準ライブラリのみ (urllib / base64 / json)。組み立ては純関数に
してテストできるようにし、通信は send() に閉じ込めて注入で差し替える。

    req = build_request(spec, brief, image_paths, settings)
    text = send(req, endpoint)          # 応答の本文だけを返す

コピペ運用 (仕様書＋ブリーフをコピー → LM Studio に貼る → 画像を添付)
の置き換えなので、system に仕様書、user にブリーフ＋参照画像を載せる。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://localhost:1234/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-27b"
DEFAULT_TIMEOUT = 600      # 27B は生成に数分かかることがある

SETTINGS_FILENAME = "settings.json"

# 送る画像の上限 (LM Studio 側のメモリと転送量の保険)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class LLMError(RuntimeError):
    """接続・HTTP・応答形式のいずれかで失敗した。"""


# ---------------------------------------------------------------------------
# 設定 (ツール直下の settings.json)
# ---------------------------------------------------------------------------

def settings_path(base_dir=None) -> str:
    base = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, SETTINGS_FILENAME)


def load_settings(base_dir=None) -> dict:
    """保存済みの設定。無ければ既定値。"""
    out = {"endpoint": DEFAULT_ENDPOINT, "model": DEFAULT_MODEL}
    path = settings_path(base_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            out.update({k: v for k, v in saved.items() if isinstance(k, str)})
    except (OSError, ValueError):
        pass
    return out


def save_settings(values: dict, base_dir=None) -> str:
    path = settings_path(base_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(values, fh, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# リクエストの組み立て (純関数)
# ---------------------------------------------------------------------------

def encode_image(path: str) -> str:
    """画像を data URL にする。読めない・大きすぎるときは LLMError。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise LLMError(f"画像を読めません: {path} ({exc})") from exc
    if size > MAX_IMAGE_BYTES:
        raise LLMError(f"画像が大きすぎます ({size / 1e6:.1f} MB): {path}")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_request(spec: str, brief: str, image_paths=(), settings=None) -> dict:
    """chat/completions のリクエスト本体を組み立てる。

    画像が無ければ user は素のテキスト (vision でないモデルでも通る)。
    画像があれば OpenAI 互換の content 配列にし、text を先頭に置く
    (画像だけ先に来ると「何をする画像か」が分からないため)。
    """
    s = dict(settings or {})
    body = {
        "model": s.get("model", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": spec},
            {"role": "user", "content": brief},
        ],
        "stream": False,
    }
    for key in ("temperature", "top_p", "max_tokens"):
        if s.get(key) is not None:
            body[key] = s[key]
    if s.get("reasoning_effort"):
        # LM Studio が解さなければ無視されるだけなので、そのまま送る
        body["reasoning_effort"] = s["reasoning_effort"]

    paths = [p for p in (image_paths or []) if p]
    if paths:
        content = [{"type": "text", "text": brief}]
        for i, path in enumerate(paths, 1):
            content.append({
                "type": "image_url",
                "image_url": {"url": encode_image(path),
                              "detail": s.get("image_detail", "high")},
            })
        body["messages"][1]["content"] = content
    return body


def extract_text(payload: dict) -> str:
    """応答から本文を取り出す。取り出せない形なら LLMError。"""
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"応答に choices がありません: {payload!r:.200}") from exc
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        # 一部の実装は content を配列で返す
        content = "".join(part.get("text", "") for part in content
                          if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        # reasoning だけ返して content が空、というケースを拾う
        reasoning = message.get("reasoning") or message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            raise LLMError("応答が推論だけで本文が空でした。"
                           "reasoning_effort を下げて再試行してください。")
        raise LLMError("応答の本文が空でした。")
    return content


# ---------------------------------------------------------------------------
# 送信
# ---------------------------------------------------------------------------

def send(request_body: dict, endpoint=DEFAULT_ENDPOINT, timeout=DEFAULT_TIMEOUT,
         opener=None) -> str:
    """LM Studio に投げて本文を返す。opener はテスト用の差し替え口。"""
    data = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:      # noqa: BLE001 — 読めなくても本題ではない
            pass
        raise LLMError(f"HTTP {exc.code} {exc.reason}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(
            f"{endpoint} に接続できません ({exc.reason})。\n"
            "LM Studio のローカルサーバーが起動しているか確認してください。"
        ) from exc
    except OSError as exc:
        raise LLMError(f"通信に失敗しました: {exc}") from exc

    if status != 200:
        raise LLMError(f"HTTP {status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise LLMError(f"応答が JSON ではありません: {raw[:200]!r}") from exc
    return extract_text(payload)
