# -*- coding: utf-8 -*-
"""P4: LM Studio (OpenAI 互換 API) 送信の組み立てと通信。

実 API は叩かない。組み立ては純関数として、通信は opener の差し替えで
200 / HTTP エラー / 接続失敗の 3 通りを確認する。
"""

import base64
import io
import json
import os
import struct
import tempfile
import unittest
import urllib.error
import zlib

import _path  # noqa: F401

from h3_prompt_toolkit import llm_client as lc


def make_png(path, size_note=b"x"):
    """最小の PNG を作る (中身は問わない。base64 になることだけ見る)。"""
    def chunk(tag, data):
        payload = tag + data
        return (struct.pack(">I", len(data)) + payload
                + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\xff\xff" + size_note)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                 + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
    return path


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class TestBuildRequest(unittest.TestCase):
    def test_text_only(self):
        body = lc.build_request("SPEC", "BRIEF")
        self.assertEqual(body["model"], lc.DEFAULT_MODEL)
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SPEC"})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "BRIEF"})

    def test_settings_are_passed(self):
        body = lc.build_request("S", "B", settings={
            "model": "m", "temperature": 0.7, "top_p": 0.9,
            "reasoning_effort": "low"})
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["top_p"], 0.9)
        self.assertEqual(body["reasoning_effort"], "low")

    def test_unset_settings_are_omitted(self):
        body = lc.build_request("S", "B", settings={"temperature": None})
        self.assertNotIn("temperature", body)
        self.assertNotIn("reasoning_effort", body)

    def test_images_become_data_urls(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = make_png(os.path.join(d, "a.png"))
            p2 = make_png(os.path.join(d, "b.png"), b"y")
            body = lc.build_request("S", "B", [p1, p2])
        content = body["messages"][1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "B"})
        self.assertEqual(len(content), 3)
        for part in content[1:]:
            self.assertEqual(part["type"], "image_url")
            url = part["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"))
            base64.b64decode(url.split(",", 1)[1])   # 壊れていない

    def test_blank_paths_ignored(self):
        body = lc.build_request("S", "B", ["", None])
        self.assertEqual(body["messages"][1]["content"], "B")

    def test_missing_image_raises(self):
        with self.assertRaises(lc.LLMError):
            lc.build_request("S", "B", ["/nonexistent/none.png"])

    def test_oversized_image_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.png")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * (lc.MAX_IMAGE_BYTES + 1))
            with self.assertRaises(lc.LLMError):
                lc.build_request("S", "B", [path])


class TestExtractText(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(
            lc.extract_text({"choices": [{"message": {"content": "hi"}}]}), "hi")

    def test_content_as_list(self):
        payload = {"choices": [{"message": {"content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]}
        self.assertEqual(lc.extract_text(payload), "ab")

    def test_no_choices(self):
        with self.assertRaises(lc.LLMError):
            lc.extract_text({})

    def test_empty_content(self):
        with self.assertRaises(lc.LLMError):
            lc.extract_text({"choices": [{"message": {"content": "  "}}]})

    def test_reasoning_only_explains_itself(self):
        payload = {"choices": [{"message": {"content": "",
                                            "reasoning": "thinking..."}}]}
        with self.assertRaises(lc.LLMError) as cm:
            lc.extract_text(payload)
        self.assertIn("reasoning_effort", str(cm.exception))


class TestSend(unittest.TestCase):
    def test_success(self):
        seen = {}

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["timeout"] = timeout
            return FakeResponse({"choices": [{"message": {"content": "OUT"}}]})

        body = lc.build_request("S", "B")
        out = lc.send(body, endpoint="http://localhost:9/v1/chat/completions",
                      timeout=12, opener=opener)
        self.assertEqual(out, "OUT")
        self.assertEqual(seen["url"], "http://localhost:9/v1/chat/completions")
        self.assertEqual(seen["body"]["messages"][0]["content"], "S")
        self.assertEqual(seen["timeout"], 12)

    def test_http_error(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"model not loaded"}'))

        with self.assertRaises(lc.LLMError) as cm:
            lc.send({}, opener=opener)
        self.assertIn("400", str(cm.exception))
        self.assertIn("model not loaded", str(cm.exception))

    def test_connection_refused(self):
        def opener(req, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError("refused"))

        with self.assertRaises(lc.LLMError) as cm:
            lc.send({}, opener=opener)
        self.assertIn("LM Studio", str(cm.exception))

    def test_non_json_response(self):
        class Raw(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with self.assertRaises(lc.LLMError):
            lc.send({}, opener=lambda req, timeout=None: Raw(b"<html>"))


class TestSettingsFile(unittest.TestCase):
    def test_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            s = lc.load_settings(d)
        self.assertEqual(s["endpoint"], lc.DEFAULT_ENDPOINT)
        self.assertEqual(s["model"], lc.DEFAULT_MODEL)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            lc.save_settings({"endpoint": "http://x/v1", "model": "m"}, d)
            s = lc.load_settings(d)
        self.assertEqual(s["endpoint"], "http://x/v1")
        self.assertEqual(s["model"], "m")

    def test_broken_file_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            with open(lc.settings_path(d), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            s = lc.load_settings(d)
        self.assertEqual(s["endpoint"], lc.DEFAULT_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
