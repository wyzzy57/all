package config

import (
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
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
}

func Load() (Config, error) {
	cfg := Config{
		PlatformURL:     strings.TrimSpace(os.Getenv("VISIOX_AGENT_PLATFORM_URL")),
		NodeName:        strings.TrimSpace(os.Getenv("VISIOX_AGENT_NODE_NAME")),
		EnrollmentToken: strings.TrimSpace(os.Getenv("VISIOX_AGENT_ENROLLMENT_TOKEN")),
		StateDir:        strings.TrimSpace(os.Getenv("VISIOX_AGENT_STATE_DIR")),
		AgentVersion:    strings.TrimSpace(os.Getenv("VISIOX_AGENT_VERSION")),
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
	return cfg, nil
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
	case "localhost", "127.0.0.1", "api-service":
		return true
	default:
		return false
	}
}
