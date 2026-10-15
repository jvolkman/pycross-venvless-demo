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
# -> 4 of 8 tests FAIL: two cross-wheel regular-package collisions, the bare-SONAME
#    ctypes.CDLL check (which a plain pip venv fails too), and torch's RUNPATH check. Also spawns one symlink action per
#    linked path per binary.
bazel test --config=off //...

# Startup directory scan instead of the prebuilt native metadata (pure-Python resolution only)
# -> 4 of 8 tests FAIL (the four native tests)
bazel test --config=scan //...

# Plain per-wheel sys.path (venvs_site_packages=no, venv_import_index=off)
# -> 7 of 8 tests FAIL!
bazel test --config=no_venv //...
```

| Test Target | Pathology | Default (`prebuilt`) | `--config=scan` | `--config=off` (symlinked venv) | `--config=no_venv` (plain `sys.path`) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `//tests:functions_framework_test` | `*-nspkg.pth` + overlapping regular pkgs (`google.cloud.functions`) | PASS | PASS | FAIL | FAIL |
| `//tests:azure_plugin_test` | Plugin subpkg inside regular pkg (`azure.core.tracing.ext`) | PASS | PASS | FAIL | FAIL |
| `//tests:pyqt5_test` | 3-wheel split pkg + cross-wheel `$ORIGIN/Qt5/lib` `.so`s & QML/translation assets | PASS | FAIL | PASS | FAIL |
| `//tests:nvidia_cuda_test` | 4-wheel `nvidia/__init__.py` + cross-wheel `DT_NEEDED` & bare SONAME `CDLL` | PASS | FAIL | FAIL¹ | FAIL |
| `//tests:pth_relocation_test` | Root `.pth` subdirectory redirection (`rerun_sdk.pth`) | PASS | PASS | PASS | FAIL |
| `//tests:mixed_namespaces_test` | Mixed PEP 420, `pkgutil.extend_path`, and `*-nspkg.pth` namespaces | PASS | PASS | PASS | PASS |
| `//tests:torch_test` | Real torch: CUDA libraries via escaping `$ORIGIN/../../nvidia/*/lib` RUNPATHs, plus torch's `sys.path`-walking fallback | PASS | FAIL² | FAIL² | FAIL² |
| `//tests:onnxruntime_test` | Real `onnxruntime-gpu`: `preload_dlls()` relative `../nvidia/*/lib` miss $\rightarrow$ bare-SONAME `ctypes.CDLL` fallback + `libonnxruntime_providers_cuda.so` | PASS | FAIL | PASS | FAIL |

¹ Only the bare-SONAME `ctypes.CDLL("libnvJitLink.so.12")` check fails; a plain pip venv fails it too unless something already loaded the library. Cross-wheel `DT_NEEDED` resolution passes since rules_pycross 2.0.1 ([#364](https://github.com/jvolkman/rules_pycross/pull/364)).
² torch itself imports and works in every mode, because when `libtorch_global_deps.so` fails to load it globs `nvidia/<lib>/lib` on every `sys.path` entry (`_preload_cuda_deps`) and retries. Only the check that its RUNPATH dependencies resolve without that fallback fails, which matters for packages with the same layout and no fallback (tensorflow, jaxlib plugins, …).

**Is `--config=off` what rules_python's own pip rules would do?** Probably not for the two collision tests (not tested in this repo). When several wheels' venv entries overlap, rules_python merges them file by file (`_merge_venv_path_group` in `venv_runfiles.bzl`), which needs every file as a `File` at analysis time. `pip.parse` extracts wheels at repository time and globs their files, so it provides that list, and `azure_plugin_test` and `functions_framework_test` likely pass there with `venvs_site_packages=yes`. One caveat: files both wheels ship (the two `google/cloud/functions/__init__.py`) are first-wins, so the functions-framework result may depend on dependency order, much as pip's result depends on install order. rules_pycross installs each wheel into a single TreeArtifact whose files aren't visible at analysis time, so overlapping entries are dropped instead. That's a rules_pycross limitation of the symlinked venv, which the import index avoids.

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
  1. Under symlinked `site-packages/` (`--config=off`), both wheels claim `google/cloud/functions` and `google/cloud/functions_v1`. rules_python can only merge overlapping entries file by file, and a rules_pycross wheel is a single TreeArtifact, so both entries are dropped (`ModuleNotFoundError: No module named 'google.cloud.functions'`) unless `site_paths` overrides on `functions-framework` make the paths disjoint. rules_python's `pip.parse` likely merges them (see above).
  2. Under per-wheel `sys.path` (`--config=no_venv`), `functions_framework-*-nspkg.pth` pre-populates `sys.modules['google.cloud']` with a `_NamespacePath` bound only to `functions-framework`'s wheel directory (`[sitedir/google/cloud]`), hiding every PEP 420 `google.cloud.*` wheel in the process! And if `functions-framework` precedes `google-cloud-functions` on `sys.path`, its comment-only `google/cloud/functions/__init__.py` shadows `google-cloud-functions`'s `CloudFunctionsServiceClient` export.
- **How `rules_pycross` handles it:**
  The import index reconciles namespace modules injected into `sys.modules` by `*-nspkg.pth` files across all wheel roots, merges all contributing wheel directories into `google.cloud.functions.__path__`, and prefers non-comment `__init__.py` files over comment-only headers when merging regular packages.

### 2. Plugin Subpackage Inside Another Wheel's Regular Package (`azure-core` + `azure-core-tracing-opentelemetry`)
- **Target:** `//tests:azure_plugin_test`
- **What the wheels do:**
  - `azure-core` ships `azure/core/tracing/ext/__init__.py`, making `azure.core.tracing.ext` a **regular package** (not a PEP 420 namespace).
  - `azure-core-tracing-opentelemetry` installs its plugin module `azure/core/tracing/ext/opentelemetry_span/` *inside* `azure/core/tracing/ext/`.
- **Why standard Bazel setups fail:**
  1. Under symlinked `site-packages/` (`--config=off`), `azure-core` claims `site_paths = ["azure/core"]` while `azure-core-tracing-opentelemetry` claims `site_paths = ["azure/core/tracing/ext/opentelemetry_span"]`. `rules_python` merges overlapping entries file by file (`_merge_venv_path_group`), which needs a per-file `File` list; a rules_pycross wheel is a single TreeArtifact, so both entries are dropped (`ModuleNotFoundError: No module named 'azure'`).
  2. Under per-wheel `sys.path` (`--config=no_venv`), importing `azure.core.tracing.ext` binds `azure.core.tracing.ext.__path__` strictly to `azure-core`'s wheel directory, causing `import azure.core.tracing.ext.opentelemetry_span` to fail with `ModuleNotFoundError`.
- **How `rules_pycross` handles it:**
  The import index includes `azure-core-tracing-opentelemetry`'s `azure/core/tracing/ext` directory in `azure.core.tracing.ext.__path__`.

### 3. Split Regular Package + Cross-Wheel `$ORIGIN` C++ Shared Libraries & Assets (`PyQt5` + `PyQt5-Qt5` + `PyQt5-sip`)
- **Target:** `//tests:pyqt5_test`
- **What the wheels do:**
  - `PyQt5` is split across three wheels that all install into `PyQt5/`:
    - `PyQt5` ships `PyQt5/__init__.py` (`pkgutil.extend_path`) and `PyQt5/Qt*.abi3.so` with `DT_RUNPATH = $ORIGIN/Qt5/lib` and `DT_NEEDED = libQt5Core.so.5`.
    - `PyQt5-Qt5` ships `PyQt5/Qt5/lib/libQt5*.so.5`, `libicu{i18n,uc,data}.so.56`, `PyQt5/Qt5/plugins/platforms/libqoffscreen.so`, plus 1,332 QML (`PyQt5/Qt5/qml/**/qmldir`) and translation (`PyQt5/Qt5/translations/*.qm`) asset files.
    - `PyQt5-sip` ships `PyQt5/sip.cpython-*.so`.
- **Why standard Bazel setups fail:**
  1. Under per-wheel `sys.path` (`--config=no_venv`), `$ORIGIN/Qt5/lib` relative to `PyQt5/QtCore.abi3.so` does not exist inside the `PyQt5` wheel directory, failing with `ImportError: libQt5Core.so.5: cannot open shared object file` (or picking up an incompatible host `/usr/lib` Qt5 and crashing with `symbol lookup error: .../libqoffscreen.so: undefined symbol: _ZdlPvm, version Qt_5`).
  2. Under symlinked `site-packages/` (`--config=off`), rules_pycross 2.0.0's `inspect_package.py` only emitted `.py`, `.pth`, and `.so(.digits)*` files when descending into a namespace directory, dropping all **1,332 non-`.so` files** in `PyQt5-Qt5` (`PyQt5/Qt5/qml/**/qmldir`, `.qml`, `PyQt5/Qt5/translations/*.qm`) from the `.venv` symlink tree. Fixed in 2.0.1 ([#364](https://github.com/jvolkman/rules_pycross/pull/364)): every file in a namespace directory is linked.
- **How `rules_pycross` handles it:**
  At wheel install time, `rules_pycross` reads the ELF dynamic sections of each wheel's shared libraries and writes an in-root dotfile only for wheels that contain them: `.pycross-native-consumer.json` when a library has an escaping `$ORIGIN` RPATH or an unsatisfied non-system `DT_NEEDED` (here `PyQt5`), otherwise `.pycross-native.json`. Before `PyQt5` is imported, the import index preloads `libQt5Core.so.5` and its transitive Qt5/ICU libraries from `PyQt5-Qt5` by absolute path (`ctypes.CDLL`; a memfd stub ELF on musl), so `QtCore.abi3.so`'s `DT_NEEDED` entries resolve by SONAME with **zero wheel patches**. `PyQt5.__path__` spans all three wheels, so QML and translation assets stay in place.

### 4. Multi-Wheel `nvidia/__init__.py` + Cross-Wheel `DT_NEEDED` & Bare-SONAME `ctypes.CDLL` (`nvidia-*-cu12`)
- **Target:** `//tests:nvidia_cuda_test`
- **What the wheels do:**
  1. Every CUDA 12 wheel (`nvidia-cusparse-cu12`, `nvidia-nvjitlink-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-nvtx-cu12`) ships an empty `nvidia/__init__.py` (making `nvidia` an overlapping regular package across all `nvidia-*` wheels).
  2. `nvidia/cusparse/lib/libcusparse.so.12` has `DT_NEEDED = libnvJitLink.so.12` and `DT_RUNPATH = $ORIGIN:$ORIGIN/../../nvjitlink/lib`, expecting `../../nvjitlink/lib` to cross into `nvidia-nvjitlink-cu12`.
  3. Downstream consumers (such as `onnxruntime-gpu`'s `preload_dlls()`) load CUDA libraries via bare-SONAME `ctypes.CDLL("libnvJitLink.so.12")` / `ctypes.CDLL("libcudart.so.12")`.
- **Why standard Bazel setups fail:**
  1. Under per-wheel `sys.path` (`--config=no_venv`), the first `nvidia-*` wheel on `sys.path` wins `nvidia/__init__.py` and hides all sibling `nvidia.*` subpackages (`ModuleNotFoundError: No module named 'nvidia.cusparse'`).
  2. Under symlinked `site-packages/` (`--config=off`), rules_pycross 2.0.0 linked `site-packages/nvidia/cusparse` as a directory symlink, so the kernel resolved `$ORIGIN/../../nvjitlink/lib` against the real directory inside `nvidia-cusparse-cu12`'s wheel and `ctypes.CDLL(cusparse_path)` failed with `OSError: libnvJitLink.so.12: cannot open shared object file`. Since 2.0.1 ([#364](https://github.com/jvolkman/rules_pycross/pull/364)), shared libraries below a namespace are linked individually under real directories, so this passes. Bare-SONAME `ctypes.CDLL("libnvJitLink.so.12")` still fails in both modes (as it does in a pip venv when nothing loaded the library first).
- **How `rules_pycross` handles it:**
  - `nvidia.__path__` spans all contributing `nvidia-*` wheels.
  - Before `nvidia.cusparse` loads, the import index preloads `libnvJitLink.so.12` by absolute path, which satisfies `libcusparse.so.12`'s `DT_NEEDED`.
  - A `_ctypes.dlopen` hook resolves bare-SONAME `ctypes.CDLL(...)` calls that the normal lookup can't find through the wheels' SONAME table.

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
  The import index processes the root `.pth` files of all wheel roots at startup in global name order via `site.addpackage` (pip semantics), so `rerun_sdk.pth` adds `rerun_sdk/` to `sys.path`. In `--config=off`, `rerun_sdk.pth` is linked into the venv's `site-packages`, where `site` processes it too.

### 7. Real-World Consumer: `torch` + 12 `nvidia-*-cu12` Wheels
- **Target:** `//tests:torch_test`
- **What the wheels do:**
  - `torch` 2.5.1 depends on 12 `nvidia-*-cu12` wheels (pinned to the same versions as test 4). `torch/lib/libtorch_global_deps.so` and friends have `DT_NEEDED` entries such as `libcudart.so.12`, `libcublas.so.12` and `libcudnn.so.9`, found through `DT_RUNPATH` entries like `$ORIGIN/../../nvidia/cublas/lib`.
  - `import torch` loads `libtorch_global_deps.so` with `RTLD_GLOBAL`. If that fails, `_preload_cuda_deps()` walks `sys.path`, globs `<entry>/nvidia/<lib>/lib/<name>`, preloads what it finds and retries.
- **What the test checks:** before `import torch`, that `libtorch_global_deps.so` loads directly (its RUNPATH dependencies resolve); after it, that a tensor op works, that `libcublas`, `libcudnn`, `libcusparse` and `libnvJitLink` are mapped from the nvidia wheels, and that torch's own `sys.path` walk (`_preload_cuda_deps`) finds `libcublas` and `libcudnn`. No GPU is needed.
- **Why standard Bazel setups fail (the RUNPATH check; torch's fallback still rescues the import):**
  1. Under per-wheel `sys.path` (`--config=no_venv`), `torch/lib/../../nvidia` is inside torch's own wheel directory, where no `nvidia/` exists.
  2. Under symlinked `site-packages/` (`--config=off`), `torch` is a top-level regular package, so it is linked as one directory symlink, and the kernel resolves `$ORIGIN/../..` inside torch's wheel. rules_pycross keeps top-level packages whole; only shared-library directories below a namespace are split.
- **How `rules_pycross` handles it:** `torch` is a consumer wheel (escaping `$ORIGIN`), so the import index preloads the nvidia libraries its RUNPATHs point at, by absolute path, before torch loads `libtorch_global_deps.so`. In `--config=scan` there is no native metadata, so only torch's fallback works.

### 8. Real-World Bare-SONAME `ctypes.CDLL` Fallback (`onnxruntime-gpu` + 6 `nvidia-*-cu12` Wheels)
- **Target:** `//tests:onnxruntime_test`
- **What the wheels do:**
  - `onnxruntime-gpu` 1.24.4 ships `onnxruntime/capi/libonnxruntime_providers_cuda.so` with `DT_NEEDED` entries on `libcublasLt.so.12`, `libcublas.so.12`, `libcurand.so.10`, `libcufft.so.11`, `libcudart.so.12`, `libcudnn.so.9`, and `libnvrtc.so.12`, and **empty `DT_RPATH` / `DT_RUNPATH`** (`rpath: [], runpath: []`).
  - To load those libraries before the CUDA execution provider is opened, `onnxruntime.preload_dlls(cuda=True, cudnn=True)` first looks for `../nvidia/<component>/lib/lib*.so.*` relative to `onnxruntime/__init__.py` (`os.path.join(os.path.dirname(__file__), "..")`). If `os.path.isfile(dll_path)` is `False`, it falls back to bare-name `ctypes.CDLL("libcublasLt.so.12")`, `ctypes.CDLL("libcublas.so.12")`, `ctypes.CDLL("libnvrtc.so.12")`, `ctypes.CDLL("libcurand.so.10")`, `ctypes.CDLL("libcufft.so.11")`, `ctypes.CDLL("libcudart.so.12")`, and `ctypes.CDLL("libcudnn.so.9")`.
- **What the test checks:** calls `onnxruntime.preload_dlls(cuda=True, cudnn=True)`, asserts no `"Failed to load"` messages are printed, verifies all 7 libraries are mapped in `/proc/self/maps` from the `/nvidia/` wheels, and then opens `capi/libonnxruntime_providers_shared.so` and `capi/libonnxruntime_providers_cuda.so` via `ctypes.CDLL` to prove `libonnxruntime_providers_cuda.so`'s `DT_NEEDED` entries resolve. No GPU is needed.
- **Why standard Bazel setups fail:**
  1. Under `--config=scan` and `--config=no_venv`, each wheel lives in its own `site-packages/` directory, so `os.path.join(os.path.dirname(onnxruntime.__file__), "..", "nvidia", ...)` looks inside `onnxruntime-gpu`'s wheel root (where no `nvidia/` exists), and the bare-name `ctypes.CDLL("libcublasLt.so.12")` fallback fails because the dynamic linker's default search paths do not include wheel directories.
  2. Under `--config=off` (symlinked `site-packages/`), `onnxruntime/__init__.py`'s `__file__` is inside the merged `.venv` symlink tree, so `../nvidia/...` resolves to the symlinked `nvidia/` directories in `.venv` and `os.path.isfile(dll_path)` succeeds (matching a flat `pip` venv).
- **How `rules_pycross` handles it:** When `preload_dlls()` falls back to bare-name `ctypes.CDLL("libcublasLt.so.12")` after `os.path.isfile` misses inside `onnxruntime-gpu`'s wheel root, the import index's `_ctypes.dlopen` hook catches the failed bare-SONAME lookup, lazily loads the per-wheel `.pycross-native.json` dotfiles, and resolves each SONAME to its provider wheel in the binary's closure. Once `preload_dlls()` finishes, all 7 SONAMEs are registered in the dynamic linker's `link_map`, so `libonnxruntime_providers_cuda.so`'s `DT_NEEDED` entries resolve cleanly.
