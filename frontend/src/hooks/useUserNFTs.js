import { useState, useEffect } from "react";

// Helper to generate random bonds
const generateMockBonds = (count) => {
  const bonds = [];
  const currencies = ["USDC", "DAI", "USDT"];
  const now = new Date();

  for (let i = 1; i <= count; i++) {
    // Random maturity between -1 year (matured) and +5 years (active)
    const monthsOffset = Math.floor(Math.random() * 72) - 12;
    const maturityDate = new Date(now);
    maturityDate.setMonth(now.getMonth() + monthsOffset);

    // Purchase date 1 year before maturity
    const purchaseDate = new Date(maturityDate);
    purchaseDate.setFullYear(maturityDate.getFullYear() - 1);

    const isMatured = maturityDate < now;

    bonds.push({
      id: i,
      tokenId: (100 + i).toString(),
      principal: Math.floor(Math.random() * 9500) + 500, // 500 - 10000
      currency: currencies[Math.floor(Math.random() * currencies.length)],
      rate: parseFloat((Math.random() * 5 + 3).toFixed(2)), // 3.00% - 8.00%
      maturityDate: maturityDate.toISOString(),
      purchaseDate: purchaseDate.toISOString(),
      status: isMatured ? "MATURED" : "ACTIVE",
      image: null,
    });
  }
  return bonds;
};

const MOCK_NFTS = generateMockBonds(33);

export function useUserNFTs(account) {
  const [nfts, setNfts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!account) {
      setNfts([]);
      return;
    }

    const fetchNFTs = async () => {
      setLoading(true);
      setError(null);
      try {
        // Simulate network delay
        await new Promise((resolve) => setTimeout(resolve, 800));

        // For now, return mock data for any connected account
        setNfts(MOCK_NFTS);
      } catch (err) {
        console.error("Failed to fetch user NFTs:", err);
        setError(err);
      } finally {
        setLoading(false);
      }
    };

    fetchNFTs();
  }, [account]);

  return { nfts, loading, error };
}
