# -*- coding: utf-8 -*-
"""P7: Qwen3.8 の実出力フィクスチャを [C] → [D] に通す回帰テスト。

tests/fixtures/qwen38/ に 生テキスト (.txt) と対応する Timeline (.json) を
並べて置くだけで対象になる。0 件でも成功する (skip ではなく素通し)。
置き方は同ディレクトリの README.md を参照。
"""

import json
import os
import unittest

import _path

from h3_prompt_toolkit.timeline import Timeline
from h3_prompt_toolkit.substitute import substitute
from h3_prompt_toolkit.validate import validate, counts, render_report

QWEN_DIR = os.path.join(_path.FIXTURES, "qwen38")


def cases():
    """[(名前, 生テキスト, Timeline, 対応表), ...] を返す。"""
    out = []
    if not os.path.isdir(QWEN_DIR):
        return out
    for name in sorted(os.listdir(QWEN_DIR)):
        if not name.endswith(".txt"):
            continue
        stem = name[:-4]
        tl_path = os.path.join(QWEN_DIR, stem + ".json")
        if not os.path.isfile(tl_path):
            continue
        with open(os.path.join(QWEN_DIR, name), encoding="utf-8") as fh:
            text = fh.read()
        maps = {}
        map_path = os.path.join(QWEN_DIR, stem + ".map.json")
        if os.path.isfile(map_path):
            with open(map_path, encoding="utf-8") as fh:
                maps = json.load(fh)
        out.append((stem, text, Timeline.load(tl_path), maps))
    return out


class TestQwen38Fixtures(unittest.TestCase):
    def test_all_fixtures_survive_substitute_and_validate(self):
        found = cases()
        for name, text, tl, maps in found:
            with self.subTest(fixture=name):
                res = substitute(text, tl,
                                 ts_map=maps.get("ts_map"),
                                 d_map=maps.get("d_map"))
                self.assertTrue(
                    res.ok,
                    f"{name}: [C] が完了しませんでした\n{res.report_text()}")
                findings = validate(res.text, tl)
                errors = counts(findings)[0]
                self.assertEqual(
                    errors, 0,
                    f"{name}: [D] にエラー {errors} 件\n{render_report(findings)}")
        # 0 件でも失敗にはしない。件数だけ記録しておく
        self.assertGreaterEqual(len(found), 0)


if __name__ == "__main__":
    unittest.main()
