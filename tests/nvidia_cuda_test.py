"""Tests overlapping nvidia/__init__.py regular packages + cross-wheel DT_NEEDED & bare SONAME dlopen.

Demonstrates three distinct issues in NVIDIA CUDA wheels (`nvidia-*-cu12`):
  1. Every wheel (`nvidia-cusparse-cu12`, `nvidia-nvjitlink-cu12`, `nvidia-cuda-runtime-cu12`,
     `nvidia-nvtx-cu12`) ships an empty `nvidia/__init__.py` (a regular package, not a PEP 420
     namespace). On standard multi-root `sys.path`, the first `nvidia-*` wheel wins
     `nvidia/__init__.py` and hides all sibling `nvidia.*` subpackages.
  2. `nvidia/cusparse/lib/libcusparse.so.12` has `DT_NEEDED = libnvJitLink.so.12` and
     `DT_RUNPATH = $ORIGIN:$ORIGIN/../../nvjitlink/lib`, expecting `../../nvjitlink/lib`
     to resolve into `nvidia-nvjitlink-cu12`'s directory.
  3. Downstream packages (e.g. `onnxruntime-gpu`) call `ctypes.CDLL("libnvJitLink.so.12")`
     or `ctypes.CDLL("libcudart.so.12")` by bare SONAME.
"""

import ctypes
import os
import unittest


class NvidiaCudaSplitAndNativeLoadTest(unittest.TestCase):
    def test_nvidia_multi_wheel_regular_package(self):
        import nvidia
        import nvidia.cuda_runtime.lib
        import nvidia.cusparse.lib
        import nvidia.nvjitlink.lib
        import nvidia.nvtx.lib

        # All 4 wheels contribute to `nvidia.__path__`
        self.assertEqual(len(nvidia.__path__), 4)

    def test_cross_wheel_dt_needed_resolution(self):
        import nvidia.cusparse.lib

        # libcusparse.so.12 has DT_NEEDED on libnvJitLink.so.12 (in nvidia-nvjitlink-cu12).
        cusparse_path = os.path.join(
            nvidia.cusparse.lib.__path__[0], "libcusparse.so.12"
        )
        self.assertTrue(os.path.isfile(cusparse_path))
        lib = ctypes.CDLL(cusparse_path)
        self.assertIsNotNone(lib)

    def test_bare_soname_cdll(self):
        # Loading by bare SONAME (as onnxruntime-gpu's preload_dlls() does) resolves
        # directly through .pycross_solibs primed in ld.so.
        for soname in (
            "libnvJitLink.so.12",
            "libcusparse.so.12",
            "libcudart.so.12",
            "libnvToolsExt.so.1",
        ):
            with self.subTest(soname=soname):
                lib = ctypes.CDLL(soname)
                self.assertIsNotNone(lib)


if __name__ == "__main__":
    unittest.main()
