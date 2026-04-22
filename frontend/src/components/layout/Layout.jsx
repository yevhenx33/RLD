import AppShell from "../../app/AppShell";
import { useEnvioStatus } from "../../hooks/queries/useEnvioStatus";

export default function Layout() {
  const { ratesLoaded, isCapped } = useEnvioStatus();
  return <AppShell ratesLoaded={ratesLoaded} isCapped={isCapped} />;
}
