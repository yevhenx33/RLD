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
  ETH:      TW("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
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
  WSRUSD:   AVATAR("wsrUSD"),
  AA_FALCONXUSDC: AVATAR("AA"),
  STCUSD:   AVATAR("stcUSD"),
  STUSDS:   AVATAR("stUSDS"),
  MSY:      AVATAR("msY"),
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
  ETH: "Ether", WETH: "Wrapped Ether", WSTETH: "Wrapped stETH", RETH: "Rocket Pool ETH",
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
  WSRUSD: "Wrapped Savings rUSD",
  AA_FALCONXUSDC: "Pareto AA Tranche - FalconXUSDC",
  STCUSD: "Staked cap USD",
  STUSDS: "Staked USDS",
  MSY: "msY",
};

/**
 * Primary brand colors for token symbols (muted for dark UI).
 * Used across charts, breakdowns, and composition visualizations.
 */
const TOKEN_COLORS = {
  // ─── Stablecoins ───────────────────────────────────────────
  USDC:     "#2775ca",  // Circle blue
  USDT:     "#26a17b",  // Tether green
  DAI:      "#f5ac37",  // MakerDAO amber
  FRAX:     "#1a1a1a",  // Frax black
  LUSD:     "#2eb6ea",  // Liquity blue
  PYUSD:    "#0070e0",  // PayPal blue
  GHO:      "#9b72cb",  // Aave GHO purple
  USDS:     "#1bab6b",  // Sky green
  CRVUSD:   "#a56729",  // Curve brown-gold
  SDAI:     "#c9a227",  // Savings DAI gold
  EURC:     "#1e56a0",  // Euro Coin navy
  USDE:     "#1a1a2e",  // Ethena dark
  SUSDE:    "#2d2d44",  // Staked USDe dark purple
  EUSDE:    "#3a3a55",  // Ethena eUSDe
  FRXUSD:   "#1a1a1a",  // Frax USD
  RLUSD:    "#005b99",  // Ripple USD blue
  USD0:     "#3c3c5a",  // Usual USD0
  USUAL:    "#4b4b6a",  // Usual purple
  USDTB:    "#1a1a2e",  // Ethena USDtb
  USDG:     "#0058a3",  // Paxos USDG
  MUSD:     "#1e1e3a",  // mStable
  USDF:     "#4682b4",  // Fractal
  BONDUSD:  "#5a5a7a",  // BondUSD
  AUSD:     "#3a6ea5",  // Agora
  LVLUSD:   "#4a7a8a",  // Level
  USR:      "#3d8a6e",  // Resolv
  WSRUSD:   "#5b8fd9",
  AA_FALCONXUSDC: "#6d8fb3",
  STCUSD:   "#4c8f78",
  STUSDS:   "#2f9f72",
  MSY:      "#7c8594",

  // ─── ETH & LSTs ────────────────────────────────────────────
  ETH:      "#627eea",  // Ethereum blue
  WETH:     "#627eea",  // Wrapped Ether blue
  WSTETH:   "#00a3ff",  // Lido sky blue
  CBETH:    "#0052ff",  // Coinbase blue
  RETH:     "#f5a623",  // Rocket Pool orange
  EZETH:    "#69d2a0",  // Renzo green
  WEETH:    "#7c3aed",  // ether.fi violet
  OSETH:    "#2dd4bf",  // StakeWise teal
  RSETH:    "#3b9e6f",  // KelpDAO green
  TETH:     "#4a7a6a",  // Treehouse

  // ─── BTC Variants ─────────────────────────────────────────
  WBTC:     "#f09242",  // Wrapped BTC orange
  TBTC:     "#48466d",  // tBTC purple-gray
  EBTC:     "#7c5e2e",  // eBTC brown
  CBBTC:    "#0052ff",  // Coinbase blue
  LBTC:     "#c9a227",  // Lombard gold
  FBTC:     "#e88a3e",  // Ignition orange

  // ─── DeFi Blue Chips ──────────────────────────────────────
  AAVE:     "#b6509e",  // Aave magenta
  LINK:     "#2a5ada",  // Chainlink blue
  UNI:      "#ff007a",  // Uniswap hot pink
  CRV:      "#a56729",  // Curve brown
  SNX:      "#1e1a31",  // Synthetix dark
  BAL:      "#1e1e1e",  // Balancer black
  LDO:      "#00a3ff",  // Lido blue
  RPL:      "#f5a623",  // Rocket Pool orange
  ENS:      "#5298ff",  // ENS blue
  MKR:      "#1aab9b",  // Maker teal
  FLUID:    "#6366f1",  // Fluid indigo

  // ─── Gold & Commodities ───────────────────────────────────
  XAUT:     "#c9a227",  // Tether Gold
  PAXG:     "#e6c465",  // Paxos Gold
};

/**
 * Protocol display name mapping.
 */
const PROTOCOL_NAMES = {
  AAVE: "Aave V3",
  SPARK: "Spark",
  MORPHO: "Morpho",
  EULER: "Euler",
  FLUID: "Fluid",
  COMPOUND_V3: "Compound V3",
};

const PROTOCOL_ICONS = {
  AAVE: "https://icons.llama.fi/aave-v3.png",
  SPARK: "https://icons.llama.fi/sparklend.jpg",
  MORPHO: "https://icons.llama.fi/morpho-blue.png",
  FLUID: "https://icons.llama.fi/fluid-lending.png",
  EULER: "https://icons.llama.fi/euler-v2.png",
  COMPOUND_V3: "https://icons.llama.fi/compound-v3.png",
};

const CURATOR_ICONS = {
  ALPHA: "https://icons.llama.fi/alpha.jpg",
  APOSTRO: "https://icons.llama.fi/apostro.jpg",
  CLEARSTAR: "https://icons.llama.fi/clearstar.jpg",
  DFORCE: "https://icons.llama.fi/dforce.jpg",
  GAUNTLET: "https://icons.llama.fi/gauntlet.jpg",
  HAKUTORA: "https://icons.llama.fi/hakutora.jpg",
  HYPERITHM: "https://icons.llama.fi/hyperithm.jpg",
  KPK: "https://icons.llama.fi/kpk.jpg",
  LULO: "https://icons.llama.fi/lulo.png",
  PARITY: "https://icons.llama.fi/parity.jpg",
  SENTORA: "https://icons.llama.fi/sentora.jpg",
  SINGULARV: "https://icons.llama.fi/singularv.jpg",
  SPARK: "https://icons.llama.fi/sparklend.jpg",
  STEAKHOUSE: "https://icons.llama.fi/steakhouse-financial.jpg",
  SWISSBORG: "https://icons.llama.fi/swissborg.png",
  YEARN: "https://icons.llama.fi/yearn.jpg",
};

const CURATOR_ADDRESS_ICONS = {
  "0x0f963a8a8c01042b69054e787e5763abbb0646a3": CURATOR_ICONS.SPARK,
  "0x2413a57fbd695f6b13c1d8d7d30ee10fa61b1b02": CURATOR_ICONS.HAKUTORA,
  "0x37f170e090b64bd277e604af359fb5b675ad10ce": CURATOR_ICONS.LULO,
  "0x6788c8ad65e85cca7224a0b46d061ef7d81f9da5": CURATOR_ICONS.ALPHA,
  "0x72882eb5d27c7088dfa6dde941dd42e5d184f0ef": CURATOR_ICONS.CLEARSTAR,
  "0x75178137d3b4b9a0f771e0e149b00fb8167ba325": CURATOR_ICONS.HYPERITHM,
  "0x7e43df1c1c5a2245858b60d4655fda83704e4171": CURATOR_ICONS.KPK,
  "0x827e86072b06674a077f592a531dce4590adecdb": CURATOR_ICONS.STEAKHOUSE,
  "0x834e1c1ea40173b82106f9177646b66d96ae7de8": CURATOR_ICONS.KPK,
  "0x90d0f26025571295d18a6c041e47450b81886b51": CURATOR_ICONS.YEARN,
  "0x982032cd3c37f6733190db966db7db2e0d630715": CURATOR_ICONS.PARITY,
  "0x9e33faae38ff641094fa68c65c2ce600b3410585": CURATOR_ICONS.GAUNTLET,
  "0x9e396de3312d373b87f9bd8763fb48184b42aac0": CURATOR_ICONS.SENTORA,
  "0xc266b1181a80e84edc2c6596718e88e8115c1eaa": CURATOR_ICONS.KPK,
  "0xc684c6587712e5e7bdf9fd64415f23bd2b05faec": CURATOR_ICONS.SWISSBORG,
  "0xc8f742c45c7fc91fa8627f3135986cbf48c4dd43": CURATOR_ICONS.DFORCE,
  "0xc91578e51bce35844e345cc7f733b4bbf6721734": CURATOR_ICONS.APOSTRO,
  "0xd15f11b334e1e233127302e5f759c17da1260df5": CURATOR_ICONS.KPK,
  "0xe5aec7d0e795456f90cebefba56470f0e5dfc075": CURATOR_ICONS.KPK,
  "0xf8182e5827c06a47a985ec565a3bcd56437a97be": CURATOR_ICONS.KPK,
};

const protocolDisplayKey = (protocol) => {
  const normalized = String(protocol || "").toUpperCase();
  if (PROTOCOL_NAMES[normalized] || PROTOCOL_ICONS[normalized]) return normalized;
  if (normalized.startsWith("COMPOUND_V3")) return "COMPOUND_V3";
  if (normalized === "COMPOUND V3") return "COMPOUND_V3";
  return normalized.split("_")[0];
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
  const key = protocolDisplayKey(protocol);
  return PROTOCOL_NAMES[key] || key;
}

export function getProtocolIcon(protocol) {
  if (!protocol) return AVATAR("?");
  const key = protocolDisplayKey(protocol);
  return PROTOCOL_ICONS[key] || AVATAR(key || "?");
}

export function getCuratorIcon(curator, curatorAddress) {
  const address = String(curatorAddress || "").trim().toLowerCase();
  if (CURATOR_ADDRESS_ICONS[address]) return CURATOR_ADDRESS_ICONS[address];

  const name = String(curator || "").trim();
  if (!name) return AVATAR("?");
  const key = name.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return CURATOR_ICONS[key] || AVATAR(name);
}

/**
 * Returns the primary brand color for a token symbol.
 * Falls back to a deterministic hue derived from the symbol string.
 */
const FALLBACK_HUES = [
  "#6b7280", "#7c8594", "#5a6370", "#8a8f96", "#4b5563",
  "#6e7681", "#586572", "#7a8490", "#4f5b66", "#647080",
];
export function getTokenColor(symbol) {
  if (!symbol) return FALLBACK_HUES[0];
  const upper = symbol.toUpperCase();
  if (TOKEN_COLORS[upper]) return TOKEN_COLORS[upper];
  // Pendle PT tokens → use underlying color
  if (upper.startsWith("PT-")) {
    const underlying = upper.replace("PT-", "").replace(/-\d+\w+\d{4}$/i, "");
    if (TOKEN_COLORS[underlying]) return TOKEN_COLORS[underlying];
  }
  // Deterministic fallback from symbol hash
  let hash = 0;
  for (let i = 0; i < upper.length; i++) hash = (hash * 31 + upper.charCodeAt(i)) | 0;
  return FALLBACK_HUES[Math.abs(hash) % FALLBACK_HUES.length];
}
