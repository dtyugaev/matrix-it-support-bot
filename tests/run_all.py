# -*- coding: utf-8 -*-
"""Запуск тестов без pytest: ``python3 tests/run_all.py``.

Полезно в окружениях, где pytest не установлен. Обычный запуск — ``pytest -q``.
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))


def main() -> int:
    modules = sorted(
        name[:-3]
        for name in os.listdir(HERE)
        if name.startswith("test_") and name.endswith(".py")
    )
    passed, failed = 0, []
    for module_name in modules:
        module = importlib.import_module(module_name)
        for attr in sorted(dir(module)):
            if not attr.startswith("test_"):
                continue
            func = getattr(module, attr)
            if not callable(func):
                continue
            try:
                func()
                passed += 1
            except Exception:  # noqa: BLE001
                failed.append(f"{module_name}.{attr}\n{traceback.format_exc()}")
    print(f"Пройдено: {passed}, провалено: {len(failed)}")
    for item in failed:
        print("-" * 70)
        print(item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
