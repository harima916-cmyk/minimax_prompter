# -*- coding: utf-8 -*-
import unittest

import _path  # noqa: F401

from h3_prompt_toolkit.grid import (FPS, grid_frames, grid_seconds,
                                    snap_up, grid_candidates)


class TestGrid(unittest.TestCase):
    def test_frames(self):
        self.assertEqual(grid_frames(0), 5)
        self.assertEqual(grid_frames(1), 22)
        self.assertEqual(grid_frames(8), 141)

    def test_seconds(self):
        self.assertAlmostEqual(grid_seconds(8), 141 / 24)
        self.assertEqual(FPS, 24)

    def test_snap_up_exact(self):
        # ちょうどグリッド上の値は同じ k に収まる (切り上げない)
        k, frames, sec = snap_up(141 / 24)
        self.assertEqual((k, frames), (8, 141))
        self.assertAlmostEqual(sec, 5.875)

    def test_snap_up_above(self):
        k, frames, sec = snap_up(5.876)
        self.assertEqual((k, frames), (9, 158))

    def test_snap_up_zero(self):
        k, frames, sec = snap_up(0.0)
        self.assertEqual((k, frames), (0, 5))

    def test_candidates(self):
        cands, k0 = grid_candidates(5.5)
        self.assertEqual(k0, 8)
        ks = [k for k, _, _ in cands]
        self.assertEqual(ks, [7, 8, 9, 10])


if __name__ == "__main__":
    unittest.main()
