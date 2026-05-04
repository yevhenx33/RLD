const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

function readDebugEnv() {
  const viteValue = import.meta.env?.VITE_DEBUG_LOGS;
  const processValue = globalThis.process?.env?.VITE_DEBUG_LOGS;
  return viteValue ?? processValue ?? "";
}

export function isDebugLoggingEnabled(value = readDebugEnv()) {
  return TRUE_VALUES.has(String(value).trim().toLowerCase());
}

export function debugLog(...args) {
  if (isDebugLoggingEnabled()) {
    console.log(...args);
  }
}
