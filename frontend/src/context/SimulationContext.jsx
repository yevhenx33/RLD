/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext } from "react";
import { useSimulation } from "../hooks/useSimulation";

/**
 * SimulationContext — Single shared `useSimulation()` instance for the entire app.
 *
 * Before: 5+ separate useSimulation() hooks across pages, each making
 * its own set of API calls (5-7 endpoints × 5 instances = 25-35 calls).
 *
 * After: 1 instance shared via Context, all components consume the same data.
 * SWR deduplication handles the rest.
 */
const SimulationContext = createContext(null);

export function SimulationProvider({ children, pollInterval = 2000 }) {
  const sim = useSimulation({ pollInterval });
  return (
    <SimulationContext.Provider value={sim}>
      {children}
    </SimulationContext.Provider>
  );
}

/**
 * useSim() — Drop-in replacement for useSimulation().
 * Returns the exact same object shape as useSimulation().
 */
export function useSim() {
  const ctx = useContext(SimulationContext);
  if (!ctx) {
    throw new Error("useSim() must be used within <SimulationProvider>");
  }
  return ctx;
}
