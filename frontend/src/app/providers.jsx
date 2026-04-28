import { SWRConfig } from "swr";
import ErrorBoundary from "./ErrorBoundary";
import { WalletProvider } from "../context/WalletContext";

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
  return (
    <ErrorBoundary>
      <SWRConfig value={swrConfig}>
        <WalletProvider>
          {children}
        </WalletProvider>
      </SWRConfig>
    </ErrorBoundary>
  );
}
