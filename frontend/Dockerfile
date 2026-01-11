# Build Stage
FROM node:18-alpine as build

WORKDIR /app

# Copy package files from frontend directory
COPY frontend/package*.json ./
RUN npm install

# Copy frontend source code
COPY frontend/ .

# Copy shared directory to /app/shared (so ../../../shared works)
# Since WORKDIR is /app, we copy to ../shared which is /shared
# Use absolute path to be safe or copy to /app/shared
# The import is `../../../shared` from `src/hooks`.
# In container: /app/src/hooks -> ../../../ = /
# So we need shared to be at /shared
COPY shared/ /shared/

RUN npm run build

# Serve Stage
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Cloud Run expects port 8080
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
