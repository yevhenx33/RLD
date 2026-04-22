import fs from "fs";
import path from "path";

const rootDir = process.cwd();
const srcDir = path.join(rootDir, "src");

function listSourceFiles(dirPath) {
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      files.push(...listSourceFiles(fullPath));
      continue;
    }
    if (/\.(js|jsx)$/.test(entry.name)) {
      files.push(fullPath);
    }
  }

  return files;
}

function getLayer(filePath) {
  const relative = path.relative(srcDir, filePath).replaceAll("\\", "/");
  if (relative.startsWith("app/")) return "app";
  if (relative.startsWith("api/")) return "api";
  if (relative.startsWith("pages/")) return "pages";
  if (relative.startsWith("features/")) return "features";
  if (relative.startsWith("hooks/queries/")) return "hooks-queries";
  if (relative.startsWith("hooks/")) return "hooks";
  if (relative.startsWith("components/shared/")) return "components-shared";
  if (relative.startsWith("components/")) return "components";
  if (relative.startsWith("charts/primitives/")) return "charts-primitives";
  if (relative.startsWith("charts/")) return "charts";
  return "other";
}

function resolveImport(fromFile, importPath) {
  if (!importPath.startsWith(".")) return null;
  const base = path.resolve(path.dirname(fromFile), importPath);
  const candidates = [
    base,
    `${base}.js`,
    `${base}.jsx`,
    path.join(base, "index.js"),
    path.join(base, "index.jsx"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function isViolation(fromLayer, toLayer, fromRelative, toRelative) {
  // pages are route entry modules; importing them from non-app surfaces causes architecture drift.
  if (toLayer === "pages" && fromLayer !== "app") {
    return "Only app layer may import pages modules.";
  }

  // query hooks should be data-only.
  if (
    fromLayer === "hooks-queries" &&
    ["components", "components-shared", "pages", "features"].includes(toLayer)
  ) {
    return "Query hooks cannot import UI/page modules.";
  }

  // API layer should stay transport-only.
  if (fromLayer === "api" && ["components", "pages", "features"].includes(toLayer)) {
    return "API layer cannot import UI/page modules.";
  }

  // Shared components should not depend on feature/page internals.
  if (
    fromLayer === "components-shared" &&
    ["pages", "features", "components"].includes(toLayer)
  ) {
    return "Shared components cannot import feature/page or non-shared component modules.";
  }

  // Features should not depend on pages.
  if (fromLayer === "features" && toLayer === "pages") {
    return "Features cannot import pages modules.";
  }

  // Chart primitives should remain low-level.
  if (fromLayer === "charts-primitives" && ["pages", "features"].includes(toLayer)) {
    return "Chart primitives cannot import pages/features modules.";
  }

  // Protect against accidental circular dependency through direct self import.
  if (fromRelative === toRelative) {
    return "File imports itself.";
  }

  return null;
}

if (!fs.existsSync(srcDir)) {
  console.error("Layer boundary check failed: src directory not found.");
  process.exit(1);
}

const files = listSourceFiles(srcDir);
const importRegex = /^\s*import\s+[\s\S]*?\sfrom\s+["']([^"']+)["'];?/gm;
const failures = [];

for (const file of files) {
  const content = fs.readFileSync(file, "utf8");
  const fromLayer = getLayer(file);
  const fromRelative = path.relative(srcDir, file).replaceAll("\\", "/");

  let match;
  while ((match = importRegex.exec(content)) !== null) {
    const importPath = match[1];
    const resolved = resolveImport(file, importPath);
    if (!resolved) continue;

    if (!resolved.startsWith(srcDir)) continue;
    const toLayer = getLayer(resolved);
    const toRelative = path.relative(srcDir, resolved).replaceAll("\\", "/");
    const violation = isViolation(fromLayer, toLayer, fromRelative, toRelative);
    if (violation) {
      failures.push(
        `${fromRelative} -> ${toRelative}: ${violation}`,
      );
    }
  }
}

if (failures.length > 0) {
  console.error("Layer boundary check failed:");
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}

console.log("Layer boundary check passed.");
