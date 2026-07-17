FROM golang:1.26.5-alpine AS builder

ARG TARGETOS=linux
ARG TARGETARCH=amd64
ARG TEST_INVENTORY_FIXTURE_BUILD=false

WORKDIR /src/apps/node-agent
COPY apps/node-agent/go.mod apps/node-agent/go.sum ./
RUN go mod download
COPY apps/node-agent ./
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w -X main.testInventoryFixtureBuild=${TEST_INVENTORY_FIXTURE_BUILD}" -o /out/visiox-node-agent ./cmd/visiox-node-agent

FROM alpine:3.22

ARG AGENT_UID=10001
ARG AGENT_GID=10001

RUN addgroup -S -g "${AGENT_GID}" visiox-agent \
    && adduser -S -D -H -u "${AGENT_UID}" -G visiox-agent visiox-agent \
    && install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
COPY --from=builder /out/visiox-node-agent /usr/local/bin/visiox-node-agent

USER visiox-agent

ENTRYPOINT ["/usr/local/bin/visiox-node-agent"]
