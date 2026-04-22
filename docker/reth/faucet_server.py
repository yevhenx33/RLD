#!/usr/bin/env python3
"""
Faucet Server — Atomic waUSDC funding via SimFunder
=====================================================
HTTP endpoint that atomically provisions waUSDC to requesting addresses
using the SimFunder contract: USDC → Aave supply → aUSDC → wrap → waUSDC.

Runs as a lightweight HTTP server on port 8088:
    POST /faucet  {"address": "0x..."}

Environment:
    WHALE_KEY         - Private key of the whale (funder) account
    RPC_URL           - Reth RPC endpoint (default: http://localhost:8545)
    FAUCET_PORT       - Port to listen on (default: 8088)
    SIM_FUNDER        - SimFunder contract address
    DEPLOYMENT_FILE   - Path to deployment-snapshot.json
"""

import json
import os
import sys
import time
import logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from web3 import Web3
from eth_account import Account

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
WHALE_KEY = os.getenv("WHALE_KEY")
SIMFUNDER_ADMIN_KEY = os.getenv("SIMFUNDER_ADMIN_KEY") or WHALE_KEY
FAUCET_PORT = int(os.getenv("FAUCET_PORT", "8088"))
DEPLOYMENT_FILE = os.getenv(
    "DEPLOYMENT_FILE",
    "/home/ubuntu/RLD/docker/reth/deployment-snapshot.json"
)
FAUCET_CORS_ORIGIN = os.getenv("FAUCET_CORS_ORIGIN", "https://rld.fi")

# Funding amounts per request
WAUSDC_FUND = 50_000 * 10**6    # 50k waUSDC (via SimFunder)
USDC_FUND = 50_000 * 10**6      # 50k USDC   (direct transfer)
ETH_AMOUNT = Web3.to_wei(10, 'ether')  # 10 ETH

# Rate limiting: 1 request per address per 60s
rate_limit = {}
RATE_LIMIT_SECONDS = 60

# ── ABIs ──────────────────────────────────────────────────────
SIM_FUNDER_ABI = [
    {
        "inputs": [
            {"name": "user", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "fund",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "amount", "type": "uint256"}],
        "name": "primeReserve",
        "outputs": [{"name": "mintedShares", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "user", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transferReserve",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "availableWAUSDC",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ERC20_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
]

# ── Globals ───────────────────────────────────────────────────
w3 = None
whale = None
simfunder_admin = None
sim_funder = None
usdc_contract = None
wausdc_addr = None
wausdc_contract = None


def init():
    """Initialize Web3, whale account, and SimFunder contract."""
    global w3, whale, simfunder_admin, sim_funder, usdc_contract, wausdc_addr, wausdc_contract

    if not WHALE_KEY:
        logger.error("WHALE_KEY not set")
        sys.exit(1)
    if not SIMFUNDER_ADMIN_KEY:
        logger.error("SIMFUNDER_ADMIN_KEY not set")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error(f"Cannot connect to {RPC_URL}")
        sys.exit(1)

    whale = Account.from_key(WHALE_KEY)
    logger.info(f"Whale: {whale.address}")
    simfunder_admin = Account.from_key(SIMFUNDER_ADMIN_KEY)
    logger.info(f"SimFunder admin: {simfunder_admin.address}")

    # Load deployment config
    try:
        with open(DEPLOYMENT_FILE) as f:
            deployment = json.load(f)
    except FileNotFoundError:
        logger.error(f"Deployment file not found: {DEPLOYMENT_FILE}")
        sys.exit(1)

    sim_funder_addr = os.getenv("SIM_FUNDER") or deployment.get("sim_funder")
    if not sim_funder_addr:
        logger.error("SIM_FUNDER not set and not in deployment-snapshot.json")
        sys.exit(1)

    # Poka-Yoke: verify SimFunder has deployed code (catches stale Anvil addresses)
    code = w3.eth.get_code(Web3.to_checksum_address(sim_funder_addr))
    if code == b'' or code == b'\x00':
        logger.error(f"SimFunder at {sim_funder_addr} has NO deployed code — stale address?")
        sys.exit(1)

    wausdc_addr = deployment.get("wausdc")
    usdc_addr = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

    sim_funder = w3.eth.contract(
        address=Web3.to_checksum_address(sim_funder_addr),
        abi=SIM_FUNDER_ABI,
    )
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(usdc_addr),
        abi=ERC20_ABI,
    )
    wausdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(wausdc_addr),
        abi=ERC20_ABI,
    )

    # Log state
    sf_bal = usdc_contract.functions.balanceOf(
        Web3.to_checksum_address(sim_funder_addr)
    ).call()
    logger.info(f"SimFunder: {sim_funder_addr}")
    logger.info(f"SimFunder USDC balance: ${sf_bal / 10**6:,.0f}")
    sf_wa = wausdc_contract.functions.balanceOf(
        Web3.to_checksum_address(sim_funder_addr)
    ).call()
    logger.info(f"SimFunder waUSDC balance: ${sf_wa / 10**6:,.0f}")
    logger.info(f"waUSDC: {wausdc_addr}")
    logger.info(f"Fund amounts: $50k waUSDC (SimFunder) + $50k USDC (direct) + 10 ETH")

    # Best-effort reserve priming at startup.
    try:
        available = sim_funder.functions.availableWAUSDC().call()
        if available < WAUSDC_FUND:
            logger.info("Priming SimFunder waUSDC reserve via primeReserve...")
            nonce = w3.eth.get_transaction_count(simfunder_admin.address)
            tx = sim_funder.functions.primeReserve(100_000_000 * 10**6).build_transaction({
                'from': simfunder_admin.address,
                'nonce': nonce,
                'gas': 3_000_000,
                'maxFeePerGas': w3.to_wei('2', 'gwei'),
                'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
            })
            signed = w3.eth.account.sign_transaction(tx, SIMFUNDER_ADMIN_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            if receipt['status'] == 1:
                new_avail = sim_funder.functions.availableWAUSDC().call()
                logger.info(f"Prime complete: reserve waUSDC=${new_avail/10**6:,.0f}")
            else:
                logger.warning("primeReserve tx reverted; fallback paths will be used")
    except Exception as e:
        logger.warning(f"primeReserve unavailable/failed: {e}")


def fund_address(address: str) -> dict:
    """Fund an address with waUSDC via SimFunder (atomic)."""
    address = Web3.to_checksum_address(address)
    results = {}

    # 1. Send ETH for gas
    try:
        nonce = w3.eth.get_transaction_count(whale.address)
        tx = {
            'to': address,
            'value': ETH_AMOUNT,
            'gas': 21000,
            'nonce': nonce,
            'maxFeePerGas': w3.to_wei('2', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
            'chainId': w3.eth.chain_id,
        }
        signed = w3.eth.account.sign_transaction(tx, WHALE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        results['eth'] = '10 ETH sent'
        logger.info(f"  ✅ Sent 10 ETH to {address}")
    except Exception as e:
        results['eth_error'] = str(e)
        logger.error(f"  ❌ ETH transfer failed: {e}")

    # 2. Atomic waUSDC funding via SimFunder (50k)
    try:
        nonce = w3.eth.get_transaction_count(whale.address)
        tx = sim_funder.functions.fund(address, WAUSDC_FUND).build_transaction({
            'from': whale.address,
            'nonce': nonce,
            'gas': 500_000,
            'maxFeePerGas': w3.to_wei('2', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
        })
        signed = w3.eth.account.sign_transaction(tx, WHALE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt['status'] != 1:
            results['wausdc_error'] = 'SimFunder.fund() reverted'
            logger.error("  ❌ SimFunder.fund() reverted")
        elif len(receipt.get('logs', [])) == 0:
            # Poka-Yoke: 0 Transfer logs means the call was a no-op
            # (e.g. calling a codeless address — EVM treats as plain ETH transfer)
            results['wausdc_error'] = 'SimFunder.fund() produced 0 events — contract may have no code'
            logger.error(f"  ❌ SimFunder.fund() tx succeeded but produced 0 logs — no tokens moved")
        else:
            results['wausdc'] = f'{WAUSDC_FUND / 10**6:,.0f} waUSDC funded'
            results['txHash'] = receipt['transactionHash'].hex()
            logger.info(f"  ✅ Funded {WAUSDC_FUND / 10**6:,.0f} waUSDC to {address} ({len(receipt['logs'])} events)")
    except Exception as e:
        results['wausdc_error'] = str(e)
        logger.error(f"  ❌ SimFunder.fund() failed: {e}")

    # 2b. Fallback: direct waUSDC transfer from whale if SimFunder path fails.
    # Keeps faucet usable in environments where Aave path is unavailable on Reth.
    if 'wausdc' not in results:
        try:
            nonce = w3.eth.get_transaction_count(simfunder_admin.address)
            tx = sim_funder.functions.transferReserve(address, WAUSDC_FUND).build_transaction({
                'from': simfunder_admin.address,
                'nonce': nonce,
                'gas': 220_000,
                'maxFeePerGas': w3.to_wei('2', 'gwei'),
                'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
            })
            signed = w3.eth.account.sign_transaction(tx, SIMFUNDER_ADMIN_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt['status'] == 1:
                results['wausdc'] = f'{WAUSDC_FUND / 10**6:,.0f} waUSDC sent (reserve fallback)'
                logger.info(f"  ✅ Reserve fallback sent {WAUSDC_FUND / 10**6:,.0f} waUSDC to {address}")
            else:
                logger.error("  ❌ waUSDC reserve fallback reverted")
        except Exception as e:
            results['wausdc_fallback_error'] = str(e)
            logger.error(f"  ❌ waUSDC reserve fallback failed: {e}")

    # 3. Direct USDC transfer (50k)
    try:
        nonce = w3.eth.get_transaction_count(whale.address)
        tx = usdc_contract.functions.transfer(address, USDC_FUND).build_transaction({
            'from': whale.address,
            'nonce': nonce,
            'gas': 100_000,
            'maxFeePerGas': w3.to_wei('2', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
        })
        signed = w3.eth.account.sign_transaction(tx, WHALE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        if receipt['status'] == 1:
            results['usdc'] = f'{USDC_FUND / 10**6:,.0f} USDC sent'
            logger.info(f"  ✅ Sent {USDC_FUND / 10**6:,.0f} USDC to {address}")
        else:
            results['usdc_error'] = 'USDC transfer reverted'
            logger.error("  ❌ USDC transfer reverted")
    except Exception as e:
        results['usdc_error'] = str(e)
        logger.error(f"  ❌ USDC transfer failed: {e}")

    return results


class FaucetHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/faucet':
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            address = data.get('address')
            if not address:
                raise ValueError("Missing 'address' field")

            # Rate limiting
            addr_lower = address.lower()
            now = time.time()
            last_request = rate_limit.get(addr_lower, 0)
            if now - last_request < RATE_LIMIT_SECONDS:
                wait = int(RATE_LIMIT_SECONDS - (now - last_request))
                raise ValueError(f"Rate limited. Try again in {wait}s")

            logger.info(f"🚰 Faucet request for {address}")
            rate_limit[addr_lower] = now
            result = fund_address(address)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', FAUCET_CORS_ORIGIN)
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, **result}).encode())

        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', FAUCET_CORS_ORIGIN)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', FAUCET_CORS_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Health check."""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            whale_addr = whale.address if whale else "not initialized"
            sf_addr = sim_funder.address if sim_funder else "not initialized"
            sf_admin = simfunder_admin.address if simfunder_admin else "not initialized"
            self.wfile.write(json.dumps({
                "status": "ok",
                "whale": whale_addr,
                "simFunderAdmin": sf_admin,
                "simFunder": sf_addr,
            }).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        """Suppress default access logs."""
        pass


def main():
    init()
    server = ThreadingHTTPServer(('0.0.0.0', FAUCET_PORT), FaucetHandler)
    logger.info(f"🚰 Faucet server listening on port {FAUCET_PORT}")
    logger.info(f"   POST /faucet  {{\"address\": \"0x...\"}}")
    logger.info(f"   GET  /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
