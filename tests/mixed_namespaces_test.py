"""Tests mixed namespace conventions: PEP 420, pkgutil.extend_path, pkg_resources, and *-nspkg.pth."""

import unittest


class MixedNamespacesTest(unittest.TestCase):
    def test_pep420_google_and_opentelemetry_namespaces(self):
        from google.cloud import pubsub_v1, storage
        from google.protobuf import timestamp_pb2
        from opentelemetry import trace
        from opentelemetry.sdk import trace as sdk_trace

        self.assertIsNotNone(storage.Client)
        self.assertIsNotNone(pubsub_v1.PublisherClient)
        self.assertIsNotNone(timestamp_pb2.Timestamp)
        self.assertIsNotNone(trace.get_tracer)
        self.assertIsNotNone(sdk_trace.TracerProvider)

    def test_zope_nspkg_pth_and_c_extension(self):
        # zope.interface ships both a compiled C extension (_zope_interface_coptimizations)
        # and a `zope.interface-*-nspkg.pth` file that pre-populates `sys.modules["zope"]`
        # during `site` initialization before `sitecustomize` runs.
        import zope.event
        import zope.interface
        from zope.interface import _zope_interface_coptimizations

        self.assertIsNotNone(zope.interface.Interface)
        self.assertIsNotNone(_zope_interface_coptimizations)
        self.assertTrue(callable(zope.event.notify))

    def test_pkgutil_extend_path_backports(self):
        # Both backports.tarfile and backports.entry_points_selectable ship
        # `backports/__init__.py` calling `pkgutil.extend_path(__path__, __name__)`.
        import backports
        import backports.entry_points_selectable
        import backports.tarfile

        self.assertGreaterEqual(len(backports.__path__), 2)
        self.assertIsNotNone(backports.tarfile.TarFile)
        self.assertIsNotNone(backports.entry_points_selectable.entry_points)


if __name__ == "__main__":
    unittest.main()
