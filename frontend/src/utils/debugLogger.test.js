import assert from "node:assert/strict";
import test from "node:test";
import { debugLog, isDebugLoggingEnabled } from "./debugLogger.js";

test("isDebugLoggingEnabled accepts explicit true-like values", () => {
  assert.equal(isDebugLoggingEnabled("true"), true);
  assert.equal(isDebugLoggingEnabled("1"), true);
  assert.equal(isDebugLoggingEnabled("yes"), true);
  assert.equal(isDebugLoggingEnabled("false"), false);
});

test("debugLog only writes when enabled", () => {
  const original = console.log;
  const calls = [];
  console.log = (...args) => calls.push(args);
  const oldEnv = globalThis.process.env.VITE_DEBUG_LOGS;
  try {
    delete globalThis.process.env.VITE_DEBUG_LOGS;
    debugLog("off");
    globalThis.process.env.VITE_DEBUG_LOGS = "true";
    debugLog("on", 1);
  } finally {
    if (oldEnv === undefined) delete globalThis.process.env.VITE_DEBUG_LOGS;
    else globalThis.process.env.VITE_DEBUG_LOGS = oldEnv;
    console.log = original;
  }
  assert.deepEqual(calls, [["on", 1]]);
});
