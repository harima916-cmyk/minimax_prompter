# -*- coding: utf-8 -*-
import os
import struct
import tempfile
import unittest

import _path  # noqa: F401

import numpy as np

from h3_prompt_toolkit.audio import (read_wav, read_wav_raw_stereo,
                                     write_wav_pcm16, pad_to_seconds)


def _riff(fmt_chunk, data_chunk, extra_chunks=()):
    chunks = b""
    for cid, body in (((b"fmt ", fmt_chunk),) + tuple(extra_chunks)
                      + ((b"data", data_chunk),)):
        chunks += cid + struct.pack("<I", len(body)) + body
        if len(body) & 1:
            chunks += b"\x00"  # 偶数境界
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _fmt(tag, ch, sr, bits, ext_sub=None):
    block = ch * bits // 8
    base = struct.pack("<HHIIHH", tag, ch, sr, sr * block, block, bits)
    if ext_sub is not None:
        base += struct.pack("<H", 22)          # cbSize
        base += struct.pack("<HI", bits, 0)    # validBits, channelMask
        base += struct.pack("<H", ext_sub) + b"\x00" * 14  # SubFormat GUID 先頭
    return base


def _write(tmpdir, name, blob):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


class TestReadWav(unittest.TestCase):
    def test_float32(self):
        sig = np.linspace(-0.5, 0.5, 480, dtype=np.float32)
        blob = _riff(_fmt(3, 1, 48000, 32), sig.tobytes())
        with tempfile.TemporaryDirectory() as d:
            arr, sr, ch = read_wav(_write(d, "f32.wav", blob))
        self.assertEqual((sr, ch), (48000, 1))
        np.testing.assert_allclose(arr, sig, atol=1e-7)

    def test_pcm16_stereo_downmix(self):
        left = (np.ones(100) * 16384).astype("<i2")
        right = np.zeros(100, dtype="<i2")
        inter = np.empty(200, dtype="<i2")
        inter[0::2], inter[1::2] = left, right
        blob = _riff(_fmt(1, 2, 44100, 16), inter.tobytes())
        with tempfile.TemporaryDirectory() as d:
            arr, sr, ch = read_wav(_write(d, "s16.wav", blob))
        self.assertEqual(ch, 2)
        np.testing.assert_allclose(arr, np.full(100, 0.25), atol=1e-4)

    def test_pcm24(self):
        vals = np.array([0, 8388607, -8388608], dtype=np.int64)
        raw = b"".join(int(v & 0xFFFFFF).to_bytes(3, "little") for v in vals)
        blob = _riff(_fmt(1, 1, 24000, 24), raw)
        with tempfile.TemporaryDirectory() as d:
            arr, _, _ = read_wav(_write(d, "s24.wav", blob))
        np.testing.assert_allclose(arr, [0.0, 8388607 / 8388608, -1.0], atol=1e-7)

    def test_extensible_float(self):
        sig = np.array([0.25, -0.25], dtype=np.float32)
        blob = _riff(_fmt(0xFFFE, 1, 48000, 32, ext_sub=3), sig.tobytes())
        with tempfile.TemporaryDirectory() as d:
            arr, _, _ = read_wav(_write(d, "ext.wav", blob))
        np.testing.assert_allclose(arr, sig, atol=1e-7)

    def test_odd_sized_extra_chunk(self):
        sig = np.array([0.5], dtype=np.float32)
        blob = _riff(_fmt(3, 1, 48000, 32), sig.tobytes(),
                     extra_chunks=((b"LIST", b"abc"),))  # 奇数長チャンク
        with tempfile.TemporaryDirectory() as d:
            arr, _, _ = read_wav(_write(d, "odd.wav", blob))
        np.testing.assert_allclose(arr, sig, atol=1e-7)

    def test_not_riff(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(d, "bad.wav", b"OggS" + b"\x00" * 64)
            with self.assertRaises(ValueError):
                read_wav(path)


class TestWriteRoundtrip(unittest.TestCase):
    def test_pcm16_roundtrip(self):
        sig = np.stack([np.linspace(-0.9, 0.9, 200, dtype=np.float32),
                        np.zeros(200, dtype=np.float32)], axis=1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.wav")
            write_wav_pcm16(path, sig, 24000)
            arr, sr = read_wav_raw_stereo(path)
        self.assertEqual(sr, 24000)
        self.assertEqual(arr.shape, (200, 2))
        np.testing.assert_allclose(arr[:, 0], sig[:, 0], atol=1e-3)

    def test_pad(self):
        arr = np.ones((100, 1), dtype=np.float32)
        out, trimmed = pad_to_seconds(arr, 100, 1.5)
        self.assertEqual(out.shape[0], 150)
        self.assertEqual(trimmed, 0.0)
        np.testing.assert_allclose(out[100:], 0.0)

    def test_pad_trims(self):
        arr = np.ones((200, 1), dtype=np.float32)
        out, trimmed = pad_to_seconds(arr, 100, 1.5)
        self.assertEqual(out.shape[0], 150)
        self.assertAlmostEqual(trimmed, 0.5)


if __name__ == "__main__":
    unittest.main()
