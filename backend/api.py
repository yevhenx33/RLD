from fastapi import FastAPI, HTTPException, Query, Request, Security, Depends, WebSocket, WebSocketDisconnect
import asyncio
import json
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import sqlite3
import pandas as pd
from datetime import datetime
import time
import os
from collections import defaultdict
import logging
import re
import json
from cachetools import TTLCache
from pydantic import BaseModel
from typing import Optional
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Import indexer modules
from indexer import init_indexer, get_indexer
import db

# Import market state modules (separate database)
from market_state_indexer import init_state_indexer, get_state_indexer, register_market_manually
from market_state_db import (
    init_market_state_db,
    get_all_markets_with_state,
    get_market_by_id,
    upsert_market,
    upsert_risk_params,
    insert_state_snapshot
)

# --- Logging Config ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Security: API Key ---
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    expected_key = os.getenv("API_KEY")
    if expected_key:
        if api_key_header != expected_key:
             raise HTTPException(status_code=403, detail="Invalid or Missing API Key")
    return api_key_header

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# Global indexer task
indexer_task = None

# --- Security: Rate Limiter ---
# Limit: 20 requests per 10 seconds per IP
RATE_LIMIT_WINDOW = 10 
RATE_LIMIT_MAX_REQUESTS = 20
request_history = defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    
    # Filter out requests older than window
    request_history[client_ip] = [t for t in request_history[client_ip] if now - t < RATE_LIMIT_WINDOW]
    
    # Check limit
    if len(request_history[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return JSONResponse(status_code=429, content={"error": "Too Many Requests. Please slow down."})
    
    # Record request
    request_history[client_ip].append(now)
    
    # Prevent memory leak (simple cleanup)
    if len(request_history) > 5000:
        request_history.clear()

    response = await call_next(request)
    return response

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Hide server signature
    if "server" in response.headers:
        del response.headers["server"]
    return response

# 1. Security & Compression
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0", "testserver", "rate-dashboard.onrender.com"]
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://rate-dashboard.netlify.app",
        "https://rate-dashboard.onrender.com",
        "https://www.rate-dashboard.onrender.com",
        "https://rld.fi",
        "https://www.rld.fi"
    ],
    allow_methods=["*"], # Allow all methods (specifically OPTIONS for preflight)
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    last_block = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_state WHERE key='last_block_number'")
        row = cursor.fetchone()
        if row:
            last_block = int(row['value'])
        conn.close()
    except Exception as e:
        logging.error(f"Health check db error: {e}")
        
    return {"status": "ok", "message": "Rate Dashboard API is running", "last_indexed_block": last_block}

# Switch to Clean DB
DB_NAME = "clean_rates.db"
DB_DIR = os.getenv("DB_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(DB_DIR, DB_NAME)

def get_db_connection():
    # Use URI for Read-Only mode validation
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn

# --- Web3 Setup (For Deployment) ---
# Load env vars from contracts/.env
load_dotenv("../contracts/.env")
# FORCE LOCALHOST FOR SIMULATION (User requested local deployment)
RPC_URL = "http://127.0.0.1:8545" 
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
account = None
if PRIVATE_KEY:
    try:
        account = Account.from_key(PRIVATE_KEY)
        logging.info(f"✅ Loaded Deployer: {account.address}")
    except Exception as e:
        logging.error(f"❌ Invalid Private Key: {e}")

# Load ABIs
# Load ABIs
CORE_ABI = []
FACTORY_ABI = []
ORACLE_ABI = []
ADDRESSES = {}
FACTORY_ADDRESS = None
CORE_ADDRESS = None

try:
    with open("../contracts/out/RLDMarketFactory.sol/RLDMarketFactory.json") as f:
        FACTORY_ABI = json.load(f)["abi"]
    with open("../contracts/out/RLDCore.sol/RLDCore.json") as f:
        CORE_ABI = json.load(f)["abi"]
    with open("../contracts/out/RLDAaveOracle.sol/RLDAaveOracle.json") as f:
        ORACLE_ABI = json.load(f)["abi"]
    with open("../shared/addresses.json") as f:
        ADDRESSES = json.load(f)
        FACTORY_ADDRESS = ADDRESSES.get("RLDMarketFactory")
        CORE_ADDRESS = ADDRESSES.get("RLDCore")
    logging.info(f"✅ Loaded ABIs - Core: {CORE_ADDRESS}, Factory: {FACTORY_ADDRESS}")
except Exception as e:
    logging.warning(f"⚠️ Could not load ABIs or Addresses: {e}")

# Global task handles
indexer_task = None
state_indexer_task = None

# Startup event: Initialize and start indexers
@app.on_event("startup")
async def startup_event_indexers():
    global indexer_task, state_indexer_task
    
    # Original event indexer
    if FACTORY_ADDRESS and FACTORY_ABI:
        try:
            indexer = init_indexer(RPC_URL, FACTORY_ADDRESS, FACTORY_ABI)
            indexer_task = asyncio.create_task(indexer.start())
            logging.info("✅ Event Indexer started")
        except Exception as e:
            logging.error(f"❌ Failed to start event indexer: {e}")
    
    # NEW: Market State Indexer (separate database)
    if CORE_ADDRESS and CORE_ABI and FACTORY_ADDRESS and FACTORY_ABI:
        try:
            state_indexer = init_state_indexer(
                RPC_URL, 
                CORE_ADDRESS, 
                FACTORY_ADDRESS,
                CORE_ABI, 
                FACTORY_ABI
            )
            state_indexer_task = asyncio.create_task(state_indexer.start())
            logging.info("✅ Market State Indexer started (separate DB)")
        except Exception as e:
            logging.error(f"❌ Failed to start state indexer: {e}")
    else:
        logging.warning("⚠️ State Indexer not started - missing Core address or ABI")
    
    # Start WebSocket broadcast task
    asyncio.create_task(broadcast_rates())

@app.on_event("shutdown")
async def shutdown_event_indexers():
    # Stop original indexer
    indexer = get_indexer()
    if indexer:
        indexer.stop()
    if indexer_task:
        indexer_task.cancel()
    
    # Stop state indexer
    state_indexer = get_state_indexer()
    if state_indexer:
        state_indexer.stop()
    if state_indexer_task:
        state_indexer_task.cancel()
    
    logging.info("🛑 All indexers stopped")

# Deployment Models
class MarketParams(BaseModel):
    lending_protocol: str # "AAVE"
    target_market: str    # "aUSDC"
    collateral_token: str # "USDC" (for now, mapped manually)
    initial_price: str    # "4.50"
    min_col_ratio: str    # "150"
    maintenance_margin: str # "110"
    liq_close_factor: str # "50"
    debt_cap: str         # "1000000"
    funding_period: str   # "86400"

@app.post("/deploy-market", dependencies=[Depends(get_api_key)])
async def deploy_market(params: MarketParams):
    if not w3.is_connected():
        raise HTTPException(status_code=500, detail="RPC Connection Failed")
    if not account:
        raise HTTPException(status_code=500, detail="Server Wallet Not Configured")
    if not FACTORY_ADDRESS:
        raise HTTPException(status_code=500, detail="Factory Address Not Found")

    try:
        logging.info(f"🚀 Deploying Market: {params.target_market}")

        # 1. Parse & Scale Parameters (Frontend sends Display values)
        # Convert "150" % -> 1.5e18 (WAD)
        # 150 / 100 * 1e18 = 1.5e18
        # Formula: value * 1e16
        min_col_wad = int(float(params.min_col_ratio) * 10**16)
        maint_margin_wad = int(float(params.maintenance_margin) * 10**16)
        liq_close_wad = int(float(params.liq_close_factor) * 10**16)
        
        debt_cap_raw = int(params.debt_cap) * 10**18 # Assuming 18 decimals for cap
        funding_period = int(params.funding_period)

        # 2. Resolve Addresses (Hardcoded for Anvil/Testnet Sim)
        # In a real app, these come from a config DB
        # MOCK ADDRESSES FOR ANVIL (Replace with real ones if needed)
        TOKEN_MAP = {
            "aUSDC": {
                "collateral": w3.to_checksum_address("0xFF00000000000000000000000000000000000001"), 
                "underlying": w3.to_checksum_address("0xFF00000000000000000000000000000000000002"), 
                "pool": w3.to_checksum_address("0xFF00000000000000000000000000000000000003")
            },
            "aUSDT": {
                 "collateral": w3.to_checksum_address("0xFF00000000000000000000000000000000000004"), 
                "underlying": w3.to_checksum_address("0xFF00000000000000000000000000000000000005"),
                "pool": w3.to_checksum_address("0xFF00000000000000000000000000000000000003")
            },
            "aDAI": {
                 "collateral": w3.to_checksum_address("0xFF00000000000000000000000000000000000006"), 
                "underlying": w3.to_checksum_address("0xFF00000000000000000000000000000000000007"),
                "pool": w3.to_checksum_address("0xFF00000000000000000000000000000000000003")
            }
        }
        
        market_config = TOKEN_MAP.get(params.target_market)
        if not market_config:
             raise HTTPException(status_code=400, detail="Unsupported Target Market")

        # 3. Deploy RLDAaveOracle (Rate Oracle)
        # Using the json artifact to get bytecode
        with open("../contracts/out/RLDAaveOracle.sol/RLDAaveOracle.json") as f:
            oracle_artifact = json.load(f)
            oracle_bytecode = oracle_artifact["bytecode"]["object"]
        
        OracleFactory = w3.eth.contract(abi=ORACLE_ABI, bytecode=oracle_bytecode)
        
        # Build Construct Tx
        # RLDAaveOracle is stateless (no constructor args in this version)
        construct_tx = OracleFactory.constructor().build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 2000000,
            'maxFeePerGas': w3.to_wei('2', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
        })
        
        # Sign & Send
        signed_tx = w3.eth.account.sign_transaction(construct_tx, PRIVATE_KEY)
        tx_hash_oracle = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt_oracle = w3.eth.wait_for_transaction_receipt(tx_hash_oracle)
        rate_oracle_address = receipt_oracle.contractAddress
        logging.info(f"✅ Deployed RLDAaveOracle: {rate_oracle_address}")

        # 4. Deploy Market via Factory
        factory_contract = w3.eth.contract(address=FACTORY_ADDRESS, abi=FACTORY_ABI)
        
        # Spot Oracle is 0x0 (1:1 Peg)
        spot_oracle_address = w3.to_checksum_address("0x0000000000000000000000000000000000000000")
        
        # Curator (Fee Receiver) -> Deployer
        curator_address = account.address
        
        # Liquidation Module -> Use RLDCore (implements liquidation interface)
        liq_module = w3.to_checksum_address(ADDRESSES.get("RLDCore"))
        
        # Construct Params Struct (Tuple)
        # struct DeployParams {
        #     address underlyingPool;
        #     address underlyingToken;
        #     address collateralToken;
        #     address curator;
        #     string positionTokenName;
        #     string positionTokenSymbol;
        #     uint64 minColRatio;
        #     uint64 maintenanceMargin;
        #     uint64 liquidationCloseFactor;
        #     address liquidationModule;
        #     bytes32 liquidationParams;
        #     address spotOracle;
        #     address rateOracle;
        #     uint32 oraclePeriod;
        #     uint24 poolFee;
        #     int24 tickSpacing;
        # }
        
        deploy_params = (
            market_config["pool"],          # underlyingPool
            market_config["underlying"],    # underlyingToken
            market_config["collateral"],    # collateralToken
            curator_address,                # curator
            f"Wrapped RLP: {params.target_market}", # name
            f"wRLP{params.target_market}",          # symbol
            min_col_wad,                    # minColRatio (1e18)
            maint_margin_wad,               # maintenanceMargin (1e18)
            liq_close_wad,                  # liquidationCloseFactor (1e18)
            liq_module,                     # liquidationModule
            b'\x00' * 32,                   # liquidationParams (empty)
            spot_oracle_address,            # spotOracle
            rate_oracle_address,            # rateOracle
            3600,                           # oraclePeriod (1h)
            3000,                           # poolFee (0.3%)
            60                              # tickSpacing
        )

        create_tx = factory_contract.functions.createMarket(deploy_params).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 5000000,
            'maxFeePerGas': w3.to_wei('2', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
        })

        signed_create_tx = w3.eth.account.sign_transaction(create_tx, PRIVATE_KEY)
        tx_hash_create = w3.eth.send_raw_transaction(signed_create_tx.raw_transaction)
        
        # Don't wait for receipt to keep UI snappy, return Hash immediately
        # (Or wait 1 sec to get Market ID if possible? Factory returns (MarketId, address))
        # Better to wait to confirm success.
        receipt_create = w3.eth.wait_for_transaction_receipt(tx_hash_create)
        
        # Decode Event to get Market ID? 
        # Alternatively, find the MarketDeployed event in the logs.
        # For prototype, just return success.
        
        # Store in Simulation List (Persistent JSON)
        sim_data = {
            "id": receipt_create.transactionHash.hex(),
            "target_market": params.target_market,
            "rate_oracle": rate_oracle_address,
            "status": "Running",
            "timestamp": int(time.time())
        }
        
        save_simulation(sim_data)

        return {
            "status": "success",
            "tx_hash": receipt_create.transactionHash.hex(),
            "rate_oracle": rate_oracle_address,
            "market_id": "0x..." # Placeholder
        }

    except Exception as e:
        logging.error(f"❌ Deployment Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/simulations")
def get_simulations():
    """Get all active simulations from the database."""
    try:
        markets = db.get_all_markets()
        
        # Transform to match frontend expected format
        simulations = []
        for market in markets:
            simulations.append({
                "id": market['tx_hash'],
                "target_market": market['position_token_symbol'],
                "rate_oracle": market['rate_oracle'],
                "status": "Running",  # Could be derived from market state
                "timestamp": market['deployment_timestamp']
            })
        
        return simulations
    except Exception as e:
        logging.error(f"Error fetching simulations: {e}")
        return []

@app.get("/simulations/enriched")
def get_enriched_simulations():
    """
    Get all simulations with live market state data.
    Uses the separate market_state.db database.
    Returns enriched data including normalizationFactor, totalDebt, risk params.
    """
    try:
        markets = get_all_markets_with_state()
        
        enriched = []
        for market in markets:
            # Format normalization factor
            nf_raw = int(market.get('normalization_factor') or 0)
            nf_display = nf_raw / 1e18 if nf_raw else 0
            accrued_interest_pct = (nf_display - 1) * 100 if nf_display > 0 else 0
            
            # Format total debt
            total_debt_raw = int(market.get('total_debt') or 0)
            total_debt_display = total_debt_raw / 1e18 if total_debt_raw else 0
            
            # Format risk params (WAD -> percentage)
            min_col_raw = int(market.get('min_col_ratio') or 0)
            maint_margin_raw = int(market.get('maintenance_margin') or 0)
            liq_close_raw = int(market.get('liquidation_close_factor') or 0)
            
            min_col_pct = min_col_raw / 1e16 if min_col_raw else 0
            maint_margin_pct = maint_margin_raw / 1e16 if maint_margin_raw else 0
            liq_close_pct = liq_close_raw / 1e16 if liq_close_raw else 0
            
            funding_period = market.get('funding_period') or 0
            funding_period_days = funding_period / 86400 if funding_period else 0
            
            # Format last update timestamp
            state_last_update = market.get('state_last_update')
            last_update_display = ""
            if state_last_update:
                from datetime import datetime
                last_update_display = datetime.utcfromtimestamp(state_last_update).strftime('%Y-%m-%d %H:%M:%S')
            
            enriched.append({
                "id": market.get('tx_hash') or market.get('market_id'),
                "market_id": market.get('market_id'),
                "target_market": market.get('position_token_symbol') or "Unknown",
                "broker_factory": market.get('broker_factory'),
                "position_token": market.get('position_token'),
                "collateral_token": market.get('collateral_token'),
                "underlying_token": market.get('underlying_token'),
                "curator": market.get('curator'),
                "status": "Running",
                "timestamp": market.get('deployment_timestamp'),
                "state": {
                    "normalization_factor": str(nf_raw),
                    "normalization_factor_display": f"{nf_display:.6f}",
                    "accrued_interest_pct": f"{accrued_interest_pct:.4f}%",
                    "total_debt": str(total_debt_raw),
                    "total_debt_display": f"{total_debt_display:.2f}",
                    "last_update": last_update_display,
                    "last_update_timestamp": state_last_update,
                    "block_number": market.get('state_block')
                },
                "risk_params": {
                    "min_col_ratio": min_col_pct,
                    "min_col_ratio_display": f"{min_col_pct:.0f}%",
                    "maintenance_margin": maint_margin_pct,
                    "maintenance_margin_display": f"{maint_margin_pct:.0f}%",
                    "liquidation_close_factor": liq_close_pct,
                    "liquidation_close_factor_display": f"{liq_close_pct:.0f}%",
                    "funding_period_seconds": funding_period,
                    "funding_period_days": funding_period_days,
                    "debt_cap": market.get('debt_cap'),
                    "broker_verifier": market.get('broker_verifier')
                },
                "oracles": {
                    "spot_oracle": market.get('spot_oracle'),
                    "rate_oracle": market.get('rate_oracle')
                }
            })
        
        return enriched
    except Exception as e:
        logging.error(f"Error fetching enriched simulations: {e}", exc_info=True)
        return []

@app.get("/market/{market_id}/state")
def get_market_state(market_id: str):
    """
    Get the current state for a specific market.
    Uses the separate market_state.db database.
    """
    try:
        market = get_market_by_id(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")
        
        # Format similar to enriched endpoint
        nf_raw = int(market.get('normalization_factor') or 0)
        nf_display = nf_raw / 1e18 if nf_raw else 0
        accrued_interest = (nf_display - 1) * 100 if nf_display > 0 else 0
        
        return {
            "market_id": market_id,
            "state": {
                "normalization_factor": str(nf_raw),
                "normalization_factor_display": f"{nf_display:.6f}",
                "accrued_interest_pct": f"{accrued_interest:.4f}%",
                "total_debt": str(market.get('total_debt') or 0),
                "last_update_timestamp": market.get('state_last_update'),
                "block_number": market.get('state_block')
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching market state: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/market/register")
async def register_market(market_id: str):
    """
    Manually register a market that was deployed before the indexer started.
    """
    if not w3.is_connected():
        raise HTTPException(status_code=500, detail="RPC not connected")
    if not CORE_ADDRESS or not CORE_ABI:
        raise HTTPException(status_code=500, detail="Core contract not configured")
    
    try:
        core_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CORE_ADDRESS),
            abi=CORE_ABI
        )
        
        success = register_market_manually(market_id, w3, core_contract)
        if success:
            return {"status": "success", "market_id": market_id}
        else:
            raise HTTPException(status_code=400, detail="Failed to register market")
    except Exception as e:
        logging.error(f"Error registering market: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Persistence Utils ---
SIMULATIONS_FILE = "simulations.json"

def load_simulations():
    if not os.path.exists(SIMULATIONS_FILE):
        return []
    try:
        with open(SIMULATIONS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_simulation(data):
    current = load_simulations()
    current.insert(0, data) # Prepend new sim
    with open(SIMULATIONS_FILE, "w") as f:
        json.dump(current, f, indent=4)

# 3. Secure In-Memory Cache (TTL: 20s, Max: 1000 items)
CACHE_STORE = TTLCache(maxsize=1000, ttl=20)


def get_from_cache(key):
    return CACHE_STORE.get(key)

def set_cache(key, val):
    CACHE_STORE[key] = val

@app.get("/simulation/{tx_hash}")
def get_simulation_detail(tx_hash: str):
    """
    Fetch details for a specific market simulation.
    Crucially, this fetches the *actual* parameters from the blockchain
    by decoding the original deployment transaction.
    """
    if not w3.is_connected():
         raise HTTPException(status_code=500, detail="RPC Connection Failed")

    try:
        # 1. Fetch Transaction
        tx = w3.eth.get_transaction(tx_hash)
        
        # 2. Decode Input Data using Factory ABI
        # Factory ABI must be loaded.
        factory_contract = w3.eth.contract(abi=FACTORY_ABI)
        
        # decode_function_input returns (function_obj, dict_params)
        func_obj, decoded_params = factory_contract.decode_function_input(tx.input)
        
        # decoded_params is a dict: {'params': ( ...tuple... )}
        # The tuple matches DeployParams struct.
        # We need to map it back to readable keys.
        
        # Struct Definition:
        # struct DeployParams {
        #     address underlyingPool;
        #     address underlyingToken;
        #     address collateralToken;
        #     address curator;
        #     string positionTokenName;
        #     string positionTokenSymbol;
        #     uint64 minColRatio;
        #     uint64 maintenanceMargin;
        #     uint64 liquidationCloseFactor;
        #     address liquidationModule;
        #     bytes32 liquidationParams;
        #     address spotOracle;
        #     address rateOracle;
        #     uint32 oraclePeriod;
        #     uint24 poolFee;
        #     int24 tickSpacing;
        # }
        
        # The decoded_params['params'] will be a tuple or struct-like depending on web3 version.
        # usually it's a dict if ABI has names, or tuple.
        # params argument in createMarket is named 'params'.
        
        data = decoded_params.get('params')
        if not data:
             raise HTTPException(status_code=404, detail="Could not decode params")

        # Normalize Data (Handle BigInts, Bytes, etc.)
        def normalize(v):
            if isinstance(v, bytes):
                return v.hex()
            return v
            
        # If it's a tuple (which it likely is if using older web3 or specific ABI parsing), index it.
        # If it's a dict (newer web3 with ABI names), access keys.
        # Let's assume dict if names are present in ABI.
        # Checking logic by try/except or just constructing response.
        
        # Map manually if it's a dict (safer to just return the dict with normalized values)
        response = {}
        for k, v in data.items():
            response[k] = normalize(v)
            
        # Enrich with other data (Block number, etc)
        response["tx_hash"] = tx_hash
        response["block_number"] = tx.blockNumber
        
        # Scaling WADs back to percentages for display
        # minColRatio (1e18) -> %
        if 'minColRatio' in response:
            response['display_minColRatio'] = float(response['minColRatio']) / 10**16
        if 'maintenanceMargin' in response:
            response['display_maintenanceMargin'] = float(response['maintenanceMargin']) / 10**16
        if 'liquidationCloseFactor' in response:
            response['display_liquidationCloseFactor'] = float(response['liquidationCloseFactor']) / 10**16
            
        return response

    except Exception as e:
        logging.error(f"Error fetching sim detail: {e}")
        # Fallback for manual restore items that might not exist on current chain state
        # (e.g. if we restarted anvil strictly but kept json)
        # return basic info from JSON?
        # User explicitly asked for "obtained from blockchain".
        raise HTTPException(status_code=404, detail=f"Simulation data not found on chain: {e}")

@app.get("/simulation/{market_id}/enriched")
def get_simulation_detail_enriched(market_id: str):
    """
    Fetch enriched details for a specific market simulation.
    Uses the market_state.db for live state data.
    Works with both tx_hash and market_id formats.
    """
    try:
        # Normalize market_id format
        if not market_id.startswith('0x'):
            market_id = '0x' + market_id
        
        # First try to get from market_state DB
        market = get_market_by_id(market_id)
        
        if not market:
            raise HTTPException(status_code=404, detail="Market not found in state database")
        
        # Format the response with all available data
        nf_raw = int(market.get('normalization_factor') or 0)
        nf_display = nf_raw / 1e18 if nf_raw else 1.0
        accrued_interest_pct = (nf_display - 1) * 100 if nf_display > 0 else 0
        
        total_debt_raw = int(market.get('total_debt') or 0)
        total_debt_display = total_debt_raw / 1e18 if total_debt_raw else 0
        
        # Risk params
        min_col_raw = int(market.get('min_col_ratio') or 0)
        maint_margin_raw = int(market.get('maintenance_margin') or 0)
        liq_close_raw = int(market.get('liquidation_close_factor') or 0)
        
        min_col_pct = min_col_raw / 1e16 if min_col_raw else 0
        maint_margin_pct = maint_margin_raw / 1e16 if maint_margin_raw else 0
        liq_close_pct = liq_close_raw / 1e16 if liq_close_raw else 0
        
        funding_period = market.get('funding_period') or 0
        funding_period_days = funding_period / 86400 if funding_period else 0
        
        # Format last update
        state_last_update = market.get('state_last_update')
        last_update_display = ""
        if state_last_update:
            from datetime import datetime
            last_update_display = datetime.utcfromtimestamp(state_last_update).strftime('%Y-%m-%d %H:%M:%S UTC')
        
        # Fetch live prices from oracles
        prices = {
            "index_price": None,
            "index_price_display": "—",
            "mark_price": None,
            "mark_price_display": "—",
            "price_error": None
        }
        
        try:
            # Minimal ABI for oracle price calls
            rate_oracle_abi = [{"inputs":[{"name":"underlyingPool","type":"address"},{"name":"underlyingToken","type":"address"}],"name":"getIndexPrice","outputs":[{"name":"indexPrice","type":"uint256"}],"stateMutability":"view","type":"function"}]
            spot_oracle_abi = [{"inputs":[{"name":"collateralToken","type":"address"},{"name":"underlyingToken","type":"address"}],"name":"getSpotPrice","outputs":[{"name":"price","type":"uint256"}],"stateMutability":"view","type":"function"}]
            
            # Fetch index price from rate oracle
            rate_oracle_addr = market.get('rate_oracle')
            underlying_pool = market.get('underlying_pool')
            underlying_token = market.get('underlying_token')
            
            if rate_oracle_addr and underlying_pool and underlying_token:
                rate_oracle = w3.eth.contract(address=rate_oracle_addr, abi=rate_oracle_abi)
                try:
                    index_price_raw = rate_oracle.functions.getIndexPrice(underlying_pool, underlying_token).call()
                    prices["index_price"] = str(index_price_raw)
                    # Index price is in WAD (1e18) representing dollar price
                    # Per RLD paper: K=100, so 5% rate = $5.00 price
                    # MIN_PRICE floor = 1e14 = $0.0001
                    index_price_dollars = index_price_raw / 1e18
                    prices["index_price_display"] = f"${index_price_dollars:.4f}"
                except Exception as e:
                    logging.warning(f"Failed to fetch index price: {e}")
            
            # Fetch mark/spot price from spot oracle
            spot_oracle_addr = market.get('spot_oracle')
            collateral_token = market.get('collateral_token')
            
            if spot_oracle_addr and collateral_token and underlying_token:
                spot_oracle = w3.eth.contract(address=spot_oracle_addr, abi=spot_oracle_abi)
                try:
                    mark_price_raw = spot_oracle.functions.getSpotPrice(collateral_token, underlying_token).call()
                    prices["mark_price"] = str(mark_price_raw)
                    # Mark price is in WAD (1e18)
                    mark_price_display = mark_price_raw / 1e18
                    prices["mark_price_display"] = f"{mark_price_display:.6f}"
                except Exception as e:
                    logging.warning(f"Failed to fetch mark price: {e}")
                    
        except Exception as e:
            prices["price_error"] = str(e)
            logging.warning(f"Oracle price fetch error: {e}")
        
        response = {
            "market_id": market_id,
            "tx_hash": market.get('tx_hash'),
            "block_number": market.get('state_block'),
            
            # Token info
            "positionTokenSymbol": market.get('position_token_symbol') or "Unknown",
            "positionTokenName": market.get('position_token_symbol') or "Unknown Market",
            "positionToken": market.get('position_token'),
            "collateralToken": market.get('collateral_token'),
            "underlyingToken": market.get('underlying_token'),
            "underlyingPool": market.get('underlying_pool'),
            "curator": market.get('curator'),
            
            # Oracles
            "spotOracle": market.get('spot_oracle'),
            "rateOracle": market.get('rate_oracle'),
            "liquidationModule": market.get('liquidation_module'),
            
            # Live Market State
            "state": {
                "normalization_factor": str(nf_raw),
                "normalization_factor_display": f"{nf_display:.6f}",
                "accrued_interest_pct": f"{accrued_interest_pct:.4f}%",
                "total_debt": str(total_debt_raw),
                "total_debt_display": f"{total_debt_display:.2f}",
                "last_update": last_update_display,
                "last_update_timestamp": state_last_update,
                "block_number": market.get('state_block')
            },
            
            # Live Prices from Oracles
            "prices": prices,
            
            # Risk Parameters
            "risk_params": {
                "minColRatio": min_col_raw,
                "display_minColRatio": min_col_pct,
                "maintenanceMargin": maint_margin_raw,
                "display_maintenanceMargin": maint_margin_pct,
                "liquidationCloseFactor": liq_close_raw,
                "display_liquidationCloseFactor": liq_close_pct,
                "fundingPeriod": funding_period,
                "fundingPeriodDays": funding_period_days,
                "debtCap": market.get('debt_cap'),
                "brokerVerifier": market.get('broker_verifier')
            },
            
            # Deployment info
            "deployment_block": market.get('deployment_block'),
            "deployment_timestamp": market.get('deployment_timestamp')
        }
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching enriched sim detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching market details: {e}")

# 4. WebSocket Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # If sending fails, we might want to disconnect or ignore
                pass

manager = ConnectionManager()

@app.websocket("/ws/rates")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# 5. Background Broadcast Loop
async def broadcast_rates():
    last_rates = {}
    while True:
        try:
            # Poll every 5 seconds (fast enough for 12s indexer)
            await asyncio.sleep(5)
            
            # Use existing cache or DB
            # We want LATEST rates for ALL assets.
            # Efficient query: Get latest 1 row from hourly_stats
            conn = get_db_connection()
            # Order DESC to get latest
            df = pd.read_sql_query("SELECT * FROM hourly_stats ORDER BY timestamp DESC LIMIT 1", conn)
            conn.close()
            
            if not df.empty:
                latest = df.iloc[0].to_dict()
                
                # Check for changes to avoid spam
                # Simple: compare timestamp
                if latest.get('timestamp') != last_rates.get('timestamp'):
                    last_rates = latest
                    
                    # Format for Frontend: { "USDC": 4.5, "ETH": 2000... }
                    # Or just send the whole object
                    payload = {
                        "type": "UPDATE",
                        "data": {
                            "timestamp": latest.get("timestamp"),
                            "USDC": latest.get("usdc_rate"),
                            "DAI": latest.get("dai_rate"),
                            "USDT": latest.get("usdt_rate"),
                            "SOFR": latest.get("sofr_rate"),
                            "ETH": latest.get("eth_price")
                        }
                    }
                    await manager.broadcast(payload)
                    
        except Exception as e:
            logging.error(f"WS Broadcast Error: {e}")
            await asyncio.sleep(5)

# Note: broadcast_rates is started in the main startup_event_indexers function above


@app.get("/rates")
def get_rates(
    limit: int = 50000, 
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D, 1W"),
    symbol: str = Query("USDC", description="USDC, DAI, USDT, SOFR")
):
    # Regex Validation for Dates (YYYY-MM-DD)
    date_pattern = r"^\d{4}-\d{2}-\d{2}$"
    if start_date and not re.match(date_pattern, start_date):
        raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    if end_date and not re.match(date_pattern, end_date):
        raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")

    try:
        # Cache Check
        cache_key = f"rates:{symbol}:{resolution}:{limit}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        # Map Symbol to Column
        symbol_map = {
            "USDC": "usdc_rate",
            "DAI": "dai_rate",
            "USDT": "usdt_rate",
            "SOFR": "sofr_rate"
        }
        
        target_col = symbol_map.get(symbol.upper())
        if not target_col:
            raise HTTPException(status_code=400, detail="Invalid Symbol")

        conn = get_db_connection()
        
        # Aggregation Logic
        # Database is already 1H resolution (hourly_stats)
        buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)
        
        # Select Clause
        if resolution == "1H":
             select_clause = f"timestamp, {target_col} as apy, eth_price"
             group_clause = ""
        else:
             # Downsampling using AVG
             select_clause = f"""
                MAX(timestamp) as timestamp, 
                AVG({target_col}) as apy, 
                AVG(eth_price) as eth_price
             """
             group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"

        # Limit Safety
        effective_limit = min(limit, 100000)

        # Build Query
        # Enforce Genesis Date: March 3, 2023 (1677801600)
        query = f"SELECT {select_clause} FROM hourly_stats WHERE timestamp >= 1677801600"
        params = []

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        query += f" {group_clause} ORDER BY timestamp DESC LIMIT {effective_limit}"
        
        # Execute
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        df = df.sort_values("timestamp", ascending=True)
        
        # Fill Gaps (Forward Fill)
        df['apy'] = df['apy'].ffill()
        
        # Convert to Dictionary (JSON)
        data = df.to_dict(orient="records")
        
        # Clean NaNs for JSON
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v: # check for NaN
                    row[k] = None
        
        set_cache(cache_key, data)
        return data
        
    except Exception as e:
        logging.error(f"ERROR in get_rates: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/eth-prices")
def get_eth_prices(
    limit: int = 50000,
    start_date: str = Query(None),
    end_date: str = Query(None),
    resolution: str = Query("1H", description="1H, 4H, 1D")
):
    try:
        # Cache Check
        cache_key = f"eth_prices:{resolution}:{limit}:{start_date}:{end_date}"
        cached = get_from_cache(cache_key)
        if cached:
            return cached

        conn = get_db_connection()
        
        buckets = {"1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
        seconds = buckets.get(resolution, 3600)
        
        if resolution == "1H":
            select_clause = "timestamp, eth_price as price"
            group_clause = ""
        else:
            select_clause = f"""
                MAX(timestamp) as timestamp, 
                AVG(eth_price) as price
            """
            group_clause = f"GROUP BY CAST(timestamp / {seconds} AS INTEGER)"
            
        params = []
        # Enforce Genesis Date: March 3, 2023 (1677801600)
        query = f"SELECT {select_clause} FROM hourly_stats WHERE timestamp >= 1677801600"

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            query += " AND timestamp >= ?"
            params.append(int(dt.timestamp()))
        
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            query += " AND timestamp <= ?"
            params.append(int(dt.timestamp()) + 86399)

        # ETH Prices usually displayed ASC for charts, but we apply limit to the LATEST
        # So effective query needed is: Get latest N, then sort ASC.
        # But simpler: Order DESC, Limit, then Sort Python side or Subquery.
        # Given existing logic, let's just apply LIMIT to the query string which uses ASC?
        # NO. ASC LIMIT N gets Oldest N. 
        # We need: SELECT * FROM (...) ORDER BY timestamp ASC.
        # For chart endpoint, preserving ASC default is good. 
        # But if LIMIT is small (e.g. 48), user usually implies "Latest 48".
        # Let's change ORDER to DESC for the query fetch, then sort ASC in Pandas if needed.
        query += f" {group_clause} ORDER BY timestamp DESC LIMIT {limit}"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return []

        data = df.to_dict(orient="records")
        for row in data:
            for k, v in row.items():
                if isinstance(v, float) and v != v:
                    row[k] = None
        
        set_cache(cache_key, data)
        return data
        
    except Exception as e:
        if "no such table" in str(e):
            return []
        logging.error(f"ERROR in get_eth_prices: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")