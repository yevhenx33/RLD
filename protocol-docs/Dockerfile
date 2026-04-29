FROM node:20-alpine
WORKDIR /app
COPY . .
RUN npm ci
RUN npm run build
RUN cp -r .next/static .next/standalone/.next/static
RUN cp -r public .next/standalone/public 2>/dev/null || true
EXPOSE 3000
CMD ["node", ".next/standalone/server.js"]
