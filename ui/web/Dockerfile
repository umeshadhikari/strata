# Multi-stage build: compile the Angular app with Node, then serve the
# static bundle via nginx. nginx also reverse-proxies /api → the FastAPI
# container so the browser only ever talks to one origin (no CORS pain
# inside the Docker stack).

# ---------- build stage ----------
FROM node:20-alpine AS build
WORKDIR /src

COPY package.json ./
RUN npm install --no-audit --no-fund --loglevel=error

COPY angular.json tsconfig.json tsconfig.app.json ./
COPY src ./src

RUN npx --yes ng build --configuration production

# ---------- runtime stage ----------
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist/strata-ui/browser /usr/share/nginx/html
EXPOSE 80
