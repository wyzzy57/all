FROM node:24-alpine AS build

WORKDIR /app

COPY package.json ./package.json
COPY apps/frontend/package.json apps/frontend/package-lock.json ./apps/frontend/

WORKDIR /app/apps/frontend
RUN npm ci

COPY apps/frontend/ ./
RUN npm run build

FROM nginx:stable-alpine

COPY apps/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/frontend/dist /usr/share/nginx/html

EXPOSE 80
