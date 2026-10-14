"""torch: real-world cross-wheel CUDA library loading.

`import torch` runs `_load_global_deps()`, which loads `torch/lib/libtorch_global_deps.so`
with RTLD_GLOBAL. Its DT_NEEDED entries (libcublas, libcudnn, ...) are found through
DT_RUNPATH entries such as `$ORIGIN/../../nvidia/cublas/lib`, which only resolve when torch
and the nvidia-*-cu12 wheels share one site-packages directory. If that load fails, torch's
`_preload_cuda_deps()` globs `<entry>/nvidia/<lib>/lib/<name>` for every `sys.path` entry,
preloads what it finds, and retries.

torch works in every mode thanks to that fallback, but most packages with escaping RUNPATHs
(tensorflow, jaxlib plugins, ...) have none. So the first test loads libtorch_global_deps.so
directly, before `import torch`, to check that its RUNPATH dependencies resolve on their own.

No GPU is needed: the CUDA libraries load without one, and torch.cuda reports no device.
"""

import ctypes
import importlib.util
import os
import unittest


def _mapped_libraries():
    with open("/proc/self/maps") as f:
        return {line.split()[-1] for line in f if "/" in line and ".so" in line}


class TorchCudaLoadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Runs before any test imports torch (and before torch's own sys.path fallback).
        spec = importlib.util.find_spec("torch")
        lib = os.path.join(spec.submodule_search_locations[0], "lib", "libtorch_global_deps.so")
        try:
            ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
            cls.direct_load_error = None
        except OSError as e:
            cls.direct_load_error = str(e)

    def test_global_deps_resolve_without_torch_fallback(self):
        self.assertIsNone(self.direct_load_error)

    def test_import_loads_cuda_libraries_from_wheels(self):
        import torch

        self.assertEqual((torch.ones(3) + 1).tolist(), [2.0, 2.0, 2.0])
        torch.cuda.is_available()  # Must not crash without a GPU.

        mapped = _mapped_libraries()
        for soname in ("libcublas.so.12", "libcudnn.so.9", "libcusparse.so.12", "libnvJitLink.so.12"):
            with self.subTest(soname=soname):
                hits = [p for p in mapped if os.path.basename(p) == soname]
                self.assertTrue(hits, f"{soname} is not loaded")
                self.assertTrue(all("/nvidia/" in p for p in hits), hits)

    def test_preload_cuda_deps_walks_sys_path(self):
        import torch

        # torch's own fallback: glob nvidia/<folder>/lib/<name> on every sys.path entry.
        # It raises ValueError if no sys.path entry has the library.
        torch._preload_cuda_deps("cublas", "libcublas.so.*[0-9]")
        torch._preload_cuda_deps("cudnn", "libcudnn.so.*[0-9]")


if __name__ == "__main__":
    unittest.main()
