
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import config


def test_protocols_dict():
    """Verify PROTOCOLS registry has expected structure."""
    assert "aave_v3" in config.PROTOCOLS
    assert config.PROTOCOLS["aave_v3"]["enabled"] is True
    assert "fluid" in config.PROTOCOLS
    assert config.PROTOCOLS["fluid"]["enabled"] is False
    assert "euler" in config.PROTOCOLS
    assert config.PROTOCOLS["euler"]["enabled"] is False


def test_aave_v3_assets():
    """Verify Aave V3 assets are configured."""
    aave = config.PROTOCOLS["aave_v3"]
    assert "USDC" in aave["assets"]
    assert "DAI" in aave["assets"]
    assert "USDT" in aave["assets"]
    assert aave["pool_address"] == config.AAVE_POOL_ADDRESS


def test_standalone_sources():
    """Verify standalone data sources."""
    assert "ETH" in config.STANDALONE_SOURCES
    assert config.STANDALONE_SOURCES["ETH"]["type"] == "onchain_price"
    assert "sUSDe" in config.STANDALONE_SOURCES
    assert config.STANDALONE_SOURCES["sUSDe"]["type"] == "onchain_erc4626"
    assert "SOFR" in config.STANDALONE_SOURCES
    assert config.STANDALONE_SOURCES["SOFR"]["type"] == "offchain_api"


def test_backward_compat_assets():
    """Verify legacy ASSETS dict is populated from PROTOCOLS."""
    assert "USDC" in config.ASSETS
    assert config.ASSETS["USDC"]["table"] == "rates"
    assert config.ASSETS["USDC"]["protocol"] == "aave_v3"
    assert "SOFR" in config.ASSETS
    assert config.ASSETS["SOFR"]["type"] == "offchain_file"
    assert "sUSDe" in config.ASSETS
    assert config.ASSETS["sUSDe"]["type"] == "onchain_erc4626"


def test_contracts_defined():
    assert config.AAVE_POOL_ADDRESS.startswith("0x")
    assert config.UNI_POOL_ADDRESS.startswith("0x")
    assert config.SUSDE_ADDRESS.startswith("0x")


def test_db_name():
    assert config.DB_NAME == "aave_rates.db"
