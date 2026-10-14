# `rules_pycross` Venvless / Import-Index Torture Tests

This repository collects real-world PyPI packages that break traditional Bazel Python rules (per-wheel `sys.path`, symlinked `site-packages/` trees, or static repo-time `RECORD` inspection) and demonstrates how [`rules_pycross`](https://github.com/jvolkman/rules_pycross/tree/prototype/native-loading) (`prototype/native-loading`) runs all of them out of the box with:

- **Zero per-wheel `site-packages` symlinks** in binaries/tests (eliminating the $O(\text{binaries} \times \text{wheels})$ symlink farm)
- **Zero `site_paths` overrides** in `MODULE.bazel`
- **Zero `post_install_patches`** or `LD_LIBRARY_PATH` wrapper scripts for cross-wheel `.so` dependencies
- **Zero build-time directory merge actions**

---

## Quick Start

Run the entire torture-test suite with the prebuilt import index (default):

```bash
bazel test //...
```

Verify the prebuilt native metadata against a live disk scan:

```bash
bazel test --test_env=RULES_PYTHON_IMPORT_INDEX=verify //...
```

Or compare against stock `rules_python` modes without the `rules_pycross` import index:

```bash
# Stock rules_python symlinked site-packages (venvs_site_packages=yes, venv_import_index=off)
# -> 5 of 6 tests FAIL (and spawns ~690 extra symlink actions!)
bazel test --config=off //...

# Plain per-wheel sys.path (venvs_site_packages=no, venv_import_index=off)
# -> 5 of 6 tests FAIL!
bazel test --config=no_venv //...
```

| Test Target | Pathology | Default (`prebuilt`) | `--config=off` (symlinked venv) | `--config=no_venv` (plain `sys.path`) |
| :--- | :--- | :---: | :---: | :---: |
| `//tests:functions_framework_test` | `*-nspkg.pth` + overlapping regular pkgs (`google.cloud.functions`) | PASS | FAIL | FAIL |
| `//tests:azure_plugin_test` | Plugin subpkg inside regular pkg (`azure.core.tracing.ext`) | PASS | FAIL | FAIL |
| `//tests:pyqt5_test` | 3-wheel split pkg + cross-wheel `$ORIGIN/Qt5/lib` C++ `.so`s | PASS | FAIL | FAIL |
| `//tests:nvidia_cuda_test` | 4-wheel `nvidia/__init__.py` + cross-wheel `DT_NEEDED` & bare SONAME `CDLL` | PASS | FAIL | FAIL |
| `//tests:mixed_namespaces_test` | Mixed PEP 420, `pkgutil.extend_path`, and `*-nspkg.pth` namespaces | PASS | FAIL | PASS |
| `//tests:pth_relocation_test` | Root `.pth` subdirectory redirection (`rerun_sdk.pth`) | PASS | PASS | FAIL |

---

## Torture-Test Catalog

### 1. Overlapping Non-Empty Regular Packages + `*-nspkg.pth` Poisoning (`functions-framework` + `google-cloud-functions`)
- **Target:** `//tests:functions_framework_test`
- **What the wheels do:**
  - `google-cloud-functions` (a modern PEP 420 package with no `.pth` file) ships `google/cloud/functions/__init__.py` and `google/cloud/functions_v1/__init__.py` (defining `CloudFunctionsServiceClient`, etc., plus `google/cloud/functions_v1/services/...`).
  - `functions-framework` ships:
    1. `functions_framework/`,
    2. compatibility shims at `google/cloud/functions/{__init__.py,context.py}` and `google/cloud/functions_v1/{__init__.py,context.py}` where its `__init__.py` files contain **only license comments** (0 executable statements), and
    3. a legacy setuptools `functions_framework-3.10.2-py3.12-nspkg.pth` file that runs `sys.modules.setdefault('google.cloud', ...)` at `site` startup using `[sitedir]` (a single wheel's `site-packages`!).
- **Why standard Bazel setups fail:**
  1. Under symlinked `site-packages/` (`--config=off`), two wheels cannot both claim `google/cloud/functions`, requiring manual `site_paths` overrides on `functions-framework` in `MODULE.bazel`.
  2. Under per-wheel `sys.path` (`--config=no_venv`), `functions_framework-*-nspkg.pth` pre-populates `sys.modules['google.cloud']` with a `_NamespacePath` bound only to `functions-framework`'s wheel directory (`[sitedir/google/cloud]`), hiding every PEP 420 `google.cloud.*` wheel in the process! And if `functions-framework` precedes `google-cloud-functions` on `sys.path`, its comment-only `google/cloud/functions/__init__.py` shadows `google-cloud-functions`'s `CloudFunctionsServiceClient` export.
- **How `rules_pycross` handles it:**
  The import index reconciles namespace modules injected into `sys.modules` by `*-nspkg.pth` files across all wheel roots, merges all contributing wheel directories into `google.cloud.functions.__path__`, and prefers non-comment `__init__.py` files over comment-only headers when merging regular packages.

### 2. Plugin Subpackage Inside Another Wheel's Regular Package (`azure-core` + `azure-core-tracing-opentelemetry`)
- **Target:** `//tests:azure_plugin_test`
- **What the wheels do:**
  - `azure-core` ships `azure/core/tracing/ext/__init__.py`, making `azure.core.tracing.ext` a **regular package** (not a PEP 420 namespace).
  - `azure-core-tracing-opentelemetry` installs its plugin module `azure/core/tracing/ext/opentelemetry_span/` *inside* `azure/core/tracing/ext/`.
- **Why standard Bazel setups fail:**
  On standard multi-root `sys.path`, importing `azure.core.tracing.ext` binds `azure.core.tracing.ext.__path__` strictly to `azure-core`'s wheel directory, causing `import azure.core.tracing.ext.opentelemetry_span` to fail with `ModuleNotFoundError`.
- **How `rules_pycross` handles it:**
  The import index includes `azure-core-tracing-opentelemetry`'s `azure/core/tracing/ext` directory in `azure.core.tracing.ext.__path__`.

### 3. Split Regular Package + Cross-Wheel `$ORIGIN` C++ Shared Libraries (`PyQt5` + `PyQt5-Qt5` + `PyQt5-sip`)
- **Target:** `//tests:pyqt5_test`
- **What the wheels do:**
  - `PyQt5` is split across three wheels that all install into `PyQt5/`:
    - `PyQt5` ships `PyQt5/__init__.py` and `PyQt5/Qt*.abi3.so` with `DT_RUNPATH = $ORIGIN/Qt5/lib` and `DT_NEEDED = libQt5Core.so.5`.
    - `PyQt5-Qt5` ships `PyQt5/Qt5/lib/libQt5*.so.5` and `libicu{i18n,uc,data}.so.56`.
    - `PyQt5-sip` ships `PyQt5/sip.cpython-*.so`.
- **Why standard Bazel setups fail:**
  Because each wheel is extracted into its own Bazel directory, `$ORIGIN/Qt5/lib` relative to `PyQt5/QtCore.abi3.so` does not exist, failing at import time with:
  ```text
  ImportError: libQt5Core.so.5: cannot open shared object file: No such file or directory
  ```
- **How `rules_pycross` handles it:**
  At wheel install time, `rules_pycross` inspects ELF binaries in each wheel and writes a small `.pycross_native.json` dotfile only for wheels that contain shared libraries. At startup, the import index populates a single `.pycross_solibs/` symlink directory for the native wheels in the binary and primes `glibc`'s dynamic loader (`ld.so`) once, allowing `QtCore.abi3.so` and its transitive Qt5/ICU shared libraries to resolve by SONAME with **zero wheel patches**.

### 4. Multi-Wheel `nvidia/__init__.py` + Cross-Wheel `DT_NEEDED` & Bare-SONAME `ctypes.CDLL` (`nvidia-*-cu12`)
- **Target:** `//tests:nvidia_cuda_test`
- **What the wheels do:**
  1. Every CUDA 12 wheel (`nvidia-cusparse-cu12`, `nvidia-nvjitlink-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-nvtx-cu12`) ships an empty `nvidia/__init__.py` (making `nvidia` an overlapping regular package across all `nvidia-*` wheels).
  2. `nvidia/cusparse/lib/libcusparse.so.12` has `DT_NEEDED = libnvJitLink.so.12` and `DT_RUNPATH = $ORIGIN:$ORIGIN/../../nvjitlink/lib`, expecting `../../nvjitlink/lib` to cross into `nvidia-nvjitlink-cu12`.
  3. Downstream consumers (such as `onnxruntime-gpu`'s `preload_dlls()`) load CUDA libraries via bare-SONAME `ctypes.CDLL("libnvJitLink.so.12")` / `ctypes.CDLL("libcudart.so.12")`.
- **How `rules_pycross` handles it:**
  - `nvidia.__path__` spans all contributing `nvidia-*` wheels.
  - `.pycross_solibs/` exposes `libnvJitLink.so.12`, `libcusparse.so.12`, `libcudart.so.12`, and `libnvToolsExt.so.1` to `ld.so`, satisfying both cross-wheel `DT_NEEDED` and bare-SONAME `ctypes.CDLL(...)` calls.

### 5. Mixed Namespace Conventions (`google.*`, `opentelemetry.*`, `zope.*`, `backports.*`)
- **Target:** `//tests:mixed_namespaces_test`
- **What the wheels do:**
  Exercises all four Python namespace mechanisms in the same binary:
  - **PEP 420 implicit namespaces:** `google-cloud-storage` + `google-cloud-pubsub` + `protobuf` (`google._upb._message`), `opentelemetry-api` + `opentelemetry-sdk`
  - **`pkgutil.extend_path`:** `backports.tarfile` + `backports.entry-points-selectable` (both ship `backports/__init__.py`)
  - **`*-nspkg.pth` startup injection + C extensions:** `zope.interface` + `zope.event` (`zope.interface` ships `_zope_interface_coptimizations.cpython-*.so` and a `zope.interface-*-nspkg.pth` that injects `sys.modules["zope"]` during `site` initialization)

### 6. Root `.pth` Subdirectory Relocation (`rerun-sdk`)
- **Target:** `//tests:pth_relocation_test`
- **What the wheel does:**
  `rerun-sdk` places its actual `rerun` package inside a subdirectory (`site-packages/rerun_sdk/rerun/__init__.py`) and ships a root `rerun_sdk.pth` file containing `rerun_sdk` so `import rerun` resolves.
- **How `rules_pycross` handles it:**
  `wheel_installer` evaluates root `.pth` relative path entries at install time and folds the extra subdirectory (`rerun_sdk`) into the wheel's `.pycross_index.json` (`extra_sys_path`, `top_levels`, `pkg_dirs`, and `native_libs`).
