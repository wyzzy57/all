package config

import (
	"strings"
	"testing"
)

func TestLoadRequiresPlatformURLAndNodeName(t *testing.T) {
	t.Setenv("VISIOX_AGENT_PLATFORM_URL", "")
	t.Setenv("VISIOX_AGENT_NODE_NAME", "")

	_, err := Load()
	if err == nil {
		t.Fatal("expected validation error")
	}
}

func TestLoadAppliesDefaults(t *testing.T) {
	setRequiredEnvironment(t, "https://platform.example.com", "edge-01")
	t.Setenv("VISIOX_AGENT_STATE_DIR", "")
	t.Setenv("VISIOX_AGENT_VERSION", "")
	t.Setenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL", "")

	got, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.StateDir != "/var/lib/visiox-agent" {
		t.Fatalf("unexpected state directory: %q", got.StateDir)
	}
	if got.AgentVersion != "0.1.0" {
		t.Fatalf("unexpected agent version: %q", got.AgentVersion)
	}
	if got.AllowInsecureLocal {
		t.Fatal("insecure local mode must default to false")
	}
}

func TestLoadRejectsInsecurePlatformURLByDefault(t *testing.T) {
	setRequiredEnvironment(t, "http://localhost:8000", "edge-01")
	t.Setenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL", "")

	_, err := Load()
	if err == nil || !strings.Contains(err.Error(), "HTTPS") {
		t.Fatalf("expected HTTPS validation error, got %v", err)
	}
}

func TestLoadAllowsExplicitInsecureLocalPlatformURL(t *testing.T) {
	for _, host := range []string{"localhost", "127.0.0.1", "api-service"} {
		t.Run(host, func(t *testing.T) {
			setRequiredEnvironment(t, "http://"+host+":8000", "edge-01")
			t.Setenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL", "true")

			got, err := Load()
			if err != nil {
				t.Fatal(err)
			}
			if !got.AllowInsecureLocal {
				t.Fatal("expected insecure local mode")
			}
		})
	}
}

func TestLoadRejectsInsecureRemotePlatformURLInDevelopmentMode(t *testing.T) {
	setRequiredEnvironment(t, "http://platform.example.com", "edge-01")
	t.Setenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL", "true")

	_, err := Load()
	if err == nil {
		t.Fatal("expected non-local HTTP URL to be rejected")
	}
}

func TestLoadRejectsMalformedPlatformURLAndBoolean(t *testing.T) {
	t.Run("URL", func(t *testing.T) {
		setRequiredEnvironment(t, "://bad", "edge-01")
		_, err := Load()
		if err == nil {
			t.Fatal("expected malformed URL to be rejected")
		}
	})

	t.Run("boolean", func(t *testing.T) {
		setRequiredEnvironment(t, "https://platform.example.com", "edge-01")
		t.Setenv("VISIOX_AGENT_ALLOW_INSECURE_LOCAL", "sometimes")
		_, err := Load()
		if err == nil {
			t.Fatal("expected malformed boolean to be rejected")
		}
	})
}

func setRequiredEnvironment(t *testing.T, platformURL, nodeName string) {
	t.Helper()
	t.Setenv("VISIOX_AGENT_PLATFORM_URL", platformURL)
	t.Setenv("VISIOX_AGENT_NODE_NAME", nodeName)
	t.Setenv("VISIOX_AGENT_ENROLLMENT_TOKEN", "token")
}
