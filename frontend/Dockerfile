# ═══════════════════════════════════════════════════════════════
# RLD Frontend — Multi-stage Docker Build
# ═══════════════════════════════════════════════════════════════
# Stage 1: Build Vite app with Node
# Stage 2: Serve with Nginx Alpine
#
# Build:  docker build --build-arg VITE_API_BASE_URL=https://rld.fi/api \
#                      --build-arg VITE_MAINNET_RPC_URL=https://eth.llamarpc.com \
#                      -f frontend/Dockerfile -t rld-frontend .
#
# Run:    docker run -p 3000:80 rld-frontend
# ═══════════════════════════════════════════════════════════════

# --- Stage 1: Build ---
FROM node:20-alpine AS builder

WORKDIR /app

# Install deps first (cache layer)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

# Copy source
COPY frontend/ .

# Vite reads VITE_* env vars at build time
ARG VITE_API_BASE_URL=https://rld.fi/api
ARG VITE_MAINNET_RPC_URL=https://eth.llamarpc.com

# Vite envDir points to '../', so write .env to parent
RUN echo "VITE_API_BASE_URL=$VITE_API_BASE_URL" > ../.env && \
    echo "VITE_MAINNET_RPC_URL=$VITE_MAINNET_RPC_URL" >> ../.env

RUN npm run build

# --- Stage 2: Serve ---
FROM nginx:alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Copy SPA nginx config
COPY frontend/nginx.conf /etc/nginx/conf.d/rld.conf

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s \
    CMD wget -qO- http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
