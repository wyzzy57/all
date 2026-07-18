package gateway

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"github.com/wyzzy57/all/apps/node-agent/internal/state"
	"github.com/wyzzy57/all/apps/node-agent/internal/testsupport"
	"go.etcd.io/bbolt"
)

const peerTimeout = 5 * time.Second

func TestClientAuthenticatesAndAcknowledgesEvents(t *testing.T) {
	store := openGatewayStore(t)
	serverDone := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}

		seen := map[string]int{}
		for len(seen) < 3 {
			_, raw, err := peerRead(conn)
			if err != nil {
				reportServerError(serverErrors, err)
				return
			}
			envelope, err := decodePeerEnvelope(raw)
			if err != nil {
				reportServerError(serverErrors, err)
				return
			}
			seen[envelope.Type]++
			switch envelope.Type {
			case "inventory":
				var inventory protocol.InventoryMessage
				if err := json.Unmarshal(raw, &inventory); err != nil || inventory.Architecture != "amd64" {
					reportServerError(serverErrors, fmt.Errorf("invalid inventory: %s", raw))
					return
				}
			case "heartbeat":
				var heartbeat protocol.HeartbeatMessage
				if err := json.Unmarshal(raw, &heartbeat); err != nil || heartbeat.OccurredAt.Location() != time.UTC {
					reportServerError(serverErrors, fmt.Errorf("invalid heartbeat: %s", raw))
					return
				}
			case "event_batch":
				var batch protocol.EventBatchMessage
				if err := json.Unmarshal(raw, &batch); err != nil || len(batch.Events) != 1 || batch.Events[0].Sequence != 1 {
					reportServerError(serverErrors, fmt.Errorf("invalid event batch: %s", raw))
					return
				}
			default:
				reportServerError(serverErrors, fmt.Errorf("unexpected message type %q", envelope.Type))
				return
			}
		}
		for messageType, count := range seen {
			if count != 1 {
				reportServerError(serverErrors, fmt.Errorf("received %d %s messages", count, messageType))
				return
			}
		}
		if err := peerWriteJSON(conn, protocol.EventsAckMessage{
			Envelope:        testEnvelope("events_acked"),
			ThroughSequence: 1,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		serverDone <- struct{}{}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()

	gatewayURL := websocketURL(server.URL)
	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		gatewayURL,
		time.Now().Add(365*24*time.Hour),
	)
	if _, err := store.AppendEvent("agent_started", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	select {
	case <-serverDone:
	case err := <-serverErrors:
		cancel()
		t.Fatal(err)
	case <-ctx.Done():
		cancel()
		t.Fatal("timed out waiting for Gateway acceptance")
	}
	waitPendingEvents(t, store, 0)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
}

func TestClientReconnectsAfterGatewayAuthenticationDeadline(t *testing.T) {
	store := openGatewayStore(t)
	serverErrors := make(chan error, 1)
	firstConnectionClosed := make(chan struct{}, 1)
	secondConnectionReady := make(chan struct{}, 1)
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
		connections    atomic.Int32
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()

		switch connections.Add(1) {
		case 1:
			_, _, _ = peerRead(conn)
			firstConnectionClosed <- struct{}{}
		case 2:
			if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectLiveInventory(conn); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			secondConnectionReady <- struct{}{}
			<-r.Context().Done()
		default:
			reportServerError(serverErrors, fmt.Errorf("unexpected connection %d", connections.Load()))
		}
	}))
	defer server.Close()

	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-auth-deadline",
		websocketURL(server.URL),
		time.Now().Add(365*24*time.Hour),
	)
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(
		testGatewayConfig(),
		store,
		testInventory(),
		WithBackoff(zeroBackoff),
		WithConnectionDeadlines(100*time.Millisecond, time.Second, time.Second),
	))

	waitSignalOrError(t, ctx, firstConnectionClosed, serverErrors, "authentication deadline close")
	waitSignalOrError(t, ctx, secondConnectionReady, serverErrors, "authenticated reconnect")
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
	if got := connections.Load(); got != 2 {
		t.Fatalf("Gateway connections = %d, want 2", got)
	}
}

func TestClientReconnectsWhenGatewayStopsRespondingToPings(t *testing.T) {
	store := openGatewayStore(t)
	serverErrors := make(chan error, 1)
	firstConnectionReady := make(chan struct{}, 1)
	secondConnectionReady := make(chan struct{}, 1)
	releaseFirstConnection := make(chan struct{})
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
		connections    atomic.Int32
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := expectLiveInventory(conn); err != nil {
			reportServerError(serverErrors, err)
			return
		}

		switch connections.Add(1) {
		case 1:
			firstConnectionReady <- struct{}{}
			<-releaseFirstConnection
		case 2:
			secondConnectionReady <- struct{}{}
			<-r.Context().Done()
		default:
			reportServerError(serverErrors, fmt.Errorf("unexpected connection %d", connections.Load()))
		}
	}))
	defer server.Close()

	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-pong-deadline",
		websocketURL(server.URL),
		time.Now().Add(365*24*time.Hour),
	)
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(
		testGatewayConfig(),
		store,
		testInventory(),
		WithBackoff(zeroBackoff),
		WithConnectionDeadlines(time.Second, 100*time.Millisecond, 100*time.Millisecond),
	))

	waitSignalOrError(t, ctx, firstConnectionReady, serverErrors, "first live connection")
	waitSignalOrError(t, ctx, secondConnectionReady, serverErrors, "pong-timeout reconnect")
	close(releaseFirstConnection)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
	if got := connections.Load(); got != 2 {
		t.Fatalf("Gateway connections = %d, want 2", got)
	}
}

func TestClientRenewsCertificateBeforeExpiry(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var request protocol.CertificateRenewalRequest
		if err := peerReadJSON(conn, &request); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if request.Envelope != testEnvelope("certificate_renewal_request") {
			reportServerError(serverErrors, fmt.Errorf("unexpected renewal request: %#v", request))
			return
		}
		if strings.Contains(request.CSRPEM, "PRIVATE KEY") {
			reportServerError(serverErrors, fmt.Errorf("renewal request transported private key material"))
			return
		}
		csr, err := parsePeerCSR(request.CSRPEM)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if publicKey, ok := csr.PublicKey.(ed25519.PublicKey); !ok || !bytes.Equal(publicKey, signer.Public().(ed25519.PublicKey)) {
			reportServerError(serverErrors, fmt.Errorf("renewal CSR did not reuse the stored key"))
			return
		}
		renewedPEM := ca.SignCSR(t, request.CSRPEM, storedIdentity.NodeID, time.Now().Add(365*24*time.Hour))
		renewedCertificate, err := parsePeerCertificate(renewedPEM)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		fingerprint := testCertificateFingerprint(renewedCertificate)
		if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
			Envelope:                     testEnvelope("certificate_renewal_candidate"),
			RenewalRequestID:             request.RenewalRequestID,
			CertificatePEM:               renewedPEM,
			CertificateFingerprintSHA256: fingerprint,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var acknowledgement protocol.CertificateRenewalAckMessage
		if err := peerReadJSON(conn, &acknowledgement); err != nil ||
			acknowledgement.RenewalRequestID != request.RenewalRequestID ||
			acknowledgement.CertificateFingerprintSHA256 != fingerprint {
			reportServerError(serverErrors, fmt.Errorf("invalid renewal acknowledgement: %v", err))
			return
		}
		if err := peerWriteJSON(conn, protocol.CertificateRenewalActivatedMessage{
			Envelope:                     testEnvelope("certificate_renewal_activated"),
			RenewalRequestID:             request.RenewalRequestID,
			CertificateFingerprintSHA256: fingerprint,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var inventory protocol.InventoryMessage
		if err := peerReadJSON(conn, &inventory); err != nil || inventory.Type != "inventory" {
			reportServerError(serverErrors, fmt.Errorf("client did not continue after renewal: %v", err))
			return
		}
		serverReady <- struct{}{}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()

	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	originalExpiry := storedIdentity.CertificateExpiresAt
	originalKey := storedIdentity.PrivateKeyPEM

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "renewal")

	renewed, found, err := store.Identity()
	if err != nil || !found {
		cancel()
		t.Fatalf("load renewed identity: found=%v err=%v", found, err)
	}
	if renewed.PrivateKeyPEM != originalKey {
		cancel()
		t.Fatal("certificate renewal changed the private key")
	}
	if !renewed.CertificateExpiresAt.After(originalExpiry) {
		cancel()
		t.Fatalf("renewed expiry %s did not move past %s", renewed.CertificateExpiresAt, originalExpiry)
	}
	certificate, err := parsePeerCertificate(renewed.CertificatePEM)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if publicKey, ok := certificate.PublicKey.(ed25519.PublicKey); !ok || !bytes.Equal(publicKey, signer.Public().(ed25519.PublicKey)) {
		cancel()
		t.Fatal("renewed certificate public key does not match the stored private key")
	}
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
}

func TestClientPersistsRenewalCandidateBeforeAcknowledgement(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if closeErr := store.Close(); closeErr != nil {
			t.Errorf("close store: %v", closeErr)
		}
	}()
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		_, rawRenewal, err := peerRead(conn)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var renewalFields map[string]json.RawMessage
		if err := json.Unmarshal(rawRenewal, &renewalFields); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var requestID, csrPEM string
		if err := json.Unmarshal(renewalFields["renewal_request_id"], &requestID); err != nil || requestID == "" {
			reportServerError(serverErrors, fmt.Errorf("renewal request is missing a stable request ID: %v", err))
			return
		}
		if err := json.Unmarshal(renewalFields["csr_pem"], &csrPEM); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		candidatePEM := ca.SignCSR(t, csrPEM, storedIdentity.NodeID, time.Now().Add(365*24*time.Hour))
		certificate, err := parsePeerCertificate(candidatePEM)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		candidate := map[string]any{
			"protocol_version":               protocol.ProtocolVersion,
			"type":                           "certificate_renewal_candidate",
			"renewal_request_id":             requestID,
			"certificate_pem":                candidatePEM,
			"certificate_fingerprint_sha256": testCertificateFingerprint(certificate),
		}
		if err := peerWriteJSON(conn, candidate); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		_, rawAck, err := peerRead(conn)
		if err != nil {
			reportServerError(serverErrors, fmt.Errorf("read renewal acknowledgement: %w", err))
			return
		}
		var acknowledgement map[string]json.RawMessage
		if err := json.Unmarshal(rawAck, &acknowledgement); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var acknowledgementType, acknowledgedRequestID string
		_ = json.Unmarshal(acknowledgement["type"], &acknowledgementType)
		_ = json.Unmarshal(acknowledgement["renewal_request_id"], &acknowledgedRequestID)
		if acknowledgementType != "certificate_renewal_ack" || acknowledgedRequestID != requestID {
			reportServerError(serverErrors, fmt.Errorf("unexpected renewal acknowledgement: %s", rawAck))
			return
		}
		pending, found, err := store.PendingRenewal()
		if err != nil || !found || pending.CertificatePEM != candidatePEM ||
			pending.CertificateFingerprintSHA256 != testCertificateFingerprint(certificate) {
			reportServerError(serverErrors, fmt.Errorf("candidate was not committed before acknowledgement: found=%v err=%v", found, err))
			return
		}
		serverReady <- struct{}{}
	}))
	defer server.Close()

	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	originalCertificate := storedIdentity.CertificatePEM
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "renewal acknowledgement")
	current, found, err := store.Identity()
	if err != nil || !found || current.CertificatePEM != originalCertificate {
		cancel()
		t.Fatalf("candidate acknowledgement changed active identity before activation: found=%v err=%v", found, err)
	}
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
}

func TestClientRetriesStableRenewalRequestAfterCandidateDeliveryLoss(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		identity     state.Identity
		signer       ed25519.PrivateKey
		ca           *testsupport.CA
		pending      state.PendingRenewal
		candidatePEM string
		fingerprint  string
		connections  atomic.Int32
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var request protocol.CertificateRenewalRequest
		if err := peerReadJSON(conn, &request); err != nil ||
			request.RenewalRequestID != pending.RequestID || request.CSRPEM != pending.CSRPEM {
			reportServerError(serverErrors, fmt.Errorf("renewal retry did not reuse the pending request: %v", err))
			return
		}
		switch connections.Add(1) {
		case 1:
			// The server staged a candidate, but its response is lost before delivery.
			return
		case 2:
			if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
				Envelope:                     testEnvelope("certificate_renewal_candidate"),
				RenewalRequestID:             pending.RequestID,
				CertificatePEM:               candidatePEM,
				CertificateFingerprintSHA256: fingerprint,
			}); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectRenewalAcknowledgement(conn, pending, fingerprint); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := peerWriteJSON(conn, protocol.CertificateRenewalActivatedMessage{
				Envelope:                     testEnvelope("certificate_renewal_activated"),
				RenewalRequestID:             pending.RequestID,
				CertificateFingerprintSHA256: fingerprint,
			}); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectLiveInventory(conn); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			serverReady <- struct{}{}
		default:
			reportServerError(serverErrors, fmt.Errorf("unexpected renewal connection %d", connections.Load()))
		}
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	pending = state.PendingRenewal{
		RequestID: "renewal-candidate-loss-001",
		CSRPEM:    csrPEMForKey(t, signer, identity.NodeID),
	}
	if err := store.SavePendingRenewal(pending); err != nil {
		t.Fatal(err)
	}
	candidatePEM = ca.SignCSR(t, pending.CSRPEM, identity.NodeID, time.Now().Add(365*24*time.Hour))
	candidate, err := parsePeerCertificate(candidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	fingerprint = testCertificateFingerprint(candidate)

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "candidate-delivery recovery")
	assertPromotedRenewal(t, store, candidatePEM)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	if connections.Load() != 2 {
		t.Fatalf("renewal connections = %d, want 2", connections.Load())
	}
	assertNoServerError(t, serverErrors)
}

func TestClientRetriesPendingAcknowledgementAfterAcknowledgementLoss(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		identity     state.Identity
		signer       ed25519.PrivateKey
		ca           *testsupport.CA
		pending      state.PendingRenewal
		candidatePEM string
		fingerprint  string
		connections  atomic.Int32
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := expectRenewalAcknowledgement(conn, pending, fingerprint); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		switch connections.Add(1) {
		case 1:
			// The ACK was lost before the server committed the candidate.
			return
		case 2:
			if err := peerWriteJSON(conn, protocol.CertificateRenewalActivatedMessage{
				Envelope:                     testEnvelope("certificate_renewal_activated"),
				RenewalRequestID:             pending.RequestID,
				CertificateFingerprintSHA256: fingerprint,
			}); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectLiveInventory(conn); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			serverReady <- struct{}{}
		default:
			reportServerError(serverErrors, fmt.Errorf("unexpected acknowledgement connection %d", connections.Load()))
		}
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	pending, candidatePEM, fingerprint = savePendingRenewalCandidate(t, store, identity, signer, ca)

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "acknowledgement-loss recovery")
	assertPromotedRenewal(t, store, candidatePEM)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	if connections.Load() != 2 {
		t.Fatalf("acknowledgement connections = %d, want 2", connections.Load())
	}
	assertNoServerError(t, serverErrors)
}

func TestClientPromotesPendingCertificateAfterActivationConfirmationLoss(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		identity          state.Identity
		candidateIdentity state.Identity
		signer            ed25519.PrivateKey
		ca                *testsupport.CA
		pending           state.PendingRenewal
		candidatePEM      string
		fingerprint       string
		connections       atomic.Int32
		serverCommitted   atomic.Bool
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		switch connections.Add(1) {
		case 1:
			if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectRenewalAcknowledgement(conn, pending, fingerprint); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			// The server commits, but the activation response is lost to the Agent.
			serverCommitted.Store(true)
			return
		case 2:
			if err := challengeAndVerifyAuthenticate(conn, identity, signer, ca); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := conn.Close(websocket.StatusCode(4403), "agent identity rejected"); err != nil {
				reportServerError(serverErrors, err)
			}
			return
		case 3:
			if err := authenticatePeer(conn, candidateIdentity, signer, ca, 5); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			if err := expectLiveInventory(conn); err != nil {
				reportServerError(serverErrors, err)
				return
			}
			serverReady <- struct{}{}
		default:
			reportServerError(serverErrors, fmt.Errorf("unexpected activation-loss connection %d", connections.Load()))
		}
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	pending, candidatePEM, fingerprint = savePendingRenewalCandidate(t, store, identity, signer, ca)
	candidateIdentity = identity
	candidateIdentity.CertificatePEM = candidatePEM
	candidate, err := parsePeerCertificate(candidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	candidateIdentity.CertificateExpiresAt = candidate.NotAfter

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "activation-confirmation recovery")
	if !serverCommitted.Load() {
		cancel()
		t.Fatal("server did not commit the renewal before the activation response was lost")
	}
	assertPromotedRenewal(t, store, candidatePEM)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	if connections.Load() != 3 {
		t.Fatalf("activation-loss connections = %d, want 3", connections.Load())
	}
	assertNoServerError(t, serverErrors)
}

func TestClientDoesNotAcknowledgeRenewalCandidateWhenLocalPersistenceFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	serverDone := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		identity     state.Identity
		signer       ed25519.PrivateKey
		ca           *testsupport.CA
		acknowledged atomic.Bool
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() { serverDone <- struct{}{} }()
		conn, acceptErr := websocket.Accept(w, r, nil)
		if acceptErr != nil {
			reportServerError(serverErrors, acceptErr)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var request protocol.CertificateRenewalRequest
		if err := peerReadJSON(conn, &request); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		candidatePEM := ca.SignCSR(t, request.CSRPEM, identity.NodeID, time.Now().Add(365*24*time.Hour))
		candidate, err := parsePeerCertificate(candidatePEM)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := store.Close(); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
			Envelope:                     testEnvelope("certificate_renewal_candidate"),
			RenewalRequestID:             request.RenewalRequestID,
			CertificatePEM:               candidatePEM,
			CertificateFingerprintSHA256: testCertificateFingerprint(candidate),
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if _, raw, readErr := peerRead(conn); readErr == nil {
			envelope, decodeErr := decodePeerEnvelope(raw)
			if decodeErr != nil {
				reportServerError(serverErrors, decodeErr)
				return
			}
			acknowledged.Store(envelope.Type == "certificate_renewal_ack")
		}
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(29*24*time.Hour),
	)
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	err = New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
	if err == nil || !strings.Contains(err.Error(), "persist renewal candidate") {
		t.Fatalf("error = %v, want candidate persistence failure", err)
	}
	select {
	case <-serverDone:
	case serverErr := <-serverErrors:
		t.Fatal(serverErr)
	case <-ctx.Done():
		t.Fatal("timed out waiting for failed candidate persistence")
	}
	if acknowledged.Load() {
		t.Fatal("Agent acknowledged a renewal candidate that was not persisted locally")
	}
	assertNoServerError(t, serverErrors)
}

func TestClientRejectsRenewalWithoutExpiryAdvance(t *testing.T) {
	tests := []struct {
		name        string
		expiryDelta time.Duration
	}{
		{name: "equal expiry"},
		{name: "earlier expiry", expiryDelta: -time.Hour},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := openGatewayStore(t)
			serverErrors := make(chan error, 1)
			var (
				identity            state.Identity
				signer              ed25519.PrivateKey
				ca                  *testsupport.CA
				returnedCertificate string
			)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				var renewal protocol.CertificateRenewalRequest
				if err := peerReadJSON(conn, &renewal); err != nil || renewal.Type != "certificate_renewal_request" {
					reportServerError(serverErrors, fmt.Errorf("read renewal request: %v", err))
					return
				}
				certificate, parseErr := parsePeerCertificate(returnedCertificate)
				if parseErr != nil {
					reportServerError(serverErrors, parseErr)
					return
				}
				if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
					Envelope:                     testEnvelope("certificate_renewal_candidate"),
					RenewalRequestID:             renewal.RenewalRequestID,
					CertificatePEM:               returnedCertificate,
					CertificateFingerprintSHA256: testCertificateFingerprint(certificate),
				}); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()

			identity, signer, ca = testsupport.NewStoredIdentity(
				t,
				store,
				"node-1",
				websocketURL(server.URL),
				time.Now().Add(29*24*time.Hour),
			)
			returnedCertificate = ca.SignCSR(
				t,
				csrPEMForKey(t, signer, identity.NodeID),
				identity.NodeID,
				identity.CertificateExpiresAt.Add(test.expiryDelta),
			)

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			client := New(testGatewayConfig(), store, testInventory(), WithBackoff(func(int) time.Duration {
				cancel()
				return 0
			}))
			err := client.Run(ctx)
			if err == nil || err.Error() != "invalid renewed certificate" {
				t.Errorf("error = %q, want stable certificate rejection %q", err, "invalid renewed certificate")
			}

			current, found, loadErr := store.Identity()
			if loadErr != nil || !found {
				t.Fatalf("load identity after rejected renewal: found=%v err=%v", found, loadErr)
			}
			if current.NodeID != identity.NodeID ||
				current.PrivateKeyPEM != identity.PrivateKeyPEM ||
				current.CertificatePEM != identity.CertificatePEM ||
				current.CACertificatePEM != identity.CACertificatePEM ||
				!current.CertificateExpiresAt.Equal(identity.CertificateExpiresAt) ||
				current.GatewayURL != identity.GatewayURL ||
				current.HeartbeatIntervalSeconds != identity.HeartbeatIntervalSeconds {
				t.Fatal("rejected renewal modified the stored identity")
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientDoesNotRenewCertificateOutsideWindow(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		storedIdentity state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
	)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var inventory protocol.InventoryMessage
		if err := peerReadJSON(conn, &inventory); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if inventory.Type != "inventory" {
			reportServerError(serverErrors, fmt.Errorf("unexpected control message %q", inventory.Type))
			return
		}
		serverReady <- struct{}{}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()

	storedIdentity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(31*24*time.Hour),
	)
	originalCertificate := storedIdentity.CertificatePEM

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "non-renewal inventory")
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	current, found, err := store.Identity()
	if err != nil || !found || current.CertificatePEM != originalCertificate {
		t.Fatalf("certificate outside renewal window changed: found=%v err=%v", found, err)
	}
	assertNoServerError(t, serverErrors)
}

func TestClientReconnectsAndReplaysUnackedEvents(t *testing.T) {
	tests := []struct {
		name             string
		mismatchObserved bool
	}{
		{name: "replays byte-for-byte"},
		{name: "mismatch cleanup returns", mismatchObserved: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := openGatewayStore(t)
			handlerCtx, cancelHandlers := context.WithCancel(context.Background())
			observedBatches := make(chan []byte, 2)
			handlerDone := make(chan struct{}, 2)
			serverErrors := make(chan error, 1)
			ackSent := make(chan struct{}, 1)
			allowAck := make(chan struct{})
			releaseSecond := make(chan struct{})
			var authentications atomic.Int32
			var (
				storedIdentity state.Identity
				signer         ed25519.PrivateKey
				ca             *testsupport.CA
			)

			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				defer func() {
					select {
					case handlerDone <- struct{}{}:
					default:
					}
				}()
				connectionNumber := authentications.Add(1)
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := authenticatePeer(conn, storedIdentity, signer, ca, 5); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				batch, err := readEventBatchFrame(conn)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				observed := append([]byte(nil), batch...)
				if test.mismatchObserved && connectionNumber == 2 {
					observed = append(observed, '\n')
				}
				select {
				case observedBatches <- observed:
				case <-handlerCtx.Done():
					return
				case <-r.Context().Done():
					return
				case <-time.After(peerTimeout):
					reportServerError(serverErrors, fmt.Errorf("timed out publishing observed event batch"))
					return
				}
				if connectionNumber == 1 {
					_ = conn.Close(websocket.StatusInternalError, "retry")
					return
				}
				if connectionNumber != 2 {
					reportServerError(serverErrors, fmt.Errorf("unexpected connection %d", connectionNumber))
					return
				}
				select {
				case <-allowAck:
				case <-handlerCtx.Done():
					return
				case <-r.Context().Done():
					return
				case <-time.After(peerTimeout):
					reportServerError(serverErrors, fmt.Errorf("timed out waiting to send event ACK"))
					return
				}
				if err := peerWriteJSON(conn, protocol.EventsAckMessage{
					Envelope:        testEnvelope("events_acked"),
					ThroughSequence: 1,
				}); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				select {
				case ackSent <- struct{}{}:
				case <-handlerCtx.Done():
					return
				case <-r.Context().Done():
					return
				case <-time.After(peerTimeout):
					reportServerError(serverErrors, fmt.Errorf("timed out publishing event ACK"))
					return
				}
				select {
				case <-releaseSecond:
				case <-handlerCtx.Done():
					return
				case <-r.Context().Done():
					return
				case <-time.After(peerTimeout):
					reportServerError(serverErrors, fmt.Errorf("timed out releasing replay connection"))
					return
				}
			}))
			defer server.Close()
			defer cancelHandlers()

			storedIdentity, signer, ca = testsupport.NewStoredIdentity(
				t,
				store,
				"node-1",
				websocketURL(server.URL),
				time.Now().Add(365*24*time.Hour),
			)
			if _, err := store.AppendEvent("agent_started", map[string]any{"ok": true}); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))

			first := waitBatch(t, ctx, observedBatches, serverErrors)
			waitPendingEvents(t, store, 1)
			second := waitBatch(t, ctx, observedBatches, serverErrors)
			if !bytes.Equal(first, second) {
				cancel()
				cancelHandlers()
				if test.mismatchObserved {
					cleanupCtx, cancelCleanup := context.WithTimeout(context.Background(), peerTimeout)
					defer cancelCleanup()
					for range 2 {
						select {
						case <-handlerDone:
						case <-cleanupCtx.Done():
							t.Fatal("replay mismatch cleanup did not stop every server handler")
						case <-time.After(peerTimeout):
							t.Fatal("timed out waiting for replay mismatch cleanup")
						}
					}
					return
				}
				t.Fatalf("replayed event batch changed\nfirst:  %s\nsecond: %s", first, second)
			}
			if test.mismatchObserved {
				t.Fatal("replay mismatch was not exercised")
			}
			waitPendingEvents(t, store, 1)
			close(allowAck)
			waitSignalOrError(t, ctx, ackSent, serverErrors, "event ACK")
			waitPendingEvents(t, store, 0)
			if got := authentications.Load(); got != 2 {
				cancel()
				cancelHandlers()
				t.Fatalf("authentications = %d, want 2", got)
			}
			cancel()
			close(releaseSecond)
			if err := waitClient(t, runDone); err != nil {
				t.Fatalf("Run returned an error after cancellation: %v", err)
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientBackoffAdvancesAfterAuthenticatedDisconnects(t *testing.T) {
	store := openGatewayStore(t)
	serverErrors := make(chan error, 1)
	var (
		authentications atomic.Int32
		identity        state.Identity
		signer          ed25519.PrivateKey
		ca              *testsupport.CA
	)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		authentications.Add(1)
	}))
	defer server.Close()
	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(365*24*time.Hour),
	)

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	var attempts []int
	client := New(testGatewayConfig(), store, testInventory(), WithBackoff(func(attempt int) time.Duration {
		attempts = append(attempts, attempt)
		if len(attempts) == 4 {
			cancel()
		}
		return 0
	}))
	if err := client.Run(ctx); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	wantAttempts := []int{0, 1, 2, 3}
	if fmt.Sprint(attempts) != fmt.Sprint(wantAttempts) {
		t.Fatalf("backoff attempts = %v, want %v", attempts, wantAttempts)
	}
	if got := authentications.Load(); got != int32(len(wantAttempts)) {
		t.Fatalf("authentications = %d, want %d", got, len(wantAttempts))
	}
	assertNoServerError(t, serverErrors)
}

func TestClientBackoffResetsAfterApplicationRoundTrip(t *testing.T) {
	store := openGatewayStore(t)
	serverErrors := make(chan error, 1)
	var (
		connections atomic.Int32
		identity    state.Identity
		signer      ed25519.PrivateKey
		ca          *testsupport.CA
	)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		connectionNumber := connections.Add(1)
		if connectionNumber < 3 {
			return
		}
		if connectionNumber != 3 {
			reportServerError(serverErrors, fmt.Errorf("unexpected connection %d", connectionNumber))
			return
		}
		if _, err := readEventBatchFrame(conn); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := peerWriteJSON(conn, protocol.EventsAckMessage{
			Envelope:        testEnvelope("events_acked"),
			ThroughSequence: 1,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := peerWriteJSON(conn, protocol.ErrorMessage{
			Envelope:  testEnvelope("error"),
			Code:      "retry_test",
			Message:   "retry after acknowledged connection",
			Retryable: true,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()
	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().Add(365*24*time.Hour),
	)
	if _, err := store.AppendEvent("agent_started", map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	var attempts []int
	client := New(testGatewayConfig(), store, testInventory(), WithBackoff(func(attempt int) time.Duration {
		attempts = append(attempts, attempt)
		if len(attempts) == 3 {
			cancel()
		}
		return 0
	}))
	if err := client.Run(ctx); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	wantAttempts := []int{0, 1, 0}
	if fmt.Sprint(attempts) != fmt.Sprint(wantAttempts) {
		t.Fatalf("backoff attempts = %v, want %v", attempts, wantAttempts)
	}
	waitPendingEvents(t, store, 0)
	assertNoServerError(t, serverErrors)
}

func TestClientRedactsServerControlledProtocolErrors(t *testing.T) {
	const marker = "https://credential-host.example/connect?access_token=secret123"
	tests := []struct {
		name    string
		payload []byte
		want    string
	}{
		{
			name: "allowed-format secret-like error code",
			payload: []byte(`{"protocol_version":1,"type":"error","code":"secret123",` +
				`"message":"` + marker + `","retryable":false}`),
			want: "Gateway server rejected connection",
		},
		{
			name: "credential-bearing unknown field",
			payload: []byte(`{"protocol_version":1,"type":"error","code":"rejected",` +
				`"message":"rejected","retryable":false,"` + marker + `":true}`),
			want: "invalid server message",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := openGatewayStore(t)
			serverErrors := make(chan error, 1)
			var identity state.Identity
			var signer ed25519.PrivateKey
			var ca *testsupport.CA
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				if err := peerWrite(conn, test.payload); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			identity, signer, ca = testsupport.NewStoredIdentity(
				t,
				store,
				"node-1",
				websocketURL(server.URL),
				time.Now().Add(365*24*time.Hour),
			)

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			if err == nil {
				t.Fatal("expected fatal protocol error")
			}
			if err.Error() != test.want {
				t.Fatalf("error = %q, want stable redacted error %q", err, test.want)
			}
			for _, sensitive := range []string{marker, "credential-host.example", "access_token=", "secret123"} {
				if strings.Contains(err.Error(), sensitive) {
					t.Fatalf("protocol error leaked %q: %v", sensitive, err)
				}
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientRejectsProtocolMismatch(t *testing.T) {
	tests := []struct {
		challenge protocol.ChallengeMessage
		want      string
	}{
		{
			challenge: protocol.ChallengeMessage{Envelope: protocol.Envelope{ProtocolVersion: 2, Type: "challenge"}, Nonce: base64.StdEncoding.EncodeToString([]byte("nonce"))},
			want:      "unsupported server protocol version",
		},
		{
			challenge: protocol.ChallengeMessage{Envelope: protocol.Envelope{ProtocolVersion: 1, Type: "authenticated"}, Nonce: base64.StdEncoding.EncodeToString([]byte("nonce"))},
			want:      "unexpected server message type",
		},
	}
	for _, test := range tests {
		t.Run(fmt.Sprintf("version_%d_type_%s", test.challenge.ProtocolVersion, test.challenge.Type), func(t *testing.T) {
			store := openGatewayStore(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					return
				}
				defer conn.CloseNow()
				_ = peerWriteJSON(conn, test.challenge)
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(365*24*time.Hour))

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			if err == nil || err.Error() != test.want {
				t.Fatalf("error = %q, want stable protocol rejection %q", err, test.want)
			}
		})
	}
}

func TestClientRejectsHeartbeatOutsideBoundsAndOverflow(t *testing.T) {
	tests := []struct {
		name            string
		heartbeat       int
		raw             string
		want            string
		controlledValue string
	}{
		{name: "below minimum", heartbeat: 4, want: "heartbeat interval is outside allowed range", controlledValue: "4"},
		{name: "above maximum", heartbeat: 301, want: "heartbeat interval is outside allowed range", controlledValue: "301"},
		{name: "malicious negative value", heartbeat: -1000000007, want: "heartbeat interval is outside allowed range", controlledValue: "-1000000007"},
		{name: "numeric overflow", raw: `{"protocol_version":1,"type":"authenticated","heartbeat_interval_seconds":9223372036854775808}`, want: "invalid server message", controlledValue: "9223372036854775808"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := openGatewayStore(t)
			serverErrors := make(chan error, 1)
			var identity state.Identity
			var signer ed25519.PrivateKey
			var ca *testsupport.CA
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := challengeAndVerifyAuthenticate(conn, identity, signer, ca); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				if test.raw != "" {
					err = peerWrite(conn, []byte(test.raw))
				} else {
					err = peerWriteJSON(conn, protocol.AuthenticatedMessage{
						Envelope:                 testEnvelope("authenticated"),
						HeartbeatIntervalSeconds: test.heartbeat,
					})
				}
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			identity, signer, ca = testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(365*24*time.Hour))

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			if err == nil || err.Error() != test.want {
				t.Fatalf("error = %q, want stable heartbeat rejection %q", err, test.want)
			}
			if strings.Contains(err.Error(), test.controlledValue) {
				t.Fatalf("heartbeat rejection leaked server value %q: %v", test.controlledValue, err)
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientRejectsAckAheadOfHighestSent(t *testing.T) {
	for _, throughSequence := range []uint64{2, ^uint64(0)} {
		t.Run(fmt.Sprintf("through sequence %d", throughSequence), func(t *testing.T) {
			store := openGatewayStore(t)
			serverErrors := make(chan error, 1)
			var identity state.Identity
			var signer ed25519.PrivateKey
			var ca *testsupport.CA
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				if _, err := readEventBatchFrame(conn); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				if err := peerWriteJSON(conn, protocol.EventsAckMessage{
					Envelope:        testEnvelope("events_acked"),
					ThroughSequence: throughSequence,
				}); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			identity, signer, ca = testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(365*24*time.Hour))
			if _, err := store.AppendEvent("agent_started", map[string]any{}); err != nil {
				t.Fatal(err)
			}

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			const want = "event ACK is ahead of highest sent sequence"
			if err == nil || err.Error() != want {
				t.Fatalf("error = %q, want stable ACK-ahead rejection %q", err, want)
			}
			for _, controlledValue := range []string{fmt.Sprint(throughSequence), "1"} {
				if strings.Contains(err.Error(), controlledValue) {
					t.Fatalf("ACK-ahead rejection leaked sequence value %q: %v", controlledValue, err)
				}
			}
			waitPendingEvents(t, store, 1)
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestDecodeServerMessageRejectsUnsafeErrorCode(t *testing.T) {
	raw := []byte(`{"protocol_version":1,"type":"error","code":"wss://gateway.example/connect?token=secret","message":"rejected","retryable":false}`)

	_, err := decodeServerMessage(raw)
	if err == nil {
		t.Fatal("expected unsafe server error code to be rejected")
	}
	if strings.Contains(err.Error(), "token=secret") {
		t.Fatalf("protocol error leaked the unsafe code: %v", err)
	}
}

func TestDecodeServerMessageRedactsUnexpectedType(t *testing.T) {
	marker := "wss://host/path?token=secret"
	raw := []byte(fmt.Sprintf(`{"protocol_version":1,"type":%q}`, marker))

	_, err := decodeServerMessage(raw)
	assertRedactedUnexpectedMessageTypeError(t, err, marker)
}

func TestRequireEnvelopeRedactsUnexpectedType(t *testing.T) {
	marker := "wss://host/path?token=secret"
	err := requireEnvelope(protocol.Envelope{
		ProtocolVersion: protocol.ProtocolVersion,
		Type:            marker,
	}, "authenticated")

	assertRedactedUnexpectedMessageTypeError(t, err, marker)
}

func assertRedactedUnexpectedMessageTypeError(t *testing.T, err error, marker string) {
	t.Helper()
	const want = "unexpected server message type"
	if err == nil {
		t.Fatal("expected unexpected-message rejection")
	}
	if err.Error() != want {
		t.Fatalf("error = %q, want stable redacted error %q", err, want)
	}
	for _, sensitive := range []string{marker, "host", "token=", "secret"} {
		if strings.Contains(err.Error(), sensitive) {
			t.Fatalf("protocol error leaked %q: %v", sensitive, err)
		}
	}
}

func TestClientPromotesPendingCandidateAfterCurrentCertificateExpiresWithoutFallbackLoop(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	var (
		identity          state.Identity
		candidateIdentity state.Identity
		signer            ed25519.PrivateKey
		ca                *testsupport.CA
		candidatePEM      string
		connections       atomic.Int32
	)
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if connections.Add(1) != 1 {
			reportServerError(serverErrors, fmt.Errorf("unexpected fallback connection %d", connections.Load()))
			return
		}
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, candidateIdentity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := expectLiveInventory(conn); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		serverReady <- struct{}{}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().UTC().Add(time.Hour),
	)
	pending := state.PendingRenewal{
		RequestID: "renewal-expired-current-001",
		CSRPEM:    csrPEMForKey(t, signer, identity.NodeID),
	}
	if err := store.SavePendingRenewal(pending); err != nil {
		t.Fatal(err)
	}
	candidatePEM = ca.SignCSR(
		t,
		pending.CSRPEM,
		identity.NodeID,
		time.Now().UTC().Add(365*24*time.Hour),
	)
	candidate, err := parsePeerCertificate(candidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SaveRenewalCandidate(
		pending.RequestID,
		candidatePEM,
		testCertificateFingerprint(candidate),
	); err != nil {
		t.Fatal(err)
	}
	expiredCurrentPEM := ca.SignCSR(
		t,
		csrPEMForKey(t, signer, identity.NodeID),
		identity.NodeID,
		time.Now().UTC().Add(-time.Hour),
	)
	expiredCurrent, err := parsePeerCertificate(expiredCurrentPEM)
	if err != nil {
		t.Fatal(err)
	}
	identity.CertificatePEM = expiredCurrentPEM
	identity.CertificateExpiresAt = expiredCurrent.NotAfter
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	rewriteStoredIdentity(t, path, identity)
	candidateIdentity = identity
	candidateIdentity.CertificatePEM = candidatePEM
	candidateIdentity.CertificateExpiresAt = candidate.NotAfter

	reopened, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if closeErr := reopened.Close(); closeErr != nil {
			t.Errorf("close reopened store: %v", closeErr)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	runDone := runClient(ctx, New(testGatewayConfig(), reopened, testInventory(), WithBackoff(zeroBackoff)))
	select {
	case <-serverReady:
	case err := <-runDone:
		t.Fatalf("Run returned before pending-candidate authentication: %v", err)
	case err := <-serverErrors:
		t.Fatal(err)
	case <-ctx.Done():
		t.Fatal("timed out waiting for pending-candidate authentication")
	}
	assertPromotedRenewal(t, reopened, candidatePEM)
	if connections.Load() != 1 {
		t.Fatalf("connections = %d, want one pending-candidate authentication", connections.Load())
	}
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
}

func TestClientClearsExpiredPendingCandidateAndReissues(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	serverReady := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var (
		identity       state.Identity
		signer         ed25519.PrivateKey
		ca             *testsupport.CA
		expiredPending state.PendingRenewal
		reissuedPEM    string
		connections    atomic.Int32
	)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if connections.Add(1) != 1 {
			reportServerError(serverErrors, fmt.Errorf("unexpected renewal connection %d", connections.Load()))
			return
		}
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		var request protocol.CertificateRenewalRequest
		if err := peerReadJSON(conn, &request); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if request.Envelope != testEnvelope("certificate_renewal_request") ||
			request.RenewalRequestID == "" || request.RenewalRequestID == expiredPending.RequestID {
			reportServerError(serverErrors, fmt.Errorf("expired candidate was not reissued: %#v", request))
			return
		}
		reissuedPEM = ca.SignCSR(
			t,
			request.CSRPEM,
			identity.NodeID,
			time.Now().UTC().Add(365*24*time.Hour),
		)
		reissuedCertificate, err := parsePeerCertificate(reissuedPEM)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		fingerprint := testCertificateFingerprint(reissuedCertificate)
		if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
			Envelope:                     testEnvelope("certificate_renewal_candidate"),
			RenewalRequestID:             request.RenewalRequestID,
			CertificatePEM:               reissuedPEM,
			CertificateFingerprintSHA256: fingerprint,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := expectRenewalAcknowledgement(
			conn,
			state.PendingRenewal{RequestID: request.RenewalRequestID},
			fingerprint,
		); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := peerWriteJSON(conn, protocol.CertificateRenewalActivatedMessage{
			Envelope:                     testEnvelope("certificate_renewal_activated"),
			RenewalRequestID:             request.RenewalRequestID,
			CertificateFingerprintSHA256: fingerprint,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		if err := expectLiveInventory(conn); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		serverReady <- struct{}{}
		_, _, _ = peerRead(conn)
	}))
	defer server.Close()

	identity, signer, ca = testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		websocketURL(server.URL),
		time.Now().UTC().Add(29*24*time.Hour),
	)
	expiredPending = state.PendingRenewal{
		RequestID: "renewal-expired-candidate-001",
		CSRPEM:    csrPEMForKey(t, signer, identity.NodeID),
	}
	if err := store.SavePendingRenewal(expiredPending); err != nil {
		t.Fatal(err)
	}
	persistedCertificatePEM := ca.SignCSR(
		t,
		expiredPending.CSRPEM,
		identity.NodeID,
		time.Now().UTC().Add(365*24*time.Hour),
	)
	persistedCertificate, err := parsePeerCertificate(persistedCertificatePEM)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SaveRenewalCandidate(
		expiredPending.RequestID,
		persistedCertificatePEM,
		testCertificateFingerprint(persistedCertificate),
	); err != nil {
		t.Fatal(err)
	}
	expiredCertificatePEM := ca.SignCSR(
		t,
		expiredPending.CSRPEM,
		identity.NodeID,
		time.Now().UTC().Add(-time.Hour),
	)
	expiredCertificate, err := parsePeerCertificate(expiredCertificatePEM)
	if err != nil {
		t.Fatal(err)
	}
	expiredPending.CertificatePEM = expiredCertificatePEM
	expiredPending.CertificateFingerprintSHA256 = testCertificateFingerprint(expiredCertificate)
	expiredPending.CertificateExpiresAt = expiredCertificate.NotAfter
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	rewriteStoredPendingRenewal(t, path, expiredPending)
	store, err = state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if closeErr := store.Close(); closeErr != nil {
			t.Errorf("close store: %v", closeErr)
		}
	}()

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, ctx, serverReady, serverErrors, "expired pending candidate recovery")
	assertPromotedRenewal(t, store, reissuedPEM)
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	if connections.Load() != 1 {
		t.Fatalf("connections = %d, want 1", connections.Load())
	}
	assertNoServerError(t, serverErrors)
}

func TestClientClearsExpiredPendingCandidateAndStopsForReEnrollment(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	identity, signer, ca := testsupport.NewStoredIdentity(
		t,
		store,
		"node-1",
		"ws://gateway.invalid/agent/v1/connect",
		time.Now().UTC().Add(time.Hour),
	)
	pending := state.PendingRenewal{
		RequestID: "renewal-expired-reenroll-001",
		CSRPEM:    csrPEMForKey(t, signer, identity.NodeID),
	}
	if err := store.SavePendingRenewal(pending); err != nil {
		t.Fatal(err)
	}
	persistedCandidatePEM := ca.SignCSR(
		t,
		pending.CSRPEM,
		identity.NodeID,
		time.Now().UTC().Add(365*24*time.Hour),
	)
	persistedCandidate, err := parsePeerCertificate(persistedCandidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SaveRenewalCandidate(
		pending.RequestID,
		persistedCandidatePEM,
		testCertificateFingerprint(persistedCandidate),
	); err != nil {
		t.Fatal(err)
	}
	expiredCurrentPEM := ca.SignCSR(
		t,
		csrPEMForKey(t, signer, identity.NodeID),
		identity.NodeID,
		time.Now().UTC().Add(-time.Hour),
	)
	expiredCurrent, err := parsePeerCertificate(expiredCurrentPEM)
	if err != nil {
		t.Fatal(err)
	}
	identity.CertificatePEM = expiredCurrentPEM
	identity.CertificateExpiresAt = expiredCurrent.NotAfter
	expiredPendingPEM := ca.SignCSR(
		t,
		pending.CSRPEM,
		identity.NodeID,
		time.Now().UTC().Add(-time.Hour),
	)
	expiredPendingCertificate, err := parsePeerCertificate(expiredPendingPEM)
	if err != nil {
		t.Fatal(err)
	}
	pending.CertificatePEM = expiredPendingPEM
	pending.CertificateFingerprintSHA256 = testCertificateFingerprint(expiredPendingCertificate)
	pending.CertificateExpiresAt = expiredPendingCertificate.NotAfter
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	rewriteStoredIdentity(t, path, identity)
	rewriteStoredPendingRenewal(t, path, pending)

	reopened, err := state.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if closeErr := reopened.Close(); closeErr != nil {
			t.Errorf("close reopened store: %v", closeErr)
		}
	}()
	var backoffCalls atomic.Int32
	err = New(
		testGatewayConfig(),
		reopened,
		testInventory(),
		WithBackoff(func(int) time.Duration {
			backoffCalls.Add(1)
			return 0
		}),
	).Run(context.Background())
	if err != errRenewalRecoveryRequired {
		t.Fatalf("Run error = %v, want %v", err, errRenewalRecoveryRequired)
	}
	if backoffCalls.Load() != 0 {
		t.Fatalf("backoff calls = %d, want 0", backoffCalls.Load())
	}
	if _, found, err := reopened.PendingRenewal(); err != nil || found {
		t.Fatalf("expired pending renewal remained after recovery stop: found=%v err=%v", found, err)
	}
}

func TestClientRedactsInvalidStoredCertificates(t *testing.T) {
	const (
		subjectMarker = "stored-cert-subject-secret-marker"
		hostMarker    = "stored-cert-host.invalid"
		secretMarker  = "stored-cert-secret-marker"
	)
	expiredAt := time.Now().UTC().Add(-time.Hour).Truncate(time.Second)
	tests := []struct {
		name   string
		mutate func(*testing.T, *state.Identity, ed25519.PrivateKey, *testsupport.CA)
	}{
		{
			name: "malformed stored leaf",
			mutate: func(_ *testing.T, identity *state.Identity, _ ed25519.PrivateKey, _ *testsupport.CA) {
				identity.CertificatePEM = string(pem.EncodeToMemory(&pem.Block{
					Type:  "CERTIFICATE",
					Bytes: []byte("https://" + hostMarker + "/leaf?token=" + secretMarker),
				}))
			},
		},
		{
			name: "expired stored leaf",
			mutate: func(t *testing.T, identity *state.Identity, signer ed25519.PrivateKey, ca *testsupport.CA) {
				identity.CertificatePEM = ca.SignCSR(
					t,
					csrPEMForKey(t, signer, identity.NodeID),
					identity.NodeID,
					expiredAt,
				)
			},
		},
		{
			name: "malformed stored CA",
			mutate: func(_ *testing.T, identity *state.Identity, _ ed25519.PrivateKey, _ *testsupport.CA) {
				identity.CACertificatePEM = string(pem.EncodeToMemory(&pem.Block{
					Type:  "CERTIFICATE",
					Bytes: []byte("https://" + hostMarker + "/ca?token=" + secretMarker),
				}))
			},
		},
		{
			name: "invalid chain",
			mutate: func(t *testing.T, identity *state.Identity, signer ed25519.PrivateKey, _ *testsupport.CA) {
				identity.CertificatePEM = testsupport.NewCA(t).SignCSR(
					t,
					csrPEMForKey(t, signer, identity.NodeID),
					identity.NodeID,
					time.Now().Add(365*24*time.Hour),
				)
			},
		},
		{
			name: "stored expiry mismatch",
			mutate: func(_ *testing.T, identity *state.Identity, _ ed25519.PrivateKey, _ *testsupport.CA) {
				identity.CertificateExpiresAt = time.Date(2042, time.January, 2, 3, 4, 5, 0, time.UTC)
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "agent.db")
			seedStore, err := state.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			identity, signer, ca := testsupport.NewStoredIdentity(
				t,
				seedStore,
				subjectMarker,
				"ws://"+hostMarker+"/agent/v1/connect?token="+secretMarker,
				time.Now().Add(365*24*time.Hour),
			)
			test.mutate(t, &identity, signer, ca)
			if err := seedStore.Close(); err != nil {
				t.Fatal(err)
			}
			rewriteStoredIdentity(t, path, identity)

			store, err := state.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				if err := store.Close(); err != nil {
					t.Errorf("close store: %v", err)
				}
			})

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err = New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			const want = "invalid stored certificate"
			if err == nil || err.Error() != want {
				t.Fatalf("error = %q, want stable stored-certificate rejection %q", err, want)
			}
			for _, sensitive := range []string{
				"x509",
				subjectMarker,
				expiredAt.Format(time.RFC3339),
				"2042-01-02T03:04:05Z",
				"BEGIN CERTIFICATE",
				hostMarker,
				secretMarker,
			} {
				if strings.Contains(err.Error(), sensitive) {
					t.Fatalf("stored-certificate rejection leaked %q: %v", sensitive, err)
				}
			}
		})
	}
}

func rewriteStoredIdentity(t *testing.T, path string, identity state.Identity) {
	t.Helper()
	encoded, err := json.Marshal(identity)
	if err != nil {
		t.Fatal(err)
	}
	database, err := bbolt.Open(path, 0o600, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := database.Close(); err != nil {
			t.Errorf("close raw state database: %v", err)
		}
	}()
	if err := database.Update(func(tx *bbolt.Tx) error {
		bucket := tx.Bucket([]byte("identity"))
		if bucket == nil {
			return fmt.Errorf("identity bucket is missing")
		}
		return bucket.Put([]byte("current"), encoded)
	}); err != nil {
		t.Fatal(err)
	}
}

func rewriteStoredPendingRenewal(t *testing.T, path string, pending state.PendingRenewal) {
	t.Helper()
	encoded, err := json.Marshal(pending)
	if err != nil {
		t.Fatal(err)
	}
	database, err := bbolt.Open(path, 0o600, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := database.Close(); err != nil {
			t.Errorf("close raw state database: %v", err)
		}
	}()
	if err := database.Update(func(tx *bbolt.Tx) error {
		bucket := tx.Bucket([]byte("identity"))
		if bucket == nil {
			return fmt.Errorf("identity bucket is missing")
		}
		return bucket.Put([]byte("pending_renewal"), encoded)
	}); err != nil {
		t.Fatal(err)
	}
}

func TestClientRejectsInvalidRenewedCertificates(t *testing.T) {
	const maliciousMarker = "wss://attacker.example/renew?certificate=secret123"
	tests := []struct {
		name        string
		sensitive   []string
		certificate func(*testing.T, ed25519.PrivateKey, *testsupport.CA) string
	}{
		{
			name: "private key mismatch",
			certificate: func(t *testing.T, _ ed25519.PrivateKey, ca *testsupport.CA) string {
				_, otherKey, err := ed25519.GenerateKey(rand.Reader)
				if err != nil {
					t.Fatal(err)
				}
				return ca.SignCSR(t, csrPEMForKey(t, otherKey, "node-1"), "node-1", time.Now().Add(365*24*time.Hour))
			},
		},
		{
			name: "untrusted chain",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return testsupport.NewCA(t).SignCSR(t, csrPEMForKey(t, signer, "node-1"), "node-1", time.Now().Add(365*24*time.Hour))
			},
		},
		{
			name: "CA instead of end entity",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return selfSignedCertificate(t, signer, true, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "missing client auth",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return selfSignedCertificate(t, signer, false, []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth})
			},
		},
		{
			name:      "malicious subject",
			sensitive: []string{maliciousMarker, "attacker.example", "certificate=", "secret123"},
			certificate: func(t *testing.T, signer ed25519.PrivateKey, ca *testsupport.CA) string {
				return ca.SignCSR(t, csrPEMForKey(t, signer, maliciousMarker), maliciousMarker, time.Now().Add(365*24*time.Hour))
			},
		},
		{
			name:      "malformed x509 with PEM content",
			sensitive: []string{maliciousMarker, "attacker.example", "certificate=", "secret123", "x509:"},
			certificate: func(*testing.T, ed25519.PrivateKey, *testsupport.CA) string {
				return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: []byte(maliciousMarker)}))
			},
		},
		{
			name: "expired certificate",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, ca *testsupport.CA) string {
				return ca.SignCSR(t, csrPEMForKey(t, signer, "node-1"), "node-1", time.Now().Add(-time.Hour))
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store := openGatewayStore(t)
			serverErrors := make(chan error, 1)
			var identity state.Identity
			var signer ed25519.PrivateKey
			var ca *testsupport.CA
			var returnedCertificate string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					reportServerError(serverErrors, err)
					return
				}
				defer conn.CloseNow()
				if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				var renewal protocol.CertificateRenewalRequest
				if err := peerReadJSON(conn, &renewal); err != nil || renewal.Type != "certificate_renewal_request" {
					reportServerError(serverErrors, fmt.Errorf("read renewal request: %v", err))
					return
				}
				fingerprint := strings.Repeat("0", 64)
				if certificate, parseErr := parsePeerCertificate(returnedCertificate); parseErr == nil {
					fingerprint = testCertificateFingerprint(certificate)
				}
				if err := peerWriteJSON(conn, protocol.CertificateRenewalCandidateMessage{
					Envelope:                     testEnvelope("certificate_renewal_candidate"),
					RenewalRequestID:             renewal.RenewalRequestID,
					CertificatePEM:               returnedCertificate,
					CertificateFingerprintSHA256: fingerprint,
				}); err != nil {
					reportServerError(serverErrors, err)
					return
				}
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			identity, signer, ca = testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(29*24*time.Hour))
			returnedCertificate = test.certificate(t, signer, ca)
			originalCertificate := identity.CertificatePEM

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			const want = "invalid renewed certificate"
			if err == nil || err.Error() != want {
				t.Fatalf("error = %q, want stable certificate rejection %q", err, want)
			}
			for _, sensitive := range test.sensitive {
				if strings.Contains(err.Error(), sensitive) {
					t.Fatalf("certificate rejection leaked %q: %v", sensitive, err)
				}
			}
			current, found, loadErr := store.Identity()
			if loadErr != nil || !found || current.CertificatePEM != originalCertificate {
				t.Fatalf("invalid certificate was persisted: found=%v err=%v", found, loadErr)
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientCancellationJoinsReaderGoroutine(t *testing.T) {
	store := openGatewayStore(t)
	serverReady := make(chan struct{}, 1)
	serverClosed := make(chan struct{}, 1)
	serverErrors := make(chan error, 1)
	var identity state.Identity
	var signer ed25519.PrivateKey
	var ca *testsupport.CA
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			reportServerError(serverErrors, err)
			return
		}
		defer conn.CloseNow()
		if err := authenticatePeer(conn, identity, signer, ca, 5); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		for range 2 {
			if _, _, err := peerRead(conn); err != nil {
				reportServerError(serverErrors, err)
				return
			}
		}
		serverReady <- struct{}{}
		ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
		defer cancel()
		_, _, _ = conn.Read(ctx)
		serverClosed <- struct{}{}
	}))
	defer server.Close()
	identity, signer, ca = testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(365*24*time.Hour))

	baselineReaders := gatewayReaderGoroutines()
	ctx, cancel := context.WithCancel(context.Background())
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))
	waitSignalOrError(t, context.Background(), serverReady, serverErrors, "initial messages")
	waitFor(t, func() bool { return gatewayReaderGoroutines() > baselineReaders }, "Gateway reader goroutine to start")

	started := time.Now()
	cancel()
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("cancellation took %s", elapsed)
	}
	waitFor(t, func() bool { return gatewayReaderGoroutines() == baselineReaders }, "Gateway reader goroutine to stop")
	select {
	case <-serverClosed:
	case <-time.After(time.Second):
		t.Fatal("server did not observe connection shutdown")
	}
	assertNoServerError(t, serverErrors)
}

func TestGatewayHTTPClientDoesNotTrustDeviceIssuerAsServerRoot(t *testing.T) {
	deviceIssuer := testsupport.NewCA(t)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err == nil {
			_ = conn.CloseNow()
		}
	}))
	server.TLS = deviceIssuer.ServerTLSConfig(t, "127.0.0.1")
	server.StartTLS()
	defer server.Close()

	client := New(config.Config{}, nil, protocol.InventoryMessage{})
	httpClient, transport, err := client.gatewayHTTPClient(state.Identity{
		GatewayURL:       strings.Replace(server.URL, "https://", "wss://", 1) + "/agent/v1/connect",
		CACertificatePEM: deviceIssuer.PEM(),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer transport.CloseIdleConnections()

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	conn, _, dialErr := websocket.Dial(ctx, strings.Replace(server.URL, "https://", "wss://", 1)+"/agent/v1/connect", &websocket.DialOptions{HTTPClient: httpClient})
	if conn != nil {
		_ = conn.CloseNow()
	}
	if dialErr == nil {
		t.Fatal("device issuer CA must not authenticate the Gateway TLS server")
	}
}

func TestGatewayHTTPClientTrustsOnlyExplicitConfiguredServerCA(t *testing.T) {
	serverCA := testsupport.NewCA(t)
	serverCAPath := filepath.Join(t.TempDir(), "server-ca.crt")
	if err := os.WriteFile(serverCAPath, []byte(serverCA.PEM()), 0o600); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err == nil {
			_ = conn.CloseNow()
		}
	}))
	server.TLS = serverCA.ServerTLSConfig(t, "127.0.0.1")
	server.StartTLS()
	defer server.Close()

	deviceIssuer := testsupport.NewCA(t)
	client := New(config.Config{ServerCAFile: serverCAPath}, nil, protocol.InventoryMessage{})
	httpClient, transport, err := client.gatewayHTTPClient(state.Identity{
		GatewayURL:       strings.Replace(server.URL, "https://", "wss://", 1) + "/agent/v1/connect",
		CACertificatePEM: deviceIssuer.PEM(),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer transport.CloseIdleConnections()

	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, strings.Replace(server.URL, "https://", "wss://", 1)+"/agent/v1/connect", &websocket.DialOptions{HTTPClient: httpClient})
	if err != nil {
		t.Fatalf("explicit configured server CA did not authenticate Gateway TLS: %v", err)
	}
	_ = conn.CloseNow()
}

func TestGatewayHTTPClientRestrictsPlaintextToExplicitLocalHosts(t *testing.T) {
	ca := testsupport.NewCA(t)
	tests := []struct {
		name          string
		gatewayURL    string
		allowInsecure bool
		wantError     bool
	}{
		{name: "localhost", gatewayURL: "ws://localhost:8080/agent/v1/connect", allowInsecure: true},
		{name: "IPv4 loopback", gatewayURL: "ws://127.0.0.1/agent/v1/connect", allowInsecure: true},
		{name: "Compose service", gatewayURL: "ws://api-service/agent/v1/connect", allowInsecure: true},
		{name: "Docker host", gatewayURL: "ws://host.docker.internal/agent/v1/connect", allowInsecure: true},
		{name: "case insensitive host", gatewayURL: "ws://LOCALHOST/agent/v1/connect", allowInsecure: true},
		{name: "insecure mode disabled", gatewayURL: "ws://localhost/agent/v1/connect", wantError: true},
		{name: "remote hostname", gatewayURL: "ws://gateway.example/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "public IP", gatewayURL: "ws://203.0.113.10/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "private IPv4", gatewayURL: "ws://192.168.1.10/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "alternate loopback IPv4", gatewayURL: "ws://127.0.0.2/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "IPv6 loopback", gatewayURL: "ws://[::1]/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "userinfo", gatewayURL: "ws://user@localhost/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "empty hostname", gatewayURL: "ws:///agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "malformed URL", gatewayURL: "ws://[::1/agent/v1/connect", allowInsecure: true, wantError: true},
		{name: "secure remote hostname", gatewayURL: "wss://gateway.example/agent/v1/connect"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client := New(config.Config{AllowInsecureLocal: test.allowInsecure}, nil, protocol.InventoryMessage{})
			httpClient, transport, err := client.gatewayHTTPClient(state.Identity{
				GatewayURL:       test.gatewayURL,
				CACertificatePEM: ca.PEM(),
			})
			if test.wantError {
				if err == nil {
					transport.CloseIdleConnections()
					t.Fatalf("gatewayHTTPClient(%q) succeeded, want rejection", test.gatewayURL)
				}
				return
			}
			if err != nil {
				t.Fatalf("gatewayHTTPClient(%q): %v", test.gatewayURL, err)
			}
			if httpClient == nil || transport == nil {
				t.Fatal("gatewayHTTPClient returned nil client or transport")
			}
			transport.CloseIdleConnections()
		})
	}
}

func authenticatePeer(
	conn *websocket.Conn,
	identity state.Identity,
	signer ed25519.PrivateKey,
	ca *testsupport.CA,
	heartbeat int,
) error {
	if err := challengeAndVerifyAuthenticate(conn, identity, signer, ca); err != nil {
		return err
	}
	return peerWriteJSON(conn, protocol.AuthenticatedMessage{
		Envelope:                 testEnvelope("authenticated"),
		HeartbeatIntervalSeconds: heartbeat,
	})
}

func challengeAndVerifyAuthenticate(
	conn *websocket.Conn,
	identity state.Identity,
	signer ed25519.PrivateKey,
	ca *testsupport.CA,
) error {
	nonce := []byte("0123456789abcdef0123456789abcdef")
	if err := peerWriteJSON(conn, protocol.ChallengeMessage{
		Envelope: testEnvelope("challenge"),
		Nonce:    base64.StdEncoding.EncodeToString(nonce),
	}); err != nil {
		return err
	}
	var authenticate protocol.AuthenticateMessage
	if err := peerReadJSON(conn, &authenticate); err != nil {
		return err
	}
	if authenticate.Envelope != testEnvelope("authenticate") || authenticate.NodeID != identity.NodeID {
		return fmt.Errorf("unexpected authenticate message: %#v", authenticate)
	}
	if authenticate.CertificatePEM != identity.CertificatePEM {
		return fmt.Errorf("authenticate certificate does not match stored identity")
	}
	certificate, err := parsePeerCertificate(authenticate.CertificatePEM)
	if err != nil {
		return err
	}
	publicKey, ok := certificate.PublicKey.(ed25519.PublicKey)
	if !ok || !bytes.Equal(publicKey, signer.Public().(ed25519.PublicKey)) {
		return fmt.Errorf("authenticate certificate does not contain the stored Ed25519 key")
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(authenticate.Signature)
	if err != nil || !ed25519.Verify(publicKey, nonce, signature) {
		return fmt.Errorf("authenticate signature is invalid")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM([]byte(ca.PEM())) {
		return fmt.Errorf("test CA is invalid")
	}
	if _, err := certificate.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		return fmt.Errorf("authenticate certificate chain: %w", err)
	}
	return nil
}

func readEventBatchFrame(conn *websocket.Conn) ([]byte, error) {
	for range 3 {
		_, raw, err := peerRead(conn)
		if err != nil {
			return nil, err
		}
		envelope, err := decodePeerEnvelope(raw)
		if err != nil {
			return nil, err
		}
		if envelope.Type == "event_batch" {
			var batch protocol.EventBatchMessage
			if err := json.Unmarshal(raw, &batch); err != nil || len(batch.Events) != 1 || batch.Events[0].Sequence != 1 {
				return nil, fmt.Errorf("invalid event batch: %s", raw)
			}
			return raw, nil
		}
	}
	return nil, fmt.Errorf("event batch was not sent immediately")
}

func peerReadJSON(conn *websocket.Conn, target any) error {
	_, raw, err := peerRead(conn)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("decode peer message: %w", err)
	}
	return nil
}

func peerRead(conn *websocket.Conn) (websocket.MessageType, []byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	return conn.Read(ctx)
}

func peerWriteJSON(conn *websocket.Conn, message any) error {
	encoded, err := json.Marshal(message)
	if err != nil {
		return err
	}
	return peerWrite(conn, encoded)
}

func peerWrite(conn *websocket.Conn, encoded []byte) error {
	ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
	defer cancel()
	return conn.Write(ctx, websocket.MessageText, encoded)
}

func decodePeerEnvelope(raw []byte) (protocol.Envelope, error) {
	var envelope protocol.Envelope
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return protocol.Envelope{}, err
	}
	if envelope.ProtocolVersion != protocol.ProtocolVersion {
		return protocol.Envelope{}, fmt.Errorf("unexpected protocol version %d", envelope.ProtocolVersion)
	}
	return envelope, nil
}

func testEnvelope(messageType string) protocol.Envelope {
	return protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: messageType}
}

func expectRenewalAcknowledgement(
	conn *websocket.Conn,
	pending state.PendingRenewal,
	fingerprint string,
) error {
	var acknowledgement protocol.CertificateRenewalAckMessage
	if err := peerReadJSON(conn, &acknowledgement); err != nil {
		return err
	}
	if acknowledgement.Envelope != testEnvelope("certificate_renewal_ack") ||
		acknowledgement.RenewalRequestID != pending.RequestID ||
		acknowledgement.CertificateFingerprintSHA256 != fingerprint {
		return fmt.Errorf("unexpected renewal acknowledgement: %#v", acknowledgement)
	}
	return nil
}

func expectLiveInventory(conn *websocket.Conn) error {
	var inventory protocol.InventoryMessage
	if err := peerReadJSON(conn, &inventory); err != nil {
		return err
	}
	if inventory.Envelope != testEnvelope("inventory") {
		return fmt.Errorf("expected inventory after renewal, got %#v", inventory.Envelope)
	}
	return nil
}

func savePendingRenewalCandidate(
	t *testing.T,
	store *state.Store,
	identity state.Identity,
	signer ed25519.PrivateKey,
	ca *testsupport.CA,
) (state.PendingRenewal, string, string) {
	t.Helper()
	pending := state.PendingRenewal{
		RequestID: "renewal-pending-candidate-001",
		CSRPEM:    csrPEMForKey(t, signer, identity.NodeID),
	}
	if err := store.SavePendingRenewal(pending); err != nil {
		t.Fatal(err)
	}
	candidatePEM := ca.SignCSR(t, pending.CSRPEM, identity.NodeID, time.Now().Add(365*24*time.Hour))
	candidate, err := parsePeerCertificate(candidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	fingerprint := testCertificateFingerprint(candidate)
	if err := store.SaveRenewalCandidate(pending.RequestID, candidatePEM, fingerprint); err != nil {
		t.Fatal(err)
	}
	return pending, candidatePEM, fingerprint
}

func assertPromotedRenewal(t *testing.T, store *state.Store, candidatePEM string) {
	t.Helper()
	identity, found, err := store.Identity()
	if err != nil || !found || identity.CertificatePEM != candidatePEM {
		t.Fatalf("renewal was not promoted: found=%v identity=%#v err=%v", found, identity, err)
	}
	if _, found, err := store.PendingRenewal(); err != nil || found {
		t.Fatalf("pending renewal remained after promotion: found=%v err=%v", found, err)
	}
}

func testInventory() protocol.InventoryMessage {
	return protocol.InventoryMessage{
		Envelope:     testEnvelope("inventory"),
		Architecture: "amd64",
		PlatformKind: "x86_nvidia",
		Capabilities: map[string]any{"nvidia_gpu": true},
		Resources: map[string]any{
			"gpus": []map[string]any{{"index": int64(0), "name": "Test GPU"}},
		},
		Fingerprint:  map[string]any{"platform_kind": "x86_nvidia", "architecture": "amd64"},
		AgentVersion: "0.1.0-test",
	}
}

func testGatewayConfig() config.Config {
	return config.Config{
		PlatformURL:        "http://localhost",
		NodeName:           "edge-01",
		AgentVersion:       "0.1.0-test",
		AllowInsecureLocal: true,
	}
}

func websocketURL(httpURL string) string {
	return strings.Replace(httpURL, "http://", "ws://", 1) + "/agent/v1/connect"
}

func openGatewayStore(t *testing.T) *state.Store {
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

func runClient(ctx context.Context, client *Client) <-chan error {
	done := make(chan error, 1)
	go func() {
		done <- client.Run(ctx)
	}()
	return done
}

func waitClient(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(peerTimeout):
		t.Fatal("timed out waiting for Gateway client to stop")
		return nil
	}
}

func waitPendingEvents(t *testing.T, store *state.Store, want int) {
	t.Helper()
	waitFor(t, func() bool {
		pending, err := store.PendingEvents(100)
		return err == nil && len(pending) == want
	}, fmt.Sprintf("pending event count %d", want))
}

func waitFor(t *testing.T, condition func() bool, description string) {
	t.Helper()
	deadline := time.Now().Add(peerTimeout)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", description)
}

func waitSignalOrError(
	t *testing.T,
	ctx context.Context,
	signal <-chan struct{},
	serverErrors <-chan error,
	description string,
) {
	t.Helper()
	select {
	case <-signal:
	case err := <-serverErrors:
		t.Fatal(err)
	case <-ctx.Done():
		t.Fatalf("timed out waiting for %s", description)
	}
}

func waitBatch(t *testing.T, ctx context.Context, batches <-chan []byte, serverErrors <-chan error) []byte {
	t.Helper()
	select {
	case batch := <-batches:
		return batch
	case err := <-serverErrors:
		t.Fatal(err)
	case <-ctx.Done():
		t.Fatal("timed out waiting for event batch")
	}
	return nil
}

func reportServerError(errors chan<- error, err error) {
	select {
	case errors <- err:
	default:
	}
}

func assertNoServerError(t *testing.T, serverErrors <-chan error) {
	t.Helper()
	select {
	case err := <-serverErrors:
		t.Fatal(err)
	default:
	}
}

func zeroBackoff(int) time.Duration {
	return 0
}

func parsePeerCertificate(certificatePEM string) (*x509.Certificate, error) {
	block, rest := pem.Decode([]byte(certificatePEM))
	if block == nil || block.Type != "CERTIFICATE" || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("certificate is not one PEM block")
	}
	return x509.ParseCertificate(block.Bytes)
}

func testCertificateFingerprint(certificate *x509.Certificate) string {
	return fmt.Sprintf("%x", sha256.Sum256(certificate.Raw))
}

func parsePeerCSR(csrPEM string) (*x509.CertificateRequest, error) {
	block, rest := pem.Decode([]byte(csrPEM))
	if block == nil || block.Type != "CERTIFICATE REQUEST" || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("CSR is not one PEM block")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		return nil, err
	}
	if err := csr.CheckSignature(); err != nil {
		return nil, err
	}
	return csr, nil
}

func csrPEMForKey(t *testing.T, signer ed25519.PrivateKey, commonName string) string {
	t.Helper()
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: commonName},
	}, signer)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der}))
}

func selfSignedCertificate(
	t *testing.T,
	signer ed25519.PrivateKey,
	isCA bool,
	extendedKeyUsage []x509.ExtKeyUsage,
) string {
	t.Helper()
	now := time.Now()
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(999),
		Subject:               pkix.Name{CommonName: "node-1"},
		NotBefore:             now.Add(-time.Hour),
		NotAfter:              now.Add(365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           extendedKeyUsage,
		BasicConstraintsValid: true,
		IsCA:                  isCA,
	}
	if isCA {
		template.KeyUsage |= x509.KeyUsageCertSign
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, signer.Public(), signer)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func gatewayReaderGoroutines() int {
	buffer := make([]byte, 1<<20)
	n := runtime.Stack(buffer, true)
	return strings.Count(string(buffer[:n]), "gateway.readServerMessages")
}
