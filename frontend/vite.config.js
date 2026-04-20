import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";

// Custom plugin to serve clear bot log file
function clearBotLogsPlugin() {
  return {
    name: "clear-bot-logs",
    configureServer(server) {
      server.middlewares.use("/_logs/clear-bot", (req, res) => {
        const logPath = "/tmp/clear_bot.log";
        try {
          if (!fs.existsSync(logPath)) {
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ lines: [], total: 0 }));
            return;
          }
          const content = fs.readFileSync(logPath, "utf-8");
          const allLines = content.split("\n").filter((l) => l.trim());
          // Return last 200 lines
          const lines = allLines.slice(-200);
          res.writeHead(200, {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
          });
          res.end(JSON.stringify({ lines, total: allLines.length }));
        } catch (err) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
    },
  };
}

// Serve VitePress docs build at /docs during development
import path from "path";
import { fileURLToPath } from "url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const docsDistDir = path.resolve(__dirname, "../docs-site/.vitepress/dist");

function serveDocsPlugin() {
  return {
    name: "serve-docs",
    configureServer(server) {
      server.middlewares.use("/docs", (req, res, next) => {
        // Redirect /docs to /docs/
        if (req.url === "/" || req.url === "") {
          // Serve index.html
          req.url = "/index.html";
        }
        const filePath = path.join(docsDistDir, req.url.split("?")[0]);
        // Try exact file, then .html extension
        const candidates = [
          filePath,
          filePath + ".html",
          path.join(filePath, "index.html"),
        ];
        for (const candidate of candidates) {
          if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
            const ext = path.extname(candidate);
            const mimeTypes = {
              ".html": "text/html",
              ".js": "application/javascript",
              ".css": "text/css",
              ".json": "application/json",
              ".svg": "image/svg+xml",
              ".png": "image/png",
              ".woff2": "font/woff2",
              ".woff": "font/woff",
            };
            res.writeHead(200, {
              "Content-Type": mimeTypes[ext] || "application/octet-stream",
            });
            res.end(fs.readFileSync(candidate));
            return;
          }
        }
        next();
      });
    },
  };
}

// Copy VitePress docs build into dist/docs/ after production build
function copyDocsPlugin() {
  let isBuild = false;
  return {
    name: "copy-docs",
    configResolved(config) {
      isBuild = config.command === "build";
    },
    closeBundle() {
      if (!isBuild) return;
      const src = path.resolve(__dirname, "../docs-site/.vitepress/dist");
      const dest = path.resolve(__dirname, "dist/docs");
      if (fs.existsSync(src)) {
        fs.cpSync(src, dest, { recursive: true });
        console.log("✅ Docs copied to dist/docs/");
      } else {
        console.warn(
          "⚠️  docs-site dist not found — run `npm run build` in docs-site first",
        );
      }
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), clearBotLogsPlugin(), serveDocsPlugin(), copyDocsPlugin()],
  envDir: "../",
  server: {
    host: "0.0.0.0",
    proxy: {
      "/graphql": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
      "/rpc": {
        target: "http://127.0.0.1:8545",
        changeOrigin: true,
        rewrite: () => "", // Strip /rpc path — Anvil expects POST to /
      },
      "/api/rates": {
        target: "http://127.0.0.1:8081",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/api/ws": {
        target: "http://127.0.0.1:8081",
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/api/faucet": {
        target: "http://127.0.0.1:8088",
        changeOrigin: true,
        rewrite: () => "/faucet",
      },
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
      "/rates-graphql": {
        target: "http://127.0.0.1:8081",
        changeOrigin: true,
        rewrite: () => "/graphql",
      },
      "/envio-graphql": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true,
        rewrite: () => "/graphql",
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom"],
          "vendor-charts": ["recharts"],
          "vendor-utils": ["ethers", "lucide-react", "axios", "swr"],
        },
      },
    },
  },
});
