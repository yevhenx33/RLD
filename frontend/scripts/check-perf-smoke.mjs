import fs from "fs";
import path from "path";

const rootDir = process.cwd();
const assetsDir = path.join(rootDir, "dist", "assets");

const throughputBytesPerSecond = 1.6 * 1024 * 1024; // conservative broadband
const budgets = {
  maxEstimatedInitialLoadMs: 1800,
  maxKeyRouteChunkRawBytes: 700 * 1024,
};

function estimateTransferMs(bytes) {
  return (bytes / throughputBytesPerSecond) * 1000;
}

if (!fs.existsSync(assetsDir)) {
  console.error(
    "Perf smoke check failed: dist/assets not found. Run `npm run build` first.",
  );
  process.exit(1);
}

const jsFiles = fs.readdirSync(assetsDir).filter((f) => f.endsWith(".js"));
if (jsFiles.length === 0) {
  console.error("Perf smoke check failed: no JS assets found.");
  process.exit(1);
}

const stats = jsFiles.map((file) => {
  const rawBytes = fs.statSync(path.join(assetsDir, file)).size;
  return { file, rawBytes };
});

const entryChunk =
  stats.find((s) => s.file.startsWith("index-")) ||
  [...stats].sort((a, b) => b.rawBytes - a.rawBytes)[0];
const estimatedInitialLoadMs = estimateTransferMs(entryChunk.rawBytes);

const keyRouteHints = ["SimulationTerminal", "Markets", "PoolLP", "Bonds"];
const keyRouteChunks = stats.filter((s) =>
  keyRouteHints.some((hint) => s.file.includes(hint)),
);

const failures = [];

if (estimatedInitialLoadMs > budgets.maxEstimatedInitialLoadMs) {
  failures.push(
    `Estimated initial route transfer ${estimatedInitialLoadMs.toFixed(0)}ms exceeds ${budgets.maxEstimatedInitialLoadMs}ms.`,
  );
}

if (keyRouteChunks.length === 0) {
  failures.push(
    "No key route chunks found (SimulationTerminal/Markets/PoolLP/Bonds); verify lazy chunk boundaries.",
  );
}

for (const chunk of keyRouteChunks) {
  if (chunk.rawBytes > budgets.maxKeyRouteChunkRawBytes) {
    failures.push(
      `Key route chunk ${chunk.file} is ${(chunk.rawBytes / 1024).toFixed(1)} KiB, exceeds ${(budgets.maxKeyRouteChunkRawBytes / 1024).toFixed(1)} KiB.`,
    );
  }
}

console.log("Perf smoke summary:");
console.log(
  `- Entry chunk ${entryChunk.file}: ${(entryChunk.rawBytes / 1024).toFixed(1)} KiB raw (~${estimatedInitialLoadMs.toFixed(0)}ms transfer)`,
);
if (keyRouteChunks.length > 0) {
  console.log("- Key route chunks:");
  keyRouteChunks.forEach((chunk) => {
    console.log(
      `  - ${chunk.file}: ${(chunk.rawBytes / 1024).toFixed(1)} KiB`,
    );
  });
}

if (failures.length > 0) {
  console.error("\nPerf smoke check failed:");
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}

console.log("Perf smoke check passed.");
