/**
 * Token Icon Registry
 *
 * Maps asset symbols to logo URLs using TrustWallet Assets CDN (only verified 200 OK).
 * Tokens without a TrustWallet entry get a clean generated avatar fallback.
 */

const TW = (addr) =>
  `https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/${addr}/logo.png`;

// Fallback: generates a clean letter-avatar with the symbol
const AVATAR = (symbol) =>
  `https://ui-avatars.com/api/?name=${encodeURIComponent(symbol)}&background=1a1a2e&color=fff&size=64&bold=true&font-size=0.4`;

/**
 * All addresses verified against TrustWallet CDN (HTTP 200).
 */
const TOKEN_ICONS = {
  // ─── Major Stablecoins ────────────────────────────────────
  USDC:     TW("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
  USDT:     TW("0xdAC17F958D2ee523a2206206994597C13D831ec7"),
  DAI:      TW("0x6B175474E89094C44Da98b954EedeAC495271d0F"),
  FRAX:     TW("0x853d955aCEf822Db058eb8505911ED77F175b99e"),
  LUSD:     TW("0x5f98805A4E8be255a32880FDeC7F6728C6568bA0"),
  PYUSD:    TW("0x6c3ea9036406852006290770BEdFcAbA0e23A0e8"),
  GHO:      TW("0x40D16FC0246aD3160Ccc09B8D0D3A2cD28aE6C2f"),
  USDS:     TW("0xdC035D45d973E3EC169d2276DDab16f1e407384F"),
  CRVUSD:   TW("0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E"),
  SDAI:     TW("0x83F20F44975D03b1b09e64809B757c47f942BEeA"),
  EURC:     TW("0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c"),

  // ─── Ethena Ecosystem ─────────────────────────────────────
  USDE:     TW("0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"),
  SUSDE:    TW("0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"),

  // ─── ETH & LSTs ────────────────────────────────────────────
  WETH:     TW("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
  WSTETH:   TW("0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"),
  CBETH:    "/icons/cbETH.png",
  EZETH:    TW("0xbf5495Efe5DB9ce00f80364C8B423567e58d2110"),
  WEETH:    TW("0xCd5fE23C85820F7B72D0926FC9b05b43E359b7ee"),

  // ─── BTC Variants ─────────────────────────────────────────
  WBTC:     TW("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"),
  TBTC:     TW("0x18084fbA666a33d37592fA2633fD49a74DD93a88"),
  EBTC:     TW("0x657e8C867D8B37dCC18fA4Caead9C45EB088C642"),
  CBBTC:    "/icons/cbBTC.png",
  LBTC:     "/icons/LBTC.png",
  FBTC:     "/icons/FBTC.png",

  // ─── DeFi Blue Chips ──────────────────────────────────────
  AAVE:     TW("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9"),
  LINK:     TW("0x514910771AF9Ca656af840dff83E8264EcF986CA"),
  UNI:      TW("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"),
  CRV:      TW("0xD533a949740bb3306d119CC777fa900bA034cd52"),
  SNX:      TW("0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F"),
  BAL:      TW("0xba100000625a3754423978a60c9317c58a424e3D"),
  LDO:      TW("0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32"),
  RPL:      TW("0xD33526068D116cE69F19A9ee46F0bd304F21A51f"),
  ENS:      TW("0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72"),
  KNC:      TW("0xdeFA4e8a7bcBA345F687a2f1456F5Edd9CE97202"),
  FXS:      TW("0x3432B6A60D23Ca0dFCa7761B7ab56459D9C964D0"),
  "1INCH":  TW("0x111111111117dC0aa78b770fA6A738034120C302"),
  STG:      TW("0xAf5191B0De278C7286d6C7CC6ab6BB8A73bA2Cd6"),
  WNXM:     TW("0x0d438F3b5175Bebc262bF23753C1E53d03432bDE"),
  FLUID:    TW("0x6f40d4A6237C257fff2dB00FA0510DeEECd303eb"),

  // ─── Verified niche tokens ────────────────────────────────
  USD0:     TW("0x73A15FeD60Bf67631dC6cd7Bc5B6e8da8190aCF5"),
  USUAL:    TW("0xC4441c2BE5d8fA8126822B9929CA0b81Ea0DE38E"),
  XAUT:     TW("0x68749665FF8D2d112Fa859AA293F07A622782F38"),
  PAXG:     TW("0x45804880De22913dAFE09f4980848ECE6EcbAf78"),
  RSETH:    "/icons/RSETH.png",
  EUSDE:    "/icons/eUSDE.png",
  AGEUR:    TW("0x1a7e4e63778B4f12a199C062f3eFdD288afCBce8"),
  "0X9F8F72": "/icons/MKR.png",
  MKR:      "/icons/MKR.png",
  "BTC.B":  "/icons/BTC_B.png",
  MUSD:     "/icons/MUSD.png",
  OSETH:    "/icons/OSETH.png",
  RETH:     "/icons/RETH.png",
  RLUSD:    "/icons/RLUSD.png",
  SFRAX:    "/icons/SFRAX.png",
  SYRUPUSDT: "/icons/SYRUPUSDT.png",
  TETH:     "/icons/TETH.png",
  USDG:     "/icons/USDG.png",
  USDTB:    "/icons/USDTB.png",
};

/**
 * Readable token names for display.
 */
const TOKEN_NAMES = {
  USDC: "USD Coin", USDT: "Tether USD", DAI: "Dai Stablecoin",
  FRAX: "Frax", LUSD: "Liquity USD", PYUSD: "PayPal USD",
  GHO: "Aave GHO", USDS: "Sky Dollar", CRVUSD: "Curve USD",
  SDAI: "Savings DAI", EUSD: "eUSD", EURC: "Euro Coin",
  USDE: "Ethena USDe", SUSDE: "Staked USDe", EUSDE: "Ethena eUSDe",
  WETH: "Wrapped Ether", WSTETH: "Wrapped stETH", RETH: "Rocket Pool ETH",
  CBETH: "Coinbase ETH", EZETH: "Renzo ETH", RSETH: "KelpDAO rsETH",
  WEETH: "ether.fi weETH", OSETH: "StakeWise osETH", ETHX: "Stader ETHx",
  MSETH: "Metastreet msETH", TETH: "Treehouse tETH",
  WBTC: "Wrapped BTC", TBTC: "tBTC v2", CBBTC: "Coinbase BTC",
  LBTC: "Lombard BTC", FBTC: "Ignition FBTC", EBTC: "eBTC",
  "BTC.B": "Bitcoin", AAVE: "Aave", LINK: "Chainlink",
  UNI: "Uniswap", CRV: "Curve DAO", SNX: "Synthetix",
  BAL: "Balancer", LDO: "Lido DAO", RPL: "Rocket Pool",
  ENS: "ENS", KNC: "Kyber Network", FXS: "Frax Share",
  "1INCH": "1inch", STG: "Stargate", WNXM: "Wrapped NXM",
  "0X9F8F72": "Maker (MKR)", USD0: "Usual USD0", USUAL: "Usual",
  FRXUSD: "Frax USD", RLUSD: "Ripple USD", RUSD: "Reservoir USD",
  MUSD: "mStable USD", MSUSD: "Metastreet USD", PMUSD: "Prime USD",
  LVLUSD: "Level USD", AUSD: "Agora USD", APXUSD: "Apex USD",
  USDF: "Fractal USD", USDG: "Paxos USDG", USDQ: "USDQ",
  USDR: "Real USD", USDU: "USDU", USR: "Resolv USR",
  USDTB: "Ethena USDtb", USDCV: "USDC Vault", EURCV: "EURC Vault",
  ZCHF: "Frankencoin", XAUT: "Tether Gold", SYRUPUSDT: "Syrup USDT",
  PAXG: "Paxos Gold", SUSDS: "Staked Sky Dollar", SYRUPUSDC: "Syrup USDC",
  BONDUSD: "BondUSD",
};

/**
 * Protocol display name mapping.
 */
const PROTOCOL_NAMES = {
  AAVE: "Aave V3",
  MORPHO: "Morpho",
  EULER: "Euler",
  FLUID: "Fluid",
};

/**
 * Returns the icon URL for a given token symbol.
 */
export function getTokenIcon(symbol) {
  if (!symbol) return AVATAR("?");
  const upper = symbol.toUpperCase();

  // Direct match
  if (TOKEN_ICONS[upper]) return TOKEN_ICONS[upper];

  // Pendle PT tokens → show the underlying asset icon
  if (upper.startsWith("PT-")) {
    const underlying = upper
      .replace("PT-", "")
      .replace(/-\d+\w+\d{4}$/i, ""); // strip date suffix
    if (TOKEN_ICONS[underlying]) return TOKEN_ICONS[underlying];
  }

  // Fallback: generated avatar
  return AVATAR(symbol);
}

/**
 * Returns a human-readable name for a token symbol.
 */
export function getTokenName(symbol) {
  if (!symbol) return "";
  const upper = symbol.toUpperCase();
  if (TOKEN_NAMES[upper]) return TOKEN_NAMES[upper];
  if (upper.startsWith("PT-")) return symbol;
  return symbol;
}

/**
 * Returns a clean display name for a protocol string like "AAVE_MARKET".
 */
export function getProtocolDisplayName(protocol) {
  if (!protocol) return "Unknown";
  const prefix = protocol.split("_")[0];
  return PROTOCOL_NAMES[prefix] || prefix;
}
