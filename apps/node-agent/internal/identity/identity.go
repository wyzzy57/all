package identity

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"github.com/wyzzy57/all/apps/node-agent/internal/state"
)

const (
	enrollmentPath            = "/agent/v1/enroll"
	enrollmentTimeout         = 30 * time.Second
	maximumEnrollmentResponse = 1 << 20
)

func Ensure(
	ctx context.Context,
	cfg config.Config,
	facts protocol.EnrollmentFacts,
	store *state.Store,
	client *http.Client,
) (state.Identity, error) {
	if store == nil {
		return state.Identity{}, fmt.Errorf("identity store must not be nil")
	}
	stored, found, err := store.Identity()
	if err != nil {
		return state.Identity{}, fmt.Errorf("load identity: %w", err)
	}
	if found {
		return stored, nil
	}
	if err := validateFacts(facts); err != nil {
		return state.Identity{}, err
	}

	pending, pendingFound, err := store.PendingEnrollment()
	if err != nil {
		return state.Identity{}, fmt.Errorf("load pending enrollment: %w", err)
	}
	if !pendingFound {
		if strings.TrimSpace(cfg.EnrollmentToken) == "" {
			return state.Identity{}, fmt.Errorf("enrollment token is required before identity exists")
		}
		pending, err = newPendingEnrollment(cfg, facts)
		if err != nil {
			return state.Identity{}, err
		}
		if err := store.SavePendingEnrollment(pending); err != nil {
			return state.Identity{}, fmt.Errorf("persist pending enrollment: %w", err)
		}
	}
	privateKey, err := parsePendingPrivateKey(pending.PrivateKeyPEM)
	if err != nil {
		return state.Identity{}, fmt.Errorf("load pending enrollment key: %w", err)
	}

	requestBody, err := json.Marshal(protocol.EnrollmentRequest{
		ProtocolVersion:     protocol.ProtocolVersion,
		Token:               pending.Token,
		EnrollmentRequestID: pending.RequestID,
		NodeName:            pending.NodeName,
		Architecture:        pending.Architecture,
		PlatformKind:        pending.PlatformKind,
		AgentVersion:        pending.AgentVersion,
		CSRPEM:              pending.CSRPEM,
	})
	if err != nil {
		return state.Identity{}, fmt.Errorf("encode enrollment request: %w", err)
	}

	endpoint, err := enrollmentEndpoint(cfg.PlatformURL)
	if err != nil {
		return state.Identity{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(requestBody))
	if err != nil {
		return state.Identity{}, fmt.Errorf("create enrollment request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")

	httpClient := enrollmentHTTPClient(client)
	response, err := httpClient.Do(request)
	if err != nil {
		return state.Identity{}, fmt.Errorf("send enrollment request: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return state.Identity{}, fmt.Errorf("enrollment returned HTTP status %d", response.StatusCode)
	}

	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maximumEnrollmentResponse+1))
	if err != nil {
		return state.Identity{}, fmt.Errorf("read enrollment response: %w", err)
	}
	if len(responseBody) > maximumEnrollmentResponse {
		return state.Identity{}, fmt.Errorf("enrollment response exceeds 1 MiB")
	}
	var enrollment protocol.EnrollmentResponse
	decoder := json.NewDecoder(bytes.NewReader(responseBody))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&enrollment); err != nil {
		return state.Identity{}, fmt.Errorf("decode enrollment response: %w", err)
	}
	if err := requireJSONEnd(decoder); err != nil {
		return state.Identity{}, err
	}

	if enrollment.EnrollmentRequestID != pending.RequestID {
		return state.Identity{}, fmt.Errorf("enrollment response request ID does not match pending enrollment")
	}
	certificate, err := validateEnrollmentResponse(enrollment, privateKey.Public().(ed25519.PublicKey), cfg.AllowInsecureLocal)
	if err != nil {
		return state.Identity{}, err
	}
	identity := state.Identity{
		NodeID:                   enrollment.NodeID,
		PrivateKeyPEM:            pending.PrivateKeyPEM,
		CertificatePEM:           enrollment.CertificatePEM,
		CACertificatePEM:         enrollment.CACertificatePEM,
		CertificateExpiresAt:     certificate.NotAfter,
		GatewayURL:               enrollment.GatewayURL,
		HeartbeatIntervalSeconds: enrollment.HeartbeatIntervalSeconds,
	}
	if err := store.SaveIdentity(identity); err != nil {
		return state.Identity{}, fmt.Errorf("persist identity: %w", err)
	}
	return identity, nil
}

func newPendingEnrollment(cfg config.Config, facts protocol.EnrollmentFacts) (state.PendingEnrollment, error) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return state.PendingEnrollment{}, fmt.Errorf("generate identity key: %w", err)
	}
	privateKeyDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		return state.PendingEnrollment{}, fmt.Errorf("encode identity key: %w", err)
	}
	csrDER, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: cfg.NodeName},
	}, privateKey)
	if err != nil {
		return state.PendingEnrollment{}, fmt.Errorf("create certificate request: %w", err)
	}
	requestID, err := enrollmentRequestID()
	if err != nil {
		return state.PendingEnrollment{}, err
	}
	if !publicKey.Equal(privateKey.Public()) {
		return state.PendingEnrollment{}, fmt.Errorf("generated enrollment key is invalid")
	}
	return state.PendingEnrollment{
		RequestID:     requestID,
		Token:         cfg.EnrollmentToken,
		NodeName:      cfg.NodeName,
		Architecture:  facts.Architecture,
		PlatformKind:  facts.PlatformKind,
		AgentVersion:  cfg.AgentVersion,
		PrivateKeyPEM: string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateKeyDER})),
		CSRPEM:        string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csrDER})),
	}, nil
}

func enrollmentRequestID() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate enrollment request ID: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func parsePendingPrivateKey(privateKeyPEM string) (ed25519.PrivateKey, error) {
	block, rest := pem.Decode([]byte(privateKeyPEM))
	if block == nil || block.Type != "PRIVATE KEY" || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("private key is not valid PEM")
	}
	privateKey, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse private key: %w", err)
	}
	signer, ok := privateKey.(ed25519.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("private key is not Ed25519")
	}
	return signer, nil
}

func validateFacts(facts protocol.EnrollmentFacts) error {
	if facts.Architecture == "arm64" && facts.PlatformKind == "jetson" {
		return nil
	}
	if facts.Architecture == "amd64" && facts.PlatformKind == "x86_nvidia" {
		return nil
	}
	return fmt.Errorf("unsupported enrollment facts")
}

func enrollmentEndpoint(platformURL string) (string, error) {
	parsed, err := url.Parse(platformURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return "", fmt.Errorf("platform URL must be absolute")
	}
	return parsed.ResolveReference(&url.URL{Path: enrollmentPath}).String(), nil
}

func enrollmentHTTPClient(client *http.Client) *http.Client {
	if client == nil {
		client = http.DefaultClient
	}
	copy := *client
	copy.Timeout = enrollmentTimeout
	copy.CheckRedirect = func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return &copy
}

func requireJSONEnd(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("enrollment response contains multiple JSON values")
		}
		return fmt.Errorf("decode enrollment response trailer: %w", err)
	}
	return nil
}

func validateEnrollmentResponse(
	response protocol.EnrollmentResponse,
	generatedPublicKey ed25519.PublicKey,
	allowInsecureLocal bool,
) (*x509.Certificate, error) {
	if response.ProtocolVersion != protocol.ProtocolVersion {
		return nil, fmt.Errorf("unsupported enrollment protocol version")
	}
	if response.NodeID == "" {
		return nil, fmt.Errorf("enrollment response node ID must not be empty")
	}
	if response.EnrollmentRequestID == "" {
		return nil, fmt.Errorf("enrollment response request ID must not be empty")
	}
	if response.HeartbeatIntervalSeconds <= 0 {
		return nil, fmt.Errorf("enrollment heartbeat interval must be positive")
	}
	if err := validateGatewayURL(response.GatewayURL, allowInsecureLocal); err != nil {
		return nil, err
	}

	certificate, err := parseSingleCertificate(response.CertificatePEM, "device certificate")
	if err != nil {
		return nil, err
	}
	caCertificate, err := parseCACertificates(response.CACertificatePEM)
	if err != nil {
		return nil, err
	}
	if certificate.Subject.CommonName != response.NodeID {
		return nil, fmt.Errorf("device certificate common name does not match node ID")
	}
	if certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign != 0 {
		return nil, fmt.Errorf("device certificate must be an end-entity certificate")
	}
	if !hasClientAuthUsage(certificate.ExtKeyUsage) {
		return nil, fmt.Errorf("device certificate does not explicitly allow client authentication")
	}
	certificatePublicKey, ok := certificate.PublicKey.(ed25519.PublicKey)
	if !ok || !generatedPublicKey.Equal(certificatePublicKey) {
		return nil, fmt.Errorf("device certificate public key does not match generated private key")
	}

	roots := x509.NewCertPool()
	roots.AddCert(caCertificate)
	if _, err := certificate.Verify(x509.VerifyOptions{
		Roots:     roots,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}); err != nil {
		return nil, fmt.Errorf("verify device certificate chain: %w", err)
	}
	return certificate, nil
}

func parseSingleCertificate(certificatePEM, label string) (*x509.Certificate, error) {
	block, rest := pem.Decode([]byte(certificatePEM))
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 {
		return nil, fmt.Errorf("%s is not valid certificate PEM", label)
	}
	if len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("%s must contain exactly one certificate", label)
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", label, err)
	}
	return certificate, nil
}

func parseCACertificates(certificatePEM string) (*x509.Certificate, error) {
	block, rest := pem.Decode([]byte(certificatePEM))
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 {
		return nil, fmt.Errorf("CA certificate is not valid certificate PEM")
	}
	if len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("CA certificate PEM must contain exactly one certificate")
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse CA certificate: %w", err)
	}
	if !certificate.BasicConstraintsValid || !certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, fmt.Errorf("CA certificate does not permit certificate signing")
	}
	now := time.Now()
	if now.Before(certificate.NotBefore) || now.After(certificate.NotAfter) {
		return nil, fmt.Errorf("CA certificate is not currently valid")
	}
	if !bytes.Equal(certificate.RawIssuer, certificate.RawSubject) || certificate.CheckSignatureFrom(certificate) != nil {
		return nil, fmt.Errorf("CA certificate is not self-signed")
	}
	return certificate, nil
}

func hasClientAuthUsage(usages []x509.ExtKeyUsage) bool {
	for _, usage := range usages {
		if usage == x509.ExtKeyUsageClientAuth {
			return true
		}
	}
	return false
}

func validateGatewayURL(rawURL string, allowInsecureLocal bool) error {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Host == "" || parsed.User != nil || strings.HasSuffix(parsed.Host, ":") {
		return fmt.Errorf("gateway URL must be an absolute WebSocket URL")
	}
	hostname := strings.TrimSuffix(strings.ToLower(parsed.Hostname()), ".")
	if hostname == "" {
		return fmt.Errorf("gateway URL must be an absolute WebSocket URL")
	}
	switch strings.ToLower(parsed.Scheme) {
	case "wss":
		return nil
	case "ws":
		if allowInsecureLocal && isDevelopmentLocalHostname(hostname) {
			return nil
		}
	}
	return fmt.Errorf("gateway URL must use wss unless insecure local development is enabled")
}

func isDevelopmentLocalHostname(hostname string) bool {
	switch hostname {
	case "localhost", "127.0.0.1", "api-service", "host.docker.internal":
		return true
	default:
		return false
	}
}
