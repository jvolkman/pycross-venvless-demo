"""Tests overlapping regular packages: functions-framework + google-cloud-functions.

Both `functions-framework` and `google-cloud-functions` ship non-empty
`google/cloud/functions/__init__.py` and `google/cloud/functions_v1/__init__.py`.

Under symlinked site-packages (`venvs_site_packages=yes` without the import index),
two wheels cannot both claim `google/cloud/functions`, requiring manual `site_paths`
overrides on `functions-framework`. With the rules_pycross import index, both wheels'
package directories are merged into `google.cloud.functions.__path__` in memory.
"""

import unittest


class FunctionsFrameworkOverlapTest(unittest.TestCase):
    def test_overlapping_google_cloud_functions_packages(self):
        import functions_framework
        import google.cloud.functions
        import google.cloud.functions_v1
        from google.cloud.functions.context import Context
        from google.cloud.functions_v1.context import Context as ContextV1
        from google.cloud.functions_v1.services.cloud_functions_service import (
            CloudFunctionsServiceClient,
        )
        from google.cloud.functions_v1.types import CloudFunction

        # Symbol from google-cloud-functions's google/cloud/functions/__init__.py
        self.assertTrue(hasattr(google.cloud.functions, "CloudFunctionsServiceClient"))
        self.assertIsNotNone(CloudFunctionsServiceClient)
        self.assertIsNotNone(CloudFunction)

        # Shims installed by functions-framework into google/cloud/functions{_v1}/
        ctx = Context(eventId="123", timestamp="2026-01-01T00:00:00Z")
        self.assertEqual(ctx.event_id, "123")
        self.assertIs(Context, ContextV1)

        self.assertTrue(callable(functions_framework.http))


if __name__ == "__main__":
    unittest.main()
