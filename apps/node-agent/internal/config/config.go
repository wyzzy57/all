package config

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultStateDir     = "/var/lib/visiox-agent"
	defaultAgentVersion = "0.1.0"
)

type Config struct {
	PlatformURL        string
	NodeName           string
	EnrollmentToken    string
	StateDir           string
	AgentVersion       string
	AllowInsecureLocal bool
	ServerCAFile       string
}

func Load() (Config, error) {
	cfg := Config{
		PlatformURL:     strings.TrimSpace(os.Getenv("VISIOX_AGENT_PLATFORM_URL")),
		NodeName:        strings.TrimSpace(os.Getenv("VISIOX_AGENT_NODE_NAME")),
		EnrollmentToken: strings.TrimSpace(os.Getenv("VISIOX_AGENT_ENROLLMENT_TOKEN")),
		StateDir:        strings.TrimSpace(os.Getenv("VISIOX_AGENT_STATE_DIR")),
		AgentVersion:    strings.TrimSpace(os.Getenv("VISIOX_AGENT_VERSION")),
		ServerCAFile:    strings.TrimSpace(os.Getenv("VISIOX_AGENT_SERVER_CA_FILE")),
	}
	if cfg.StateDir == "" {
		cfg.StateDir = defaultStateDir
	}
	if cfg.AgentVersion == "" {
		cfg.AgentVersion = defaultAgentVersion
	}

	if raw := strings.TrimSpace(os.Getenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL")); raw != "" {
		allow, err := strconv.ParseBool(raw)
		if err != nil {
			return Config{}, fmt.Errorf("VISIOX_AGENT_ALLOW_INSECURE_LOCAL must be a boolean: %w", err)
		}
		cfg.AllowInsecureLocal = allow
	}

	var missing []string
	if cfg.PlatformURL == "" {
		missing = append(missing, "VISIOX_AGENT_PLATFORM_URL")
	}
	if cfg.NodeName == "" {
		missing = append(missing, "VISIOX_AGENT_NODE_NAME")
	}
	if len(missing) != 0 {
		return Config{}, fmt.Errorf("required environment variables are missing: %s", strings.Join(missing, ", "))
	}
	if err := validatePlatformURL(cfg.PlatformURL, cfg.AllowInsecureLocal); err != nil {
		return Config{}, err
	}
	if _, err := cfg.ServerTLSConfig(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func (c Config) ServerHTTPTransport() (*http.Transport, error) {
	tlsConfig, err := c.ServerTLSConfig()
	if err != nil {
		return nil, err
	}
	defaultTransport, ok := http.DefaultTransport.(*http.Transport)
	if !ok {
		return nil, fmt.Errorf("default HTTP transport is unavailable")
	}
	transport := defaultTransport.Clone()
	transport.TLSClientConfig = tlsConfig
	return transport, nil
}

func (c Config) ServerTLSConfig() (*tls.Config, error) {
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12}
	if c.ServerCAFile == "" {
		return tlsConfig, nil
	}
	serverCA, err := readServerCA(c.ServerCAFile)
	if err != nil {
		return nil, err
	}
	systemRoots, err := x509.SystemCertPool()
	if err != nil {
		return nil, fmt.Errorf("load operating-system certificate roots: %w", err)
	}
	if systemRoots == nil {
		return nil, fmt.Errorf("operating-system certificate roots are unavailable")
	}
	roots := systemRoots.Clone()
	roots.AddCert(serverCA)
	tlsConfig.RootCAs = roots
	return tlsConfig, nil
}

func readServerCA(path string) (*x509.Certificate, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("configured server CA is unavailable")
	}
	block, rest := pem.Decode(contents)
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("configured server CA is invalid")
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("configured server CA is invalid")
	}
	now := time.Now()
	if !certificate.BasicConstraintsValid || !certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, fmt.Errorf("configured server CA is invalid")
	}
	if now.Before(certificate.NotBefore) || now.After(certificate.NotAfter) {
		return nil, fmt.Errorf("configured server CA is invalid")
	}
	if certificate.CheckSignatureFrom(certificate) != nil {
		return nil, fmt.Errorf("configured server CA is invalid")
	}
	return certificate, nil
}

func validatePlatformURL(rawURL string, allowInsecureLocal bool) error {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return fmt.Errorf("VISIOX_AGENT_PLATFORM_URL must be an absolute URL")
	}

	switch strings.ToLower(parsed.Scheme) {
	case "https":
		return nil
	case "http":
		if allowInsecureLocal && isLocalDevelopmentHost(parsed.Hostname()) {
			return nil
		}
		return fmt.Errorf("VISIOX_AGENT_PLATFORM_URL must use HTTPS except for explicitly allowed local development hosts")
	default:
		return fmt.Errorf("VISIOX_AGENT_PLATFORM_URL must use HTTPS")
	}
}

func isLocalDevelopmentHost(host string) bool {
	switch strings.ToLower(host) {
	case "localhost", "127.0.0.1", "api-service", "host.docker.internal":
		return true
	default:
		return false
	}
}
