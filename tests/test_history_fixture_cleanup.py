"""Fixture connections must close even when garbage collection is delayed."""
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_messenger_history


class HistoryFixtureCleanupTests(unittest.TestCase):
    def test_history_case_closes_all_connections_without_collection(self):
        connections = []
        connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        case = test_messenger_history.HistoryTests(
            "test_signed_projection_cutoff_pagination_and_replay")
        try:
            with patch.object(sqlite3, "connect", side_effect=tracked_connect):
                result = unittest.TestResult()
                case.run(result)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        finally:
            for connection in connections:
                connection.close()
