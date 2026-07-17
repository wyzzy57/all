FROM golang:1.26.5-alpine AS builder

ARG TARGETOS=linux
ARG TARGETARCH=amd64

WORKDIR /src/apps/node-agent
COPY apps/node-agent/go.mod apps/node-agent/go.sum ./
RUN go mod download
COPY apps/node-agent ./
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w" -o /out/visiox-node-agent ./cmd/visiox-node-agent

FROM alpine:3.22

RUN addgroup -S visiox-agent && adduser -S -G visiox-agent visiox-agent \
    && install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
COPY --from=builder /out/visiox-node-agent /usr/local/bin/visiox-node-agent

ENTRYPOINT ["/usr/local/bin/visiox-node-agent"]
