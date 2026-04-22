import fs from "fs";
import path from "path";
import zlib from "zlib";

const rootDir = process.cwd();
const distAssetsDir = path.join(rootDir, "dist", "assets");

const budgets = {
  maxEntryRawBytes: 330 * 1024,
  maxEntryGzipBytes: 110 * 1024,
  maxTotalJsRawBytes: 1900 * 1024,
  maxLargestChunkRawBytes: 520 * 1024,
};

function formatKiB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

if (!fs.existsSync(distAssetsDir)) {
  console.error(
    "Bundle budget check failed: dist assets not found. Run `npm run build` first.",
  );
  process.exit(1);
}

const files = fs.readdirSync(distAssetsDir).filter((f) => f.endsWith(".js"));
if (files.length === 0) {
  console.error("Bundle budget check failed: no JS bundles found in dist/assets.");
  process.exit(1);
}

const stats = files.map((file) => {
  const fullPath = path.join(distAssetsDir, file);
  const raw = fs.readFileSync(fullPath);
  const gzip = zlib.gzipSync(raw, { level: 9 });
  return {
    file,
    rawBytes: raw.length,
    gzipBytes: gzip.length,
  };
});

const entry =
  stats.find((s) => s.file.startsWith("index-")) ||
  [...stats].sort((a, b) => b.rawBytes - a.rawBytes)[0];

const totalJsRawBytes = stats.reduce((sum, s) => sum + s.rawBytes, 0);
const largestChunk = [...stats].sort((a, b) => b.rawBytes - a.rawBytes)[0];

const failures = [];

if (entry.rawBytes > budgets.maxEntryRawBytes) {
  failures.push(
    `Entry raw size ${formatKiB(entry.rawBytes)} exceeds budget ${formatKiB(budgets.maxEntryRawBytes)}.`,
  );
}
if (entry.gzipBytes > budgets.maxEntryGzipBytes) {
  failures.push(
    `Entry gzip size ${formatKiB(entry.gzipBytes)} exceeds budget ${formatKiB(budgets.maxEntryGzipBytes)}.`,
  );
}
if (totalJsRawBytes > budgets.maxTotalJsRawBytes) {
  failures.push(
    `Total JS raw size ${formatKiB(totalJsRawBytes)} exceeds budget ${formatKiB(budgets.maxTotalJsRawBytes)}.`,
  );
}
if (largestChunk.rawBytes > budgets.maxLargestChunkRawBytes) {
  failures.push(
    `Largest chunk ${largestChunk.file} is ${formatKiB(largestChunk.rawBytes)}, exceeds budget ${formatKiB(budgets.maxLargestChunkRawBytes)}.`,
  );
}

console.log("Bundle budget summary:");
console.log(`- Entry chunk: ${entry.file} (raw ${formatKiB(entry.rawBytes)}, gzip ${formatKiB(entry.gzipBytes)})`);
console.log(`- Total JS raw: ${formatKiB(totalJsRawBytes)}`);
console.log(`- Largest chunk: ${largestChunk.file} (${formatKiB(largestChunk.rawBytes)})`);

if (failures.length > 0) {
  console.error("\nBundle budget check failed:");
  failures.forEach((f) => console.error(`- ${f}`));
  process.exit(1);
}

console.log("Bundle budget check passed.");
