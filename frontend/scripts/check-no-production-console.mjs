import fs from "fs";
import path from "path";

const rootDir = process.cwd();
const srcDir = path.join(rootDir, "src");
const allowedFile = path.join(srcDir, "utils", "debugLogger.js");
const failures = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full);
      continue;
    }
    if (!/\.(js|jsx)$/.test(entry.name)) continue;
    if (full === allowedFile || full.endsWith(".test.js")) continue;
    const lines = fs.readFileSync(full, "utf8").split("\n");
    lines.forEach((line, index) => {
      if (/\bconsole\.log\s*\(/.test(line)) {
        failures.push(`${path.relative(rootDir, full)}:${index + 1}: ${line.trim()}`);
      }
    });
  }
}

walk(srcDir);

if (failures.length > 0) {
  console.error("Production console.log check failed:");
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}

console.log("Production console.log check passed.");
