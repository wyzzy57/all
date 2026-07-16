package gateway

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
	"net/http"
	"net/http/httptest"
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
		if err := peerWriteJSON(conn, protocol.CertificateRenewedMessage{
			Envelope:       testEnvelope("certificate_renewed"),
			CertificatePEM: renewedPEM,
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
				if err := peerWriteJSON(conn, protocol.CertificateRenewedMessage{
					Envelope:       testEnvelope("certificate_renewed"),
					CertificatePEM: returnedCertificate,
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
			if err == nil || !strings.Contains(err.Error(), "advance") {
				t.Errorf("error = %v, want expiry-advance rejection", err)
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
	store := openGatewayStore(t)
	observedBatches := make(chan []byte, 2)
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
		observedBatches <- append([]byte(nil), batch...)
		if connectionNumber == 1 {
			_ = conn.Close(websocket.StatusInternalError, "retry")
			return
		}
		if connectionNumber != 2 {
			reportServerError(serverErrors, fmt.Errorf("unexpected connection %d", connectionNumber))
			return
		}
		<-allowAck
		if err := peerWriteJSON(conn, protocol.EventsAckMessage{
			Envelope:        testEnvelope("events_acked"),
			ThroughSequence: 1,
		}); err != nil {
			reportServerError(serverErrors, err)
			return
		}
		ackSent <- struct{}{}
		<-releaseSecond
	}))
	defer server.Close()

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
	runDone := runClient(ctx, New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)))

	first := waitBatch(t, ctx, observedBatches, serverErrors)
	waitPendingEvents(t, store, 1)
	second := waitBatch(t, ctx, observedBatches, serverErrors)
	if !bytes.Equal(first, second) {
		cancel()
		close(releaseSecond)
		t.Fatalf("replayed event batch changed\nfirst:  %s\nsecond: %s", first, second)
	}
	waitPendingEvents(t, store, 1)
	close(allowAck)
	waitSignalOrError(t, ctx, ackSent, serverErrors, "event ACK")
	waitPendingEvents(t, store, 0)
	if got := authentications.Load(); got != 2 {
		cancel()
		close(releaseSecond)
		t.Fatalf("authentications = %d, want 2", got)
	}
	cancel()
	close(releaseSecond)
	if err := waitClient(t, runDone); err != nil {
		t.Fatalf("Run returned an error after cancellation: %v", err)
	}
	assertNoServerError(t, serverErrors)
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

func TestClientRejectsProtocolMismatch(t *testing.T) {
	tests := []protocol.ChallengeMessage{
		{Envelope: protocol.Envelope{ProtocolVersion: 2, Type: "challenge"}, Nonce: base64.StdEncoding.EncodeToString([]byte("nonce"))},
		{Envelope: protocol.Envelope{ProtocolVersion: 1, Type: "authenticated"}, Nonce: base64.StdEncoding.EncodeToString([]byte("nonce"))},
	}
	for _, challenge := range tests {
		t.Run(fmt.Sprintf("version_%d_type_%s", challenge.ProtocolVersion, challenge.Type), func(t *testing.T) {
			store := openGatewayStore(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				conn, err := websocket.Accept(w, r, nil)
				if err != nil {
					return
				}
				defer conn.CloseNow()
				_ = peerWriteJSON(conn, challenge)
				_, _, _ = peerRead(conn)
			}))
			defer server.Close()
			testsupport.NewStoredIdentity(t, store, "node-1", websocketURL(server.URL), time.Now().Add(365*24*time.Hour))

			ctx, cancel := context.WithTimeout(context.Background(), peerTimeout)
			defer cancel()
			err := New(testGatewayConfig(), store, testInventory(), WithBackoff(zeroBackoff)).Run(ctx)
			if err == nil || (!strings.Contains(err.Error(), "protocol version") && !strings.Contains(err.Error(), "message type")) {
				t.Fatalf("error = %v, want strict protocol rejection", err)
			}
		})
	}
}

func TestClientRejectsHeartbeatOutsideBoundsAndOverflow(t *testing.T) {
	tests := []struct {
		name      string
		heartbeat int
		raw       string
	}{
		{name: "below minimum", heartbeat: 4},
		{name: "above maximum", heartbeat: 301},
		{name: "numeric overflow", raw: `{"protocol_version":1,"type":"authenticated","heartbeat_interval_seconds":9223372036854775808}`},
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
			if err == nil || (!strings.Contains(err.Error(), "heartbeat") && !strings.Contains(err.Error(), "decode")) {
				t.Fatalf("error = %v, want heartbeat rejection", err)
			}
			assertNoServerError(t, serverErrors)
		})
	}
}

func TestClientRejectsAckAheadOfHighestSent(t *testing.T) {
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
			ThroughSequence: 2,
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
	if err == nil || !strings.Contains(err.Error(), "ahead of highest sent") {
		t.Fatalf("error = %v, want ACK-ahead rejection", err)
	}
	waitPendingEvents(t, store, 1)
	assertNoServerError(t, serverErrors)
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

func TestClientRejectsInvalidRenewedCertificates(t *testing.T) {
	tests := []struct {
		name        string
		want        string
		certificate func(*testing.T, ed25519.PrivateKey, *testsupport.CA) string
	}{
		{
			name: "private key mismatch",
			want: "public key",
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
			want: "certificate chain",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return testsupport.NewCA(t).SignCSR(t, csrPEMForKey(t, signer, "node-1"), "node-1", time.Now().Add(365*24*time.Hour))
			},
		},
		{
			name: "CA instead of end entity",
			want: "end-entity",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return selfSignedCertificate(t, signer, true, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "missing client auth",
			want: "client authentication",
			certificate: func(t *testing.T, signer ed25519.PrivateKey, _ *testsupport.CA) string {
				return selfSignedCertificate(t, signer, false, []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth})
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
				if err := peerWriteJSON(conn, protocol.CertificateRenewedMessage{
					Envelope:       testEnvelope("certificate_renewed"),
					CertificatePEM: returnedCertificate,
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
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error = %v, want %q", err, test.want)
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
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
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

func TestAppendEnrolledCAKeepsSystemRoots(t *testing.T) {
	systemCA := testsupport.NewCA(t)
	enrolledCA := testsupport.NewCA(t)
	systemRoots := x509.NewCertPool()
	if !systemRoots.AppendCertsFromPEM([]byte(systemCA.PEM())) {
		t.Fatal("append system fixture CA")
	}

	combined, err := appendEnrolledCA(systemRoots, enrolledCA.PEM())
	if err != nil {
		t.Fatal(err)
	}
	if len(combined.Subjects()) != 2 {
		t.Fatalf("combined roots contain %d subjects, want 2", len(combined.Subjects()))
	}
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
