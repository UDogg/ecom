FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/catalog-reader ./cmd/server

# distroless: no shell, no package manager, non-root.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/catalog-reader /catalog-reader
USER nonroot:nonroot
EXPOSE 8082
ENTRYPOINT ["/catalog-reader"]
