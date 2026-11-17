"""Run synthetic regression tests without probing the user's live model listeners."""
import unittest
from unittest.mock import patch
import app

if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(str(app.ROOT),pattern='test_*.py')
    # Ownership tests override this mock to exercise occupied/externally managed ports.
    with patch.object(app,'port_in_use',return_value=False):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
