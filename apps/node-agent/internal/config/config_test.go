package config

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
	for _, host := range []string{"localhost", "127.0.0.1", "api-service", "host.docker.internal"} {
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

func TestServerTLSConfigUsesOperatingSystemRootsByDefault(t *testing.T) {
	tlsConfig, err := (Config{}).ServerTLSConfig()
	if err != nil {
		t.Fatal(err)
	}
	if tlsConfig.RootCAs != nil {
		t.Fatal("default server TLS config must defer to operating-system roots")
	}
}

func TestServerTLSConfigLoadsOnlyExplicitPrivateServerCA(t *testing.T) {
	serverCA, subject := testServerCAPEM(t)
	serverCAPath := filepath.Join(t.TempDir(), "server-ca.crt")
	if err := os.WriteFile(serverCAPath, []byte(serverCA), 0o600); err != nil {
		t.Fatal(err)
	}

	tlsConfig, err := (Config{ServerCAFile: serverCAPath}).ServerTLSConfig()
	if err != nil {
		t.Fatal(err)
	}
	if tlsConfig.RootCAs == nil {
		t.Fatal("explicit private server CA must be appended to TLS roots")
	}
	for _, candidate := range tlsConfig.RootCAs.Subjects() {
		if bytes.Equal(candidate, subject) {
			return
		}
	}
	t.Fatal("explicit private server CA was not added to TLS roots")
}

func TestServerTLSConfigFailsClosedForMissingAndInvalidFiles(t *testing.T) {
	for name, contents := range map[string]string{
		"missing": "",
		"invalid": "not a certificate",
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "server-ca.crt")
			if contents != "" {
				if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := (Config{ServerCAFile: path}).ServerTLSConfig(); err == nil {
				t.Fatal("configured server CA file must fail closed when unreadable or invalid")
			}
		})
	}
}

func setRequiredEnvironment(t *testing.T, platformURL, nodeName string) {
	t.Helper()
	t.Setenv("VISIOX_AGENT_PLATFORM_URL", platformURL)
	t.Setenv("VISIOX_AGENT_NODE_NAME", nodeName)
	t.Setenv("VISIOX_AGENT_ENROLLMENT_TOKEN", "token")
}

func testServerCAPEM(t *testing.T) (string, []byte) {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(99),
		Subject:               pkix.Name{CommonName: "Visiox Test Server Root"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), certificate.RawSubject
}
