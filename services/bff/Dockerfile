FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
# Static binary, no cgo, reproducible paths.
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/bff ./cmd/bff

# distroless: no shell, no package manager, non-root.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/bff /bff
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/bff"]
