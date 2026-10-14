"""Tests plugin subpackages installed inside another wheel's regular package.

`azure-core` ships `azure/core/tracing/ext/__init__.py` (making `azure.core.tracing.ext`
a regular package rather than a PEP 420 namespace). `azure-core-tracing-opentelemetry`
then installs `azure/core/tracing/ext/opentelemetry_span/` inside that directory.

Without multi-wheel regular-package `__path__` merging, importing `azure.core.tracing.ext`
binds `__path__` solely to `azure-core`'s wheel directory and makes
`azure.core.tracing.ext.opentelemetry_span` unimportable.
"""

import unittest


class AzurePluginSubpackageTest(unittest.TestCase):
    def test_plugin_inside_regular_package(self):
        import azure.core.tracing.ext
        from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan

        self.assertIsNotNone(azure.core.tracing.ext)
        self.assertIsNotNone(OpenTelemetrySpan)


if __name__ == "__main__":
    unittest.main()
