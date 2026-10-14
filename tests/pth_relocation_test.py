"""Tests root .pth subdirectory relocation: rerun-sdk.

`rerun-sdk` installs its actual `rerun` package inside a subdirectory
(`site-packages/rerun_sdk/rerun/__init__.py`) and ships a root `rerun_sdk.pth`
file containing `rerun_sdk` so `import rerun` works.
"""

import unittest


class PthRelocationTest(unittest.TestCase):
    def test_rerun_sdk_pth_relocation(self):
        import rerun

        self.assertTrue(hasattr(rerun, "__version__"))
        self.assertIn("rerun_sdk/rerun/", rerun.__file__.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
