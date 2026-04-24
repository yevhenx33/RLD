import sys
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

# Add backend to path to import combined_daemon
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rates_client import RateSample
from services.combined_daemon import fetch_latest_rate

class TestFetchLatestRate(unittest.TestCase):
    @patch("services.combined_daemon.fetch_valid_rate_sample")
    def test_fetch_success_returns_rate_fraction(self, mock_fetch):
        mock_fetch.return_value = RateSample(
            endpoint="http://rld_graphql_api:5000/api/v1/oracle/usdc-borrow-apy",
            raw_rate=Decimal("0.1265"),
            rate_fraction=Decimal("0.1265"),
            timestamp=1700000000,
            age_seconds=5,
            payload={"borrow_apy": 0.1265, "timestamp": 1700000000},
        )
        rate = fetch_latest_rate()
        self.assertEqual(rate, 0.1265)
        mock_fetch.assert_called_once()

    @patch("services.combined_daemon.fetch_valid_rate_sample")
    def test_fetch_failure_returns_none(self, mock_fetch):
        mock_fetch.return_value = None
        rate = fetch_latest_rate()
        self.assertIsNone(rate)
        mock_fetch.assert_called_once()

if __name__ == '__main__':
    unittest.main()
