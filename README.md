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
# -> 3 of 6 tests FAIL: two cross-wheel regular-package collisions, plus the bare-SONAME
#    ctypes.CDLL check (which a plain pip venv fails too). Also spawns one symlink action per
#    linked path per binary.
bazel test --config=off //...

# Startup directory scan instead of the prebuilt native metadata (pure-Python resolution only)
# -> 2 of 6 tests FAIL (the two native tests)
bazel test --config=scan //...

# Plain per-wheel sys.path (venvs_site_packages=no, venv_import_index=off)
# -> 5 of 6 tests FAIL!
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

¹ Only the bare-SONAME `ctypes.CDLL("libnvJitLink.so.12")` check fails; a plain pip venv fails it too unless something already loaded the library. Cross-wheel `DT_NEEDED` resolution passes since rules_pycross 2.0.1 ([#364](https://github.com/jvolkman/rules_pycross/pull/364)).

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
