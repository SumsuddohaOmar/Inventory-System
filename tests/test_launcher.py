"""Regression checks for reopening old code after a category update.

Mock only the operating-system/HTTP probes: verify the launcher decision without
opening a browser, binding a socket, or interrupting the user's running server.
"""

import io
import json
import unittest
from unittest.mock import patch
import launch


class LauncherTests(unittest.TestCase):
    """Recognize versions and prevent an obsolete API bypassing new accounts."""

    def test_legacy_server_blocks_startup_before_database_upgrade(self):
        old={"info":{"title":"Local Inventory · Phase 1","version":"0.3.0"}}
        with patch.object(launch,"build_opener") as opener, patch.object(launch.subprocess,"Popen") as start:
            opener.return_value.open.return_value=io.BytesIO(json.dumps(old).encode())
            self.assertEqual(launch.main(),1)
            start.assert_not_called()

    def test_reject_old_server_with_same_title(self):
        """The original bug: title alone incorrectly treated Phase 1 as current."""
        old = {"info": {"title": "Local Inventory · Phase 1", "version": "0.1.0"}, "paths": {}}
        with patch.object(launch, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(old).encode())
            self.assertFalse(launch.inventory_is_ready())

    def test_recognize_categories_version(self):
        """Only reuse a matching version that actually advertises categories."""
        current = {"info": {"title": "Local Inventory · Phase 1", "version": launch.VERSION}, "paths": {"/api/categories": {}}}
        with patch.object(launch, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(current).encode())
            self.assertTrue(launch.inventory_is_ready(8001))

    def test_old_occupied_port_uses_next_free_port(self):
        """A stale server at 8000 leaves the new app able to start at 8001."""
        with patch.object(launch, "inventory_is_ready", return_value=False), patch.object(
            launch, "port_is_available", side_effect=lambda port: port == 8001
        ):
            self.assertEqual(launch.choose_server(), (8001, False))

    def test_reuse_current_server_before_starting_another(self):
        """A subsequent click reuses the updated server rather than duplicating it."""
        with patch.object(launch, "inventory_is_ready", side_effect=lambda port: port == 8001), patch.object(
            launch, "port_is_available"
        ) as available:
            self.assertEqual(launch.choose_server(), (8001, True))
            available.assert_not_called()


if __name__ == "__main__":
    unittest.main()
