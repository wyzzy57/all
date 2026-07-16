package state

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStoreSpoolsAndAcknowledgesEvents(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	first, err := store.AppendEvent("agent_started", map[string]any{"ok": true})
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.AppendEvent("inventory", map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	if first.Sequence != 1 || second.Sequence != 2 {
		t.Fatal("sequences must be monotonic")
	}
	if err := store.AckEvents(1); err != nil {
		t.Fatal(err)
	}
	pending, err := store.PendingEvents(100)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Sequence != 2 {
		t.Fatalf("unexpected pending events: %#v", pending)
	}
}

func TestStoreReopensWithUnacknowledgedEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AppendEvent("first", map[string]any{}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.AppendEvent("second", map[string]any{}); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	pending, err := reopened.PendingEvents(100)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 2 || pending[0].Sequence != 1 || pending[1].Sequence != 2 {
		t.Fatalf("unexpected reopened events: %#v", pending)
	}
	next, err := reopened.AppendEvent("third", map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	if next.Sequence != 3 {
		t.Fatalf("next sequence = %d, want 3", next.Sequence)
	}
}

func TestStoreRejectsAckAheadOfHighestSequence(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if _, err := store.AppendEvent("first", map[string]any{}); err != nil {
		t.Fatal(err)
	}

	if err := store.AckEvents(2); err == nil {
		t.Fatal("expected ACK ahead of highest sequence to fail")
	}
	pending, err := store.PendingEvents(100)
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Sequence != 1 {
		t.Fatalf("ahead ACK discarded unseen events: %#v", pending)
	}
}

func TestStoreRejectsAckRegression(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if _, err := store.AppendEvent("first", map[string]any{}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.AppendEvent("second", map[string]any{}); err != nil {
		t.Fatal(err)
	}
	if err := store.AckEvents(1); err != nil {
		t.Fatal(err)
	}
	if err := store.AckEvents(0); err == nil {
		t.Fatal("expected ACK cursor regression to fail")
	}
}

func TestStoreCreatesDatabaseWithPrivatePermissions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("database mode = %#o, want 0600", got)
	}
}

func TestStoreSavesAndLoadsMatchingIdentity(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	identity, _ := newTestIdentity(t, "node-1", time.Now().UTC().Add(time.Hour))

	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}
	got, ok, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("saved identity was not found")
	}
	if got.NodeID != identity.NodeID || got.PrivateKeyPEM != identity.PrivateKeyPEM || got.CertificatePEM != identity.CertificatePEM {
		t.Fatalf("identity mismatch: %#v", got)
	}
}

func TestStoreRejectsIdentityWithMismatchedCertificate(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	identity, _ := newTestIdentity(t, "node-1", time.Now().UTC().Add(time.Hour))
	other, _ := newTestIdentity(t, "node-2", time.Now().UTC().Add(time.Hour))
	identity.CertificatePEM = other.CertificatePEM

	if err := store.SaveIdentity(identity); err == nil {
		t.Fatal("expected mismatched certificate to be rejected")
	}
	if _, ok, err := store.Identity(); err != nil || ok {
		t.Fatalf("invalid identity was persisted: ok=%v err=%v", ok, err)
	}
}

func TestStoreUpdatesOnlyMatchingCertificate(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	expiresAt := time.Now().UTC().Add(time.Hour)
	identity, privateKey := newTestIdentity(t, "node-1", expiresAt)
	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}

	renewedExpiry := expiresAt.Add(time.Hour)
	renewedPEM := newCertificatePEM(t, privateKey, "node-1", renewedExpiry)
	if err := store.UpdateCertificate(renewedPEM, renewedExpiry); err != nil {
		t.Fatal(err)
	}
	other, _ := newTestIdentity(t, "node-2", renewedExpiry)
	if err := store.UpdateCertificate(other.CertificatePEM, renewedExpiry); err == nil {
		t.Fatal("expected mismatched renewed certificate to be rejected")
	}

	got, ok, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if !ok || got.CertificatePEM != renewedPEM || !got.CertificateExpiresAt.Equal(renewedExpiry) {
		t.Fatalf("unexpected renewed identity: %#v", got)
	}
}

func newTestIdentity(t *testing.T, nodeID string, expiresAt time.Time) (Identity, ed25519.PrivateKey) {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return Identity{
		NodeID:                   nodeID,
		PrivateKeyPEM:            string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER})),
		CertificatePEM:           newCertificatePEM(t, privateKey, nodeID, expiresAt),
		CACertificatePEM:         "test-ca",
		CertificateExpiresAt:     expiresAt,
		GatewayURL:               "wss://platform.example.com/agent/v1/ws",
		HeartbeatIntervalSeconds: 30,
	}, privateKey
}

func newCertificatePEM(t *testing.T, privateKey ed25519.PrivateKey, nodeID string, expiresAt time.Time) string {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: nodeID},
		NotBefore:    expiresAt.Add(-2 * time.Hour),
		NotAfter:     expiresAt,
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}))
}
