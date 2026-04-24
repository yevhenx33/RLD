import json
import os
import sys
import unittest
import urllib.error
from decimal import Decimal
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rates_client import RateValidationPolicy, fetch_valid_rate_sample  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class TestRatesClient(unittest.TestCase):
    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_valid_sample_is_returned(self, mock_urlopen, _mock_time):
        mock_urlopen.return_value = _FakeResponse(
            {"borrow_apy": "0.125", "timestamp": 1700000000}
        )
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=900,
                require_timestamp=True,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNotNone(sample)
        self.assertEqual(sample.rate_fraction, Decimal("0.125"))
        self.assertEqual(sample.age_seconds, 100)

    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_percent_input_is_normalized(self, mock_urlopen, _mock_time):
        mock_urlopen.return_value = _FakeResponse(
            {"borrow_apy": "12.5", "timestamp": 1700000000}
        )
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=900,
                require_timestamp=True,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNotNone(sample)
        self.assertEqual(sample.rate_fraction, Decimal("0.125"))

    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_stale_rate_is_rejected(self, mock_urlopen, _mock_time):
        mock_urlopen.return_value = _FakeResponse(
            {"borrow_apy": "0.12", "timestamp": 1699990000}
        )
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=60,
                require_timestamp=True,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNone(sample)

    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_out_of_range_rate_is_rejected(self, mock_urlopen, _mock_time):
        mock_urlopen.return_value = _FakeResponse(
            {"borrow_apy": "0.90", "timestamp": 1700000000}
        )
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("0.20"),
                max_age_seconds=900,
                require_timestamp=True,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNone(sample)

    @patch("rates_client.urllib.request.urlopen")
    def test_missing_borrow_apy_is_rejected(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse({"timestamp": 1700000000})
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=900,
                require_timestamp=True,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNone(sample)

    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_missing_timestamp_allowed_when_policy_disabled(self, mock_urlopen, _mock_time):
        mock_urlopen.return_value = _FakeResponse({"borrow_apy": "0.11"})
        sample = fetch_valid_rate_sample(
            "http://rates:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=900,
                require_timestamp=False,
            ),
            env={},
            timeout_seconds=0.1,
        )
        self.assertIsNotNone(sample)
        self.assertIsNone(sample.timestamp)

    @patch("rates_client.time.time", return_value=1700000100)
    @patch("rates_client.urllib.request.urlopen")
    def test_fallback_endpoint_is_used_after_failure(self, mock_urlopen, _mock_time):
        mock_urlopen.side_effect = [
            urllib.error.URLError("unreachable"),
            _FakeResponse({"borrow_apy": "0.13", "timestamp": 1700000000}),
        ]
        sample = fetch_valid_rate_sample(
            "http://primary:5000",
            policy=RateValidationPolicy(
                min_rate=Decimal("0"),
                max_rate=Decimal("1"),
                max_age_seconds=900,
                require_timestamp=True,
            ),
            env={"RATES_API_BASE_URL": "http://secondary:5000"},
            timeout_seconds=0.1,
        )
        self.assertIsNotNone(sample)
        self.assertTrue(sample.endpoint.startswith("http://secondary:5000"))


if __name__ == "__main__":
    unittest.main()
