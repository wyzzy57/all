package identity

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"github.com/wyzzy57/all/apps/node-agent/internal/state"
	"github.com/wyzzy57/all/apps/node-agent/internal/testsupport"
)

func TestEnsureGeneratesCSRAndPersistsReturnedIdentity(t *testing.T) {
	ca := testsupport.NewCA(t)
	calls := 0
	var request protocol.EnrollmentRequest
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/agent/v1/enroll" {
			http.NotFound(w, r)
			return
		}
		calls++
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request: %v", err)
			return
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(body, &fields); err != nil {
			t.Errorf("decode request fields: %v", err)
			return
		}
		wantFields := []string{
			"protocol_version", "token", "node_name", "architecture",
			"platform_kind", "agent_version", "csr_pem",
		}
		if len(fields) != len(wantFields) {
			t.Errorf("request field count = %d, want %d", len(fields), len(wantFields))
			return
		}
		for _, field := range wantFields {
			if _, ok := fields[field]; !ok {
				t.Errorf("request is missing %q", field)
				return
			}
		}
		if err := json.Unmarshal(body, &request); err != nil {
			t.Errorf("decode request: %v", err)
			return
		}
		if request.ProtocolVersion != protocol.ProtocolVersion ||
			request.Token != strings.Repeat("x", 32) ||
			request.NodeName != "edge-01" ||
			request.Architecture != "arm64" ||
			request.PlatformKind != "jetson" ||
			request.AgentVersion != "0.1.0-test" {
			t.Errorf("unexpected enrollment request: %#v", request)
			return
		}
		csr := parseCSR(t, request.CSRPEM)
		if csr.Subject.CommonName != "edge-01" {
			t.Errorf("CSR common name = %q, want edge-01", csr.Subject.CommonName)
			return
		}
		if _, ok := csr.PublicKey.(ed25519.PublicKey); !ok {
			t.Errorf("CSR public key type = %T, want Ed25519", csr.PublicKey)
			return
		}

		w.WriteHeader(http.StatusCreated)
		if err := json.NewEncoder(w).Encode(protocol.EnrollmentResponse{
			ProtocolVersion:          protocol.ProtocolVersion,
			NodeID:                   "node-1",
			CertificatePEM:           ca.SignCSR(t, request.CSRPEM, "node-1", time.Now().Add(365*24*time.Hour)),
			CACertificatePEM:         ca.PEM(),
			GatewayURL:               strings.Replace(server.URL, "http://", "ws://", 1) + "/agent/v1/connect",
			HeartbeatIntervalSeconds: 15,
		}); err != nil {
			t.Errorf("encode response: %v", err)
		}
	}))
	defer server.Close()

	store := openStore(t)
	cfg := testConfig(server.URL, true)
	facts := protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"}

	got, err := Ensure(context.Background(), cfg, facts, store, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if got.NodeID != "node-1" || !strings.Contains(got.PrivateKeyPEM, "PRIVATE KEY") {
		t.Fatalf("identity was not persisted: %#v", got)
	}
	if got.CertificateExpiresAt.Before(time.Now().Add(300 * 24 * time.Hour)) {
		t.Fatal("certificate expiry was not parsed")
	}
	persisted, found, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if !found || persisted.NodeID != got.NodeID || persisted.PrivateKeyPEM != got.PrivateKeyPEM {
		t.Fatalf("stored identity does not match result: %#v", persisted)
	}

	again, err := Ensure(context.Background(), cfg, protocol.EnrollmentFacts{}, store, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if again != got || calls != 1 {
		t.Fatalf("existing identity must be reused; calls=%d", calls)
	}
}

func TestEnsureRejectsCertificateForDifferentKey(t *testing.T) {
	ca := testsupport.NewCA(t)
	server := enrollmentServer(t, func(request protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
		_, otherKey, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		otherCSRDER, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
			Subject: pkix.Name{CommonName: "edge-01"},
		}, otherKey)
		if err != nil {
			t.Fatal(err)
		}
		otherCSR := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: otherCSRDER}))
		response.CertificatePEM = ca.SignCSR(t, otherCSR, response.NodeID, time.Now().Add(24*time.Hour))
	}, ca)
	defer server.Close()

	store := openStore(t)
	_, err := Ensure(
		context.Background(),
		testConfig(server.URL, true),
		protocol.EnrollmentFacts{Architecture: "amd64", PlatformKind: "x86_nvidia"},
		store,
		server.Client(),
	)
	if err == nil {
		t.Fatal("expected a certificate key mismatch error")
	}
	assertStoreEmpty(t, store)
}

func TestEnsureRejectsInvalidFactsWithoutRequest(t *testing.T) {
	tests := []protocol.EnrollmentFacts{
		{},
		{Architecture: "arm64", PlatformKind: "x86_nvidia"},
		{Architecture: "amd64", PlatformKind: "jetson"},
		{Architecture: "386", PlatformKind: "x86_nvidia"},
	}
	for _, facts := range tests {
		t.Run(facts.Architecture+"_"+facts.PlatformKind, func(t *testing.T) {
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				calls++
			}))
			defer server.Close()
			store := openStore(t)

			_, err := Ensure(context.Background(), testConfig(server.URL, true), facts, store, server.Client())
			if err == nil {
				t.Fatal("expected invalid facts to be rejected")
			}
			if calls != 0 {
				t.Fatalf("invalid facts made %d enrollment requests", calls)
			}
			assertStoreEmpty(t, store)
		})
	}
}

func TestEnsureRejectsInvalidEnrollmentResponsesWithoutPersistence(t *testing.T) {
	tests := []struct {
		name   string
		status int
		mutate func(*testsupport.CA, protocol.EnrollmentRequest, *protocol.EnrollmentResponse)
	}{
		{
			name: "protocol version",
			mutate: func(_ *testsupport.CA, _ protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
				response.ProtocolVersion = 2
			},
		},
		{name: "HTTP status", status: http.StatusOK},
		{
			name: "CA chain",
			mutate: func(_ *testsupport.CA, _ protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
				response.CACertificatePEM = testsupport.NewCA(t).PEM()
			},
		},
		{
			name: "certificate common name",
			mutate: func(ca *testsupport.CA, request protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
				response.CertificatePEM = ca.SignCSR(t, request.CSRPEM, "other-node", time.Now().Add(24*time.Hour))
			},
		},
		{
			name: "gateway scheme",
			mutate: func(_ *testsupport.CA, _ protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
				response.GatewayURL = "http://localhost/agent/v1/connect"
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ca := testsupport.NewCA(t)
			status := test.status
			if status == 0 {
				status = http.StatusCreated
			}
			var mutate func(protocol.EnrollmentRequest, *protocol.EnrollmentResponse)
			if test.mutate != nil {
				mutate = func(request protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
					test.mutate(ca, request, response)
				}
			}
			server := enrollmentServerWithStatus(t, status, mutate, ca)
			defer server.Close()
			store := openStore(t)

			_, err := Ensure(
				context.Background(),
				testConfig(server.URL, true),
				protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"},
				store,
				server.Client(),
			)
			if err == nil {
				t.Fatalf("expected %s to be rejected", test.name)
			}
			assertStoreEmpty(t, store)
		})
	}
}

func TestEnsureRejectsOversizedResponseWithoutPersistence(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, strings.Repeat("x", 1024*1024+1))
	}))
	defer server.Close()
	store := openStore(t)

	_, err := Ensure(
		context.Background(),
		testConfig(server.URL, true),
		protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"},
		store,
		server.Client(),
	)
	if err == nil {
		t.Fatal("expected an oversized response error")
	}
	assertStoreEmpty(t, store)
}

func TestEnsureRequiresExplicitClientAuthUsage(t *testing.T) {
	ca := newSigningCA(t)
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request protocol.EnrollmentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode request: %v", err)
			return
		}
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(protocol.EnrollmentResponse{
			ProtocolVersion:          protocol.ProtocolVersion,
			NodeID:                   "node-1",
			CertificatePEM:           ca.signCSR(t, request.CSRPEM, "node-1", nil),
			CACertificatePEM:         ca.pem,
			GatewayURL:               strings.Replace(server.URL, "http://", "ws://", 1),
			HeartbeatIntervalSeconds: 15,
		})
	}))
	defer server.Close()
	store := openStore(t)

	_, err := Ensure(
		context.Background(),
		testConfig(server.URL, true),
		protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"},
		store,
		server.Client(),
	)
	if err == nil {
		t.Fatal("expected a missing client-auth EKU error")
	}
	assertStoreEmpty(t, store)
}

func TestEnsureRejectsInsecureGatewayWhenNotAllowed(t *testing.T) {
	ca := testsupport.NewCA(t)
	server := enrollmentServer(t, nil, ca)
	defer server.Close()
	store := openStore(t)

	_, err := Ensure(
		context.Background(),
		testConfig(server.URL, false),
		protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"},
		store,
		server.Client(),
	)
	if err == nil {
		t.Fatal("expected ws gateway URL to be rejected")
	}
	assertStoreEmpty(t, store)
}

func enrollmentServer(
	t *testing.T,
	mutate func(protocol.EnrollmentRequest, *protocol.EnrollmentResponse),
	ca *testsupport.CA,
) *httptest.Server {
	t.Helper()
	return enrollmentServerWithStatus(t, http.StatusCreated, mutate, ca)
}

func enrollmentServerWithStatus(
	t *testing.T,
	status int,
	mutate func(protocol.EnrollmentRequest, *protocol.EnrollmentResponse),
	ca *testsupport.CA,
) *httptest.Server {
	t.Helper()
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request protocol.EnrollmentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Errorf("decode request: %v", err)
			return
		}
		response := protocol.EnrollmentResponse{
			ProtocolVersion:          protocol.ProtocolVersion,
			NodeID:                   "node-1",
			CertificatePEM:           ca.SignCSR(t, request.CSRPEM, "node-1", time.Now().Add(24*time.Hour)),
			CACertificatePEM:         ca.PEM(),
			GatewayURL:               strings.Replace(server.URL, "http://", "ws://", 1) + "/agent/v1/connect",
			HeartbeatIntervalSeconds: 15,
		}
		if mutate != nil {
			mutate(request, &response)
		}
		w.WriteHeader(status)
		if err := json.NewEncoder(w).Encode(response); err != nil {
			t.Errorf("encode response: %v", err)
		}
	}))
	return server
}

func testConfig(platformURL string, allowInsecure bool) config.Config {
	return config.Config{
		PlatformURL:        platformURL,
		NodeName:           "edge-01",
		EnrollmentToken:    strings.Repeat("x", 32),
		AgentVersion:       "0.1.0-test",
		AllowInsecureLocal: allowInsecure,
	}
}

func openStore(t *testing.T) *state.Store {
	t.Helper()
	store, err := state.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})
	return store
}

func assertStoreEmpty(t *testing.T, store *state.Store) {
	t.Helper()
	_, found, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if found {
		t.Fatal("identity store must remain empty")
	}
}

func parseCSR(t *testing.T, csrPEM string) *x509.CertificateRequest {
	t.Helper()
	block, rest := pem.Decode([]byte(csrPEM))
	if block == nil || block.Type != "CERTIFICATE REQUEST" || len(strings.TrimSpace(string(rest))) != 0 {
		t.Fatal("CSR is not a single CERTIFICATE REQUEST PEM block")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if err := csr.CheckSignature(); err != nil {
		t.Fatalf("verify CSR signature: %v", err)
	}
	return csr
}

type signingCA struct {
	certificate *x509.Certificate
	privateKey  ed25519.PrivateKey
	pem         string
}

func newSigningCA(t *testing.T) *signingCA {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(100),
		Subject:               pkix.Name{CommonName: "Explicit EKU Test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(48 * time.Hour),
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
	return &signingCA{
		certificate: certificate,
		privateKey:  privateKey,
		pem:         string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})),
	}
}

func (c *signingCA) signCSR(
	t *testing.T,
	csrPEM string,
	nodeID string,
	extKeyUsage []x509.ExtKeyUsage,
) string {
	t.Helper()
	csr := parseCSR(t, csrPEM)
	template := &x509.Certificate{
		SerialNumber: big.NewInt(101),
		Subject:      pkix.Name{CommonName: nodeID},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  extKeyUsage,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, c.certificate, csr.PublicKey, c.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}
