"""
Dualign 0.7.0 — GUI Demo

等价于欢迎页的"体验 Demo"按钮行为，通过 dualign.demo 加载。

用法:
  python -m demo.demo_gui              # 推荐
  python demo/demo_gui.py              # 也可直接运行
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dualign.demo import get_demo_paths


def main():
    # ── Windows: 标记独立 AppUserModelID，确保任务栏显示自定义图标 ──
    if sys.platform == "win32":
        try:
            import ctypes as _ctypes

            _ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Dualign.DualignStudio.v1"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 设置应用图标 ──
    from dualign.resources import load_app_icon

    _icon = load_app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)

    from dualign.gui.theme import T

    T.apply_to_app(app)

    from dualign.gui.window import DualignWindow

    win = DualignWindow()
    win.show()

    # show 之后再加载文件对，避免影响首次绘制
    from PySide6.QtCore import QTimer

    src, tgt, label = get_demo_paths()
    QTimer.singleShot(0, lambda: win.load_file_pair(src, tgt, label=label))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
