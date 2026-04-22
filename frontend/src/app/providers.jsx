import { SWRConfig } from "swr";
import { useLocation } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { WalletProvider } from "../context/WalletContext";
import { SimulationProvider } from "../context/SimulationContext";

const swrConfig = {
  revalidateOnFocus: false,
  shouldRetryOnError: true,
  errorRetryCount: 3,
  errorRetryInterval: 2000,
  keepPreviousData: true,
  onErrorRetry: (error, key, _config, revalidate, { retryCount }) => {
    const status = error?.status || error?.httpStatus;
    if (status && status >= 400 && status < 500 && status !== 429) {
      return;
    }
    if (retryCount >= 3) {
      return;
    }
    const waitMs = Math.min(2000 * 2 ** retryCount, 12000);
    setTimeout(() => {
      revalidate({ retryCount });
    }, waitMs);
  },
};

export default function AppProviders({ children }) {
  const location = useLocation();
  const isPublicSurface =
    location.pathname === "/" || location.pathname.startsWith("/intel");
  const simulationPollInterval = isPublicSurface ? 10000 : 2000;

  return (
    <ErrorBoundary>
      <SWRConfig value={swrConfig}>
        <WalletProvider>
          <SimulationProvider pollInterval={simulationPollInterval}>
            {children}
          </SimulationProvider>
        </WalletProvider>
      </SWRConfig>
    </ErrorBoundary>
  );
}
