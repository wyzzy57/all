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
	"os"
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
			"platform_kind", "agent_version", "csr_pem", "enrollment_request_id",
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
			request.EnrollmentRequestID == "" ||
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
			EnrollmentRequestID:      request.EnrollmentRequestID,
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

func TestEnsurePersistsStableEnrollmentAttemptBeforeNetworkAndRecoversResponseLoss(t *testing.T) {
	ca := testsupport.NewCA(t)
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if store != nil {
			if closeErr := store.Close(); closeErr != nil {
				t.Errorf("close store: %v", closeErr)
			}
		}
	}()

	type capturedRequest struct {
		requestID string
		request   protocol.EnrollmentRequest
	}
	var requests []capturedRequest
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, readErr := io.ReadAll(r.Body)
		if readErr != nil {
			t.Errorf("read enrollment request: %v", readErr)
			return
		}
		if strings.Contains(string(body), "PRIVATE KEY") {
			t.Error("enrollment request transported a private key")
			return
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(body, &fields); err != nil {
			t.Errorf("decode enrollment request fields: %v", err)
			return
		}
		var requestID string
		if err := json.Unmarshal(fields["enrollment_request_id"], &requestID); err != nil || requestID == "" {
			t.Errorf("enrollment request is missing a stable request ID: %v", err)
			return
		}
		var request protocol.EnrollmentRequest
		if err := json.Unmarshal(body, &request); err != nil {
			t.Errorf("decode enrollment request: %v", err)
			return
		}
		databaseBytes, err := os.ReadFile(path)
		if err != nil {
			t.Errorf("read durable Agent state before response: %v", err)
			return
		}
		if !strings.Contains(string(databaseBytes), requestID) || !strings.Contains(string(databaseBytes), "PRIVATE KEY") {
			t.Error("Agent did not durably persist the enrollment request and private key before the HTTP request")
			return
		}
		requests = append(requests, capturedRequest{requestID: requestID, request: request})
		if len(requests) == 1 {
			// Simulate a server commit followed by response loss.
			w.WriteHeader(http.StatusCreated)
			return
		}
		response := map[string]any{
			"protocol_version":           protocol.ProtocolVersion,
			"enrollment_request_id":      requestID,
			"node_id":                    "node-1",
			"certificate_pem":            ca.SignCSR(t, request.CSRPEM, "node-1", time.Now().Add(365*24*time.Hour)),
			"ca_certificate_pem":         ca.PEM(),
			"gateway_url":                strings.Replace(server.URL, "http://", "ws://", 1) + "/agent/v1/connect",
			"heartbeat_interval_seconds": 15,
		}
		encoded, err := json.Marshal(response)
		if err != nil {
			t.Errorf("encode enrollment response: %v", err)
			return
		}
		if strings.Contains(string(encoded), "PRIVATE KEY") {
			t.Error("enrollment response transported a private key")
			return
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write(encoded)
	}))
	defer server.Close()

	cfg := testConfig(server.URL, true)
	facts := protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"}
	if _, err := Ensure(context.Background(), cfg, facts, store, server.Client()); err == nil {
		t.Fatal("expected response-loss enrollment attempt to fail locally")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store = nil

	// A restart must use the durable bootstrap request, even when the environment token is gone.
	store, err = state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	cfg.EnrollmentToken = ""
	identity, err := Ensure(context.Background(), cfg, facts, store, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if identity.NodeID != "node-1" || len(requests) != 2 {
		t.Fatalf("recovered enrollment identity = %#v requests=%d", identity, len(requests))
	}
	if requests[0].requestID != requests[1].requestID ||
		requests[0].request.Token != requests[1].request.Token ||
		requests[0].request.CSRPEM != requests[1].request.CSRPEM ||
		requests[0].request.NodeName != requests[1].request.NodeName ||
		requests[0].request.PlatformKind != requests[1].request.PlatformKind {
		t.Fatalf("restart changed the enrollment retry binding: %#v", requests)
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
			EnrollmentRequestID:      request.EnrollmentRequestID,
			NodeID:                   "node-1",
			CertificatePEM:           ca.signCSR(t, request.CSRPEM, "node-1", x509.KeyUsageDigitalSignature, nil, false),
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

func TestEnsureRejectsCACertificateAsDeviceIdentity(t *testing.T) {
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
			EnrollmentRequestID:      request.EnrollmentRequestID,
			NodeID:                   "node-1",
			CertificatePEM:           ca.signCSR(t, request.CSRPEM, "node-1", x509.KeyUsageCertSign, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, true),
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
		t.Fatal("expected a CA certificate to be rejected as a device identity")
	}
	assertStoreEmpty(t, store)
}

func TestEnsureRejectsLeafSignedByExtraUnrelatedCA(t *testing.T) {
	trustedCA := testsupport.NewCA(t)
	attackerCA := testsupport.NewCA(t)
	server := enrollmentServer(t, func(request protocol.EnrollmentRequest, response *protocol.EnrollmentResponse) {
		response.CertificatePEM = attackerCA.SignCSR(t, request.CSRPEM, response.NodeID, time.Now().Add(24*time.Hour))
		response.CACertificatePEM = trustedCA.PEM() + attackerCA.PEM()
	}, trustedCA)
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
		t.Fatal("expected a leaf signed by an extra unrelated CA to be rejected")
	}
	assertStoreEmpty(t, store)
}

func TestParseCACertificatesRejectsInvalidRootBundles(t *testing.T) {
	now := time.Now()
	validCA := testsupport.NewCA(t)
	otherCA := testsupport.NewCA(t)
	issuer := newSigningCA(t)
	tests := []struct {
		name string
		pem  string
	}{
		{name: "duplicate certificate", pem: validCA.PEM() + validCA.PEM()},
		{name: "extra unrelated certificate", pem: validCA.PEM() + otherCA.PEM()},
		{
			name: "non CA certificate",
			pem: selfSignedCertificatePEM(t, x509.Certificate{
				SerialNumber:          big.NewInt(200),
				Subject:               pkix.Name{CommonName: "Not a CA"},
				NotBefore:             now.Add(-time.Hour),
				NotAfter:              now.Add(time.Hour),
				KeyUsage:              x509.KeyUsageDigitalSignature,
				BasicConstraintsValid: true,
			}),
		},
		{
			name: "missing key cert sign usage",
			pem: selfSignedCertificatePEM(t, x509.Certificate{
				SerialNumber:          big.NewInt(201),
				Subject:               pkix.Name{CommonName: "Wrong Key Usage CA"},
				NotBefore:             now.Add(-time.Hour),
				NotAfter:              now.Add(time.Hour),
				KeyUsage:              x509.KeyUsageCRLSign,
				BasicConstraintsValid: true,
				IsCA:                  true,
			}),
		},
		{
			name: "expired CA",
			pem: selfSignedCertificatePEM(t, x509.Certificate{
				SerialNumber:          big.NewInt(202),
				Subject:               pkix.Name{CommonName: "Expired CA"},
				NotBefore:             now.Add(-2 * time.Hour),
				NotAfter:              now.Add(-time.Hour),
				KeyUsage:              x509.KeyUsageCertSign,
				BasicConstraintsValid: true,
				IsCA:                  true,
			}),
		},
		{
			name: "not yet valid CA",
			pem: selfSignedCertificatePEM(t, x509.Certificate{
				SerialNumber:          big.NewInt(203),
				Subject:               pkix.Name{CommonName: "Future CA"},
				NotBefore:             now.Add(time.Hour),
				NotAfter:              now.Add(2 * time.Hour),
				KeyUsage:              x509.KeyUsageCertSign,
				BasicConstraintsValid: true,
				IsCA:                  true,
			}),
		},
		{
			name: "not self signed",
			pem: signedCertificatePEM(t, x509.Certificate{
				SerialNumber:          big.NewInt(204),
				Subject:               pkix.Name{CommonName: "Intermediate CA"},
				NotBefore:             now.Add(-time.Hour),
				NotAfter:              now.Add(time.Hour),
				KeyUsage:              x509.KeyUsageCertSign,
				BasicConstraintsValid: true,
				IsCA:                  true,
			}, issuer.certificate, issuer.privateKey),
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := parseCACertificates(test.pem); err == nil {
				t.Fatal("expected invalid CA root bundle to be rejected")
			}
		})
	}
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

func TestValidateGatewayURLLocality(t *testing.T) {
	tests := []struct {
		name    string
		rawURL  string
		allowed bool
	}{
		{name: "secure remote host", rawURL: "wss://gateway.example:443/agent/v1/connect", allowed: true},
		{name: "secure mixed case host", rawURL: "WSS://Gateway.Example.:443/agent/v1/connect", allowed: true},
		{name: "localhost", rawURL: "ws://localhost:8080/agent/v1/connect", allowed: true},
		{name: "normalized localhost", rawURL: "ws://LOCALHOST.:8080/agent/v1/connect", allowed: true},
		{name: "loopback IPv4", rawURL: "ws://127.0.0.1:8080/agent/v1/connect", allowed: true},
		{name: "compose service", rawURL: "ws://api-service:8000/agent/v1/connect", allowed: true},
		{name: "docker host", rawURL: "ws://HOST.DOCKER.INTERNAL.:8000/agent/v1/connect", allowed: true},
		{name: "remote host", rawURL: "ws://gateway.example:443/agent/v1/connect"},
		{name: "remote private address", rawURL: "ws://192.168.1.10:443/agent/v1/connect"},
		{name: "empty insecure hostname", rawURL: "ws://:443/agent/v1/connect"},
		{name: "empty secure hostname", rawURL: "wss://:443/agent/v1/connect"},
		{name: "insecure userinfo", rawURL: "ws://user@localhost:443/agent/v1/connect"},
		{name: "secure userinfo", rawURL: "wss://user@gateway.example:443/agent/v1/connect"},
		{name: "malformed authority", rawURL: "ws://localhost:bad/agent/v1/connect"},
		{name: "dangling insecure port delimiter", rawURL: "ws://localhost:/agent/v1/connect"},
		{name: "dangling secure port delimiter", rawURL: "wss://gateway.example:/agent/v1/connect"},
		{name: "unterminated IPv6 authority", rawURL: "wss://[::1/agent/v1/connect"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := validateGatewayURL(test.rawURL, true)
			if test.allowed && err != nil {
				t.Fatalf("expected gateway URL to be allowed: %v", err)
			}
			if !test.allowed && err == nil {
				t.Fatal("expected gateway URL to be rejected")
			}
		})
	}
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
			EnrollmentRequestID:      request.EnrollmentRequestID,
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

func selfSignedCertificatePEM(t *testing.T, template x509.Certificate) string {
	t.Helper()
	return signedCertificatePEM(t, template, &template, nil)
}

func signedCertificatePEM(
	t *testing.T,
	template x509.Certificate,
	parent *x509.Certificate,
	parentKey ed25519.PrivateKey,
) string {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	if parentKey == nil {
		parentKey = privateKey
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, parent, publicKey, parentKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func (c *signingCA) signCSR(
	t *testing.T,
	csrPEM string,
	nodeID string,
	keyUsage x509.KeyUsage,
	extKeyUsage []x509.ExtKeyUsage,
	isCA bool,
) string {
	t.Helper()
	csr := parseCSR(t, csrPEM)
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(101),
		Subject:               pkix.Name{CommonName: nodeID},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              keyUsage,
		ExtKeyUsage:           extKeyUsage,
		BasicConstraintsValid: isCA,
		IsCA:                  isCA,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, c.certificate, csr.PublicKey, c.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}
