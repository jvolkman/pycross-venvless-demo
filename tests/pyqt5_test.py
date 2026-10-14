"""Tests split regular packages + cross-wheel $ORIGIN RPATHs: PyQt5 + PyQt5-Qt5 + PyQt5-sip.

On PyPI, PyQt5 is split across three wheels:
  - `PyQt5`: ships `PyQt5/__init__.py` and `PyQt5/Qt*.abi3.so` with `DT_RUNPATH = $ORIGIN/Qt5/lib`
  - `PyQt5-Qt5`: ships `PyQt5/Qt5/lib/libQt5*.so.5` and `libicu*.so.56`
  - `PyQt5-sip`: ships `PyQt5/sip.cpython-*.so`

When each wheel is isolated in its own directory, `$ORIGIN/Qt5/lib` inside the `PyQt5`
wheel does not exist, so `from PyQt5 import QtCore` fails with:
  `ImportError: libQt5Core.so.5: cannot open shared object file: No such file or directory`

The rules_pycross import index + native solib farm (`.pycross_native.json` + `.pycross_solibs`)
resolves both the 3-wheel `PyQt5` package and the cross-wheel Qt5/ICU shared libraries
with zero wheel patches.
"""

import os
import unittest


class PyQt5SplitAndRpathTest(unittest.TestCase):
    def test_pyqt5_imports_and_headless_widget(self):
        import PyQt5
        from PyQt5 import QtCore, QtGui, QtWidgets, sip

        self.assertGreaterEqual(len(PyQt5.__path__), 2)
        self.assertTrue(QtCore.QT_VERSION_STR.startswith("5.15."))
        self.assertIsNotNone(sip.SIP_VERSION_STR)

        # Configure offscreen platform plugin from PyQt5-Qt5's wheel directory
        for p in PyQt5.__path__:
            plugins_dir = os.path.join(p, "Qt5", "plugins", "platforms")
            if os.path.isdir(plugins_dir):
                os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", plugins_dir)
                break
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        label = QtWidgets.QLabel("Hello from venvless PyQt5!")
        self.assertEqual(label.text(), "Hello from venvless PyQt5!")
        self.assertIsNotNone(QtGui.QFont())
        del label
        del app


if __name__ == "__main__":
    unittest.main()
