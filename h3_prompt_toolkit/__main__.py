# -*- coding: utf-8 -*-
"""`python -m h3_prompt_toolkit` で GUI を起動する。バッチは cli サブモジュール。"""

import sys


def main():
    try:
        from . import gui
    except ImportError as exc:
        print(f"GUI を起動できません ({exc})。\n"
              "バッチ利用: python -m h3_prompt_toolkit.cli --help", file=sys.stderr)
        return 1
    gui.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
