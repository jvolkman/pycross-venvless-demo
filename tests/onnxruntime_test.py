"""onnxruntime-gpu: real-world bare-SONAME ctypes.CDLL fallback via preload_dlls().

`onnxruntime.preload_dlls()` looks for `../nvidia/<component>/lib/lib*.so.*` relative to
`onnxruntime/__init__.py`. In a flat `pip` `site-packages/` directory those relative paths exist on
disk, but when each wheel lives in its own directory (`--venv_import_index` or `--config=no_venv`),
`os.path.isfile(...)` is `False` inside `onnxruntime-gpu`'s wheel root. `preload_dlls()` then falls
back to bare-name `ctypes.CDLL("libcublasLt.so.12")`, `ctypes.CDLL("libcublas.so.12")`,
`ctypes.CDLL("libnvrtc.so.12")`, `ctypes.CDLL("libcurand.so.10")`, `ctypes.CDLL("libcufft.so.11")`,
`ctypes.CDLL("libcudart.so.12")`, and `ctypes.CDLL("libcudnn.so.9")`.

With `--venv_import_index=prebuilt`, the `_ctypes.dlopen` bare-name fallback resolves those bare
SONAMEs from the `nvidia-*-cu12` wheels in the binary's dependency closure, allowing
`capi/libonnxruntime_providers_cuda.so` (which has no `DT_RPATH`/`DT_RUNPATH` of its own) to
resolve all of its CUDA/cuDNN `DT_NEEDED` entries.

No GPU is needed: `preload_dlls()` and `libonnxruntime_providers_cuda.so` load without a GPU.
"""

import contextlib
import ctypes
import io
import os
import unittest

import onnxruntime

EXPECTED_NVIDIA_SONAMES = (
    "libcublasLt.so.12",
    "libcublas.so.12",
    "libnvrtc.so.12",
    "libcurand.so.10",
    "libcufft.so.11",
    "libcudart.so.12",
    "libcudnn.so.9",
)


def _mapped_libraries():
    with open("/proc/self/maps") as f:
        return {line.split()[-1] for line in f if "/" in line and ".so" in line}


class OnnxRuntimeGpuPreloadTest(unittest.TestCase):
    def test_preload_dlls_and_cuda_provider_load(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            onnxruntime.preload_dlls(cuda=True, cudnn=True)
        output = buf.getvalue()
        self.assertNotIn("Failed to load", output, output)

        mapped = _mapped_libraries()
        for soname in EXPECTED_NVIDIA_SONAMES:
            with self.subTest(soname=soname):
                hits = [p for p in mapped if os.path.basename(p) == soname]
                self.assertTrue(hits, f"{soname} is not loaded after preload_dlls()")
                self.assertTrue(all("/nvidia/" in p for p in hits), hits)

        capi_dir = os.path.join(os.path.dirname(onnxruntime.__file__), "capi")
        shared_so = os.path.join(capi_dir, "libonnxruntime_providers_shared.so")
        cuda_so = os.path.join(capi_dir, "libonnxruntime_providers_cuda.so")
        self.assertTrue(os.path.isfile(shared_so), shared_so)
        self.assertTrue(os.path.isfile(cuda_so), cuda_so)

        ctypes.CDLL(shared_so, mode=ctypes.RTLD_GLOBAL)
        ctypes.CDLL(cuda_so)


if __name__ == "__main__":
    unittest.main()
