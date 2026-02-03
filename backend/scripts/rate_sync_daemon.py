#!/usr/bin/env python3
"""
Rate Sync Daemon - Syncs MockRLDAaveOracle with live mainnet Aave V3 rates.

Runs continuously, fetching the latest rate from the Render API every 12 seconds
and updating the mock oracle on the local Anvil fork.

Usage:
    python3 rate_sync_daemon.py

Environment:
    MOCK_ORACLE_ADDR - Address of deployed MockRLDAaveOracle
    PRIVATE_KEY      - Admin private key
    RPC_URL          - Anvil RPC (default: http://localhost:8545)
    API_URL          - Rate API URL (default: https://rate-dashboard.onrender.com)
    API_KEY          - API key for authentication
"""

import os
import sys
import time
import json
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv("../contracts/.env")
load_dotenv("../.env")

# Configuration
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
API_URL = os.getenv("API_URL", "https://rate-dashboard.onrender.com")
API_KEY = os.getenv("API_KEY", os.getenv("VITE_API_KEY"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
MOCK_ORACLE_ADDR = os.getenv("MOCK_ORACLE_ADDR")
SYNC_INTERVAL = 12  # seconds (block time)

# MockRLDAaveOracle ABI (minimal)
MOCK_ORACLE_ABI = [
    {
        "inputs": [{"name": "newRateRay", "type": "uint256"}],
        "name": "setRate",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "mockRateRay",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getRatePercent",
        "outputs": [{"name": "apyPercent", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]


def fetch_latest_rate():
    """Fetch latest USDC borrow rate from API."""
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        response = requests.get(
            f"{API_URL}/rates?limit=1&symbol=USDC",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if data and len(data) > 0:
            apy = data[0].get("apy", 0)
            timestamp = data[0].get("timestamp", 0)
            return apy, timestamp
        return None, None
    except Exception as e:
        logger.error(f"Failed to fetch rate: {e}")
        return None, None


def apy_to_ray(apy_percent):
    """
    Convert APY percentage to RAY format.
    
    Example: 4.64% -> 0.0464 * 1e27 = 4.64e25
    """
    return int(apy_percent / 100 * 1e27)


def ray_to_apy(ray):
    """Convert RAY to APY percentage."""
    return ray / 1e27 * 100


class RateSyncDaemon:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)
        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(MOCK_ORACLE_ADDR),
            abi=MOCK_ORACLE_ABI
        )
        self.last_rate = None
        self.running = True
        
    def get_current_oracle_rate(self):
        """Read current rate from mock oracle."""
        try:
            rate_ray = self.oracle.functions.mockRateRay().call()
            return rate_ray
        except Exception as e:
            logger.error(f"Failed to read oracle: {e}")
            return None
    
    def update_oracle_rate(self, new_rate_ray):
        """Send transaction to update oracle rate."""
        try:
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            
            tx = self.oracle.functions.setRate(new_rate_ray).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 100000,
                'maxFeePerGas': self.w3.to_wei('2', 'gwei'),
                'maxPriorityFeePerGas': self.w3.to_wei('1', 'gwei'),
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            
            return receipt.status == 1
        except Exception as e:
            logger.error(f"Failed to update oracle: {e}")
            return False
    
    def sync_once(self):
        """Perform one sync cycle."""
        # Fetch latest rate from API
        apy, timestamp = fetch_latest_rate()
        
        if apy is None:
            logger.warning("⚠️  No rate data available")
            return False
        
        # Convert to RAY
        new_rate_ray = apy_to_ray(apy)
        
        # Read current oracle rate for comparison
        current_oracle_rate = self.get_current_oracle_rate()
        current_apy = ray_to_apy(current_oracle_rate) if current_oracle_rate else 0
        
        # Always update on local testnet (removed threshold check)
        logger.info(f"📊 Syncing: API={apy:.4f}% | Oracle={current_apy:.4f}%")
        
        # Update oracle
        
        if self.update_oracle_rate(new_rate_ray):
            self.last_rate = new_rate_ray
            logger.info(f"✅ Oracle updated to {apy:.4f}%")
            return True
        else:
            logger.error("❌ Failed to update oracle")
            return False
    
    def run(self):
        """Run the sync daemon continuously."""
        logger.info("=" * 60)
        logger.info("🚀 Rate Sync Daemon Started")
        logger.info(f"   Oracle: {MOCK_ORACLE_ADDR}")
        logger.info(f"   API:    {API_URL}")
        logger.info(f"   Interval: {SYNC_INTERVAL}s")
        logger.info("=" * 60)
        
        # Initial sync
        self.sync_once()
        
        while self.running:
            try:
                time.sleep(SYNC_INTERVAL)
                self.sync_once()
            except KeyboardInterrupt:
                logger.info("🛑 Stopping daemon...")
                self.running = False
            except Exception as e:
                logger.error(f"Sync error: {e}")
                time.sleep(SYNC_INTERVAL)


def main():
    # Validate configuration
    if not MOCK_ORACLE_ADDR:
        logger.error("❌ MOCK_ORACLE_ADDR not set")
        logger.info("   Set it in .env or export MOCK_ORACLE_ADDR=0x...")
        sys.exit(1)
    
    if not PRIVATE_KEY:
        logger.error("❌ PRIVATE_KEY not set")
        sys.exit(1)
    
    # Check Web3 connection
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error(f"❌ Cannot connect to RPC: {RPC_URL}")
        logger.info("   Make sure Anvil is running")
        sys.exit(1)
    
    logger.info(f"✅ Connected to RPC at {RPC_URL}")
    logger.info(f"   Chain ID: {w3.eth.chain_id}")
    logger.info(f"   Block: {w3.eth.block_number}")
    
    # Run daemon
    daemon = RateSyncDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
