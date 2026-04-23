import sys
import os
import unittest
from unittest.mock import patch, Mock

# Add backend to path to import combined_daemon
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.combined_daemon import fetch_latest_rate

class TestFetchLatestRate(unittest.TestCase):
    
    @patch('services.combined_daemon.requests.get')
    def test_fetch_success_primary(self, mock_get):
        """Test successful fetch from the primary internal Docker mesh endpoint."""
        mock_response = Mock()
        mock_response.json.return_value = {"symbol": "USDC", "borrow_apy": 0.1265}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        rate = fetch_latest_rate()
        
        self.assertEqual(rate, 0.1265)
        # Should succeed on the first attempt
        mock_get.assert_called_once_with("http://rld_graphql_api:5000/api/v1/oracle/usdc-borrow-apy", timeout=2)

    @patch('services.combined_daemon.requests.get')
    def test_fetch_success_fallback(self, mock_get):
        """Test successful fetch from the fallback endpoint if primary fails."""
        # Setup side effect: first call raises exception, second returns success
        mock_resp_success = Mock()
        mock_resp_success.json.return_value = {"symbol": "USDC", "borrow_apy": 0.1400}
        mock_resp_success.raise_for_status.return_value = None
        
        mock_get.side_effect = [Exception("Connection timeout"), mock_resp_success]

        rate = fetch_latest_rate()
        
        self.assertEqual(rate, 0.1400)
        self.assertEqual(mock_get.call_count, 2)

    @patch('services.combined_daemon.requests.get')
    def test_fetch_all_fail(self, mock_get):
        """Test behavior when all endpoints fail."""
        mock_get.side_effect = Exception("Network down")

        rate = fetch_latest_rate()
        
        self.assertIsNone(rate)
        self.assertEqual(mock_get.call_count, 2)
        
    @patch('services.combined_daemon.requests.get')
    def test_fetch_invalid_json(self, mock_get):
        """Test behavior when endpoint returns malformed response."""
        mock_response = Mock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_response

        rate = fetch_latest_rate()
        
        self.assertIsNone(rate)
        self.assertEqual(mock_get.call_count, 2)

if __name__ == '__main__':
    unittest.main()
