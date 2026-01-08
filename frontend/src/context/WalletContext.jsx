import React, { createContext, useContext, useState, useEffect } from 'react';
import { ethers } from 'ethers';

const WalletContext = createContext();

export function WalletProvider({ children }) {
    const [account, setAccount] = useState(null);
    const [provider, setProvider] = useState(null);
    const [balance, setBalance] = useState("0");
    const [usdcBalance, setUsdcBalance] = useState("0");
    const [chainId, setChainId] = useState(null);
    const [debugInfo, setDebugInfo] = useState("");

    // USDC Addresses by Chain ID
    const USDC_ADDRESSES = {
        1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", // Mainnet
        31337: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", // Anvil (Mainnet Fork)
        11155111: "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", // Sepolia (Native USDC)
        // Add Aave Sepolia USDC if needed: 0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8
    };

    const USDC_ABI = [
        "function balanceOf(address owner) view returns (uint256)",
        "function decimals() view returns (uint8)"
    ];

    const fetchBalances = async (acc, prov) => {
        if (!acc || !prov) return;
        
        // Get Network info for debug
        const net = await prov.getNetwork();
        setChainId(net.chainId.toString());

        // 1. Fetch ETH
        try {
            const bal = await prov.getBalance(acc);
            setBalance(ethers.formatEther(bal));
        } catch (err) {
            console.error("Failed to fetch ETH balance:", err);
        }

        // 2. Fetch USDC
        try {
            const currentChainId = net.chainId.toString(); // Ensure we use the freshly fetched ID
            const usdcAddr = USDC_ADDRESSES[currentChainId];

            if (!usdcAddr) {
                setDebugInfo(`No USDC Config for Chain ${currentChainId}`);
                console.warn(`No USDC address configured for chain ${currentChainId}`);
                setUsdcBalance("0.00");
                return;
            }

            const usdcContract = new ethers.Contract(usdcAddr, USDC_ABI, prov);
            // Verify contract exists
            const code = await prov.getCode(usdcAddr);
            if (code === "0x") {
                setDebugInfo(`USDC Contract Missing on ${currentChainId}`);
                console.warn("USDC Contract not found on this network!");
                return;
            }
            
            const usdcBal = await usdcContract.balanceOf(acc);
            setDebugInfo(`Connected: ${currentChainId}. Bal: ${usdcBal.toString()}`);
            
            // Decimals might vary on testnets, but usually 6. 
            // For safety we could fetch it, but let's stick to 6 for now or fetch if needed.
            // const decimals = await usdcContract.decimals(); 
            setUsdcBalance(ethers.formatUnits(usdcBal, 6)); 
        } catch (error) {
            console.error("Error fetching USDC balance:", error);
            setDebugInfo(`Error: ${error.message}`);
            setUsdcBalance("0.00"); 
        }
    };

    // Check if wallet is already connected
    useEffect(() => {
        if (window.ethereum) {
            const tempProvider = new ethers.BrowserProvider(window.ethereum);
            setProvider(tempProvider);
            
            // Auto-connect if already authorized
            window.ethereum.request({ method: 'eth_accounts' })
                .then(accounts => {
                    if (accounts.length > 0) {
                        setAccount(accounts[0]);
                        fetchBalances(accounts[0], tempProvider);
                    }
                })
                .catch(console.error);

            // Listen for accountsChanged
            const handleAccountsChanged = (accounts) => {
                const newAccount = accounts.length > 0 ? accounts[0] : null;
                setAccount(newAccount);
                if (newAccount) {
                    fetchBalances(newAccount, tempProvider);
                } else {
                    setBalance("0");
                    setUsdcBalance("0");
                }
            };
            
            // Listen for chainChanged (optional but good practice) to refetch? 
            // For now simple account change is enough.

            window.ethereum.on('accountsChanged', handleAccountsChanged);

            return () => {
                window.ethereum.removeListener('accountsChanged', handleAccountsChanged);
            };
        }
    }, []);

    const connectWallet = async () => {
        if (!provider) {
             if (window.ethereum) {
                 const tempProvider = new ethers.BrowserProvider(window.ethereum);
                 setProvider(tempProvider);
                 try {
                     const accounts = await tempProvider.send("eth_requestAccounts", []);
                     setAccount(accounts[0]);
                     fetchBalances(accounts[0], tempProvider);
                 } catch (error) {
                     console.error("Connection failed", error);
                 }
             } else {
                alert("No Ethereum wallet found. Please install MetaMask.");
             }
             return;
        }

        try {
            const accounts = await provider.send("eth_requestAccounts", []);
            setAccount(accounts[0]);
            fetchBalances(accounts[0], provider);
        } catch (error) {
            console.error("Connection failed", error);
        }
    };

    const switchNetwork = async () => {
        if (!provider) return;
        
        const ANVIL_CHAIN_ID_HEX = "0x7a69"; // 31337
        const MAINNET_CHAIN_ID_HEX = "0x1"; // 1

        const targetChainId = chainId === "31337" ? MAINNET_CHAIN_ID_HEX : ANVIL_CHAIN_ID_HEX;

        try {
            await provider.send("wallet_switchEthereumChain", [{ chainId: targetChainId }]);
        } catch (switchError) {
            // This error code indicates that the chain has not been added to MetaMask.
            if (switchError.code === 4902 || switchError.error?.code === 4902) {
                if (targetChainId === ANVIL_CHAIN_ID_HEX) {
                    try {
                        await provider.send("wallet_addEthereumChain", [
                            {
                                chainId: ANVIL_CHAIN_ID_HEX,
                                chainName: "Anvil Localhost",
                                rpcUrls: ["http://127.0.0.1:8545"],
                                nativeCurrency: {
                                    name: "ETH",
                                    symbol: "ETH",
                                    decimals: 18,
                                },
                            },
                        ]);
                    } catch (addError) {
                         console.error("Failed to add Anvil network:", addError);
                    }
                } else {
                    console.error("Target chain not found in wallet and cannot be auto-added (Mainnet typically pre-added).");
                }
            } else {
                console.error("Failed to switch network:", switchError);
            }
        }
    };

    const disconnect = () => {
        setAccount(null);
        setBalance("0");
        setUsdcBalance("0");
        setChainId(null);
        setDebugInfo("");
    };

    return (
        <WalletContext.Provider value={{ account, provider, balance, usdcBalance, chainId, debugInfo, connectWallet, disconnect, switchNetwork }}>
            {children}
        </WalletContext.Provider>
    );
}

export const useWallet = () => useContext(WalletContext);
