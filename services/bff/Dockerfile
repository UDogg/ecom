# The storefront (web/, passed in as the `web` build context), built and precompressed.
FROM node:24-alpine AS storefront
WORKDIR /web
COPY --from=web package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=web . .
RUN npm run build

FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
# Static binary, no cgo, reproducible paths.
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/bff ./cmd/bff

# distroless: no shell, no package manager, non-root.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/bff /bff
# Loaded into memory at start; the BFF serves it with the API from one origin.
COPY --from=storefront /web/dist /web
ENV WEB_ROOT=/web
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/bff"]
