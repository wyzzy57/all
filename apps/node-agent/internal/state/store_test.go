package state

import (
	"crypto"
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
	identity, _, _ := newTestIdentity(t, "node-1", time.Now().UTC().Add(time.Hour))

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

func TestStoreRetainsPendingRenewalAcrossRestartUntilPromotion(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.db")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	activeExpiry := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	identity, privateKey, ca := newTestIdentity(t, "node-1", activeExpiry)
	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}
	pending := PendingRenewal{
		RequestID: "renewal-restart-001",
		CSRPEM:    csrPEMForPrivateKey(t, privateKey, identity.NodeID),
	}
	if err := store.SavePendingRenewal(pending); err != nil {
		t.Fatal(err)
	}
	candidatePEM := ca.signCertificate(
		t,
		privateKey.Public(),
		identity.NodeID,
		activeExpiry.Add(time.Hour),
		[]x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	)
	candidate, err := parseCertificate(candidatePEM)
	if err != nil {
		t.Fatal(err)
	}
	fingerprint := certificateFingerprint(candidate)
	if err := store.SaveRenewalCandidate(pending.RequestID, candidatePEM, fingerprint); err != nil {
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
	storedPending, found, err := reopened.PendingRenewal()
	if err != nil || !found || storedPending.CertificatePEM != candidatePEM ||
		storedPending.CertificateFingerprintSHA256 != fingerprint {
		t.Fatalf("pending renewal was not retained across restart: found=%v pending=%#v err=%v", found, storedPending, err)
	}
	before, found, err := reopened.Identity()
	if err != nil || !found || before.CertificatePEM != identity.CertificatePEM {
		t.Fatalf("active identity changed before activation: found=%v identity=%#v err=%v", found, before, err)
	}
	if err := reopened.PromotePendingRenewal(pending.RequestID, fingerprint); err != nil {
		t.Fatal(err)
	}
	after, found, err := reopened.Identity()
	if err != nil || !found || after.CertificatePEM != candidatePEM {
		t.Fatalf("pending renewal was not promoted: found=%v identity=%#v err=%v", found, after, err)
	}
	if _, found, err := reopened.PendingRenewal(); err != nil || found {
		t.Fatalf("pending renewal remained after promotion: found=%v err=%v", found, err)
	}
}

func TestStoreRejectsIdentityWithMismatchedCertificate(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	identity, _, _ := newTestIdentity(t, "node-1", time.Now().UTC().Add(time.Hour))
	other, _, _ := newTestIdentity(t, "node-2", time.Now().UTC().Add(time.Hour))
	identity.CertificatePEM = other.CertificatePEM

	if err := store.SaveIdentity(identity); err == nil {
		t.Fatal("expected mismatched certificate to be rejected")
	}
	if _, ok, err := store.Identity(); err != nil || ok {
		t.Fatalf("invalid identity was persisted: ok=%v err=%v", ok, err)
	}
}

func TestStoreRejectsUntrustedMatchingKeyIdentityCertificates(t *testing.T) {
	expiresAt := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	tests := []struct {
		name        string
		certificate func(*testing.T, ed25519.PrivateKey, *testCA) string
	}{
		{
			name: "self-signed",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, _ *testCA) string {
				return newSelfSignedCertificatePEM(t, privateKey, "node-1", expiresAt, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "unrelated CA",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, _ *testCA) string {
				return newTestCA(t).signCertificate(t, privateKey.Public(), "node-1", expiresAt, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "without client auth",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, ca *testCA) string {
				return ca.signCertificate(t, privateKey.Public(), "node-1", expiresAt, []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth})
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			identity, privateKey, ca := newTestIdentity(t, "node-1", expiresAt)
			identity.CertificatePEM = test.certificate(t, privateKey, ca)

			if err := store.SaveIdentity(identity); err == nil {
				t.Fatal("expected untrusted certificate to be rejected")
			}
			if _, ok, err := store.Identity(); err != nil || ok {
				t.Fatalf("invalid identity was persisted: ok=%v err=%v", ok, err)
			}
		})
	}
}

func TestStoreDerivesSavedCertificateExpiryFromLeaf(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	expiresAt := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	identity, _, _ := newTestIdentity(t, "node-1", expiresAt)
	identity.CertificateExpiresAt = expiresAt.Add(24 * time.Hour)

	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}
	got, ok, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if !ok || !got.CertificateExpiresAt.Equal(expiresAt) {
		t.Fatalf("stored expiry = %s, want leaf NotAfter %s", got.CertificateExpiresAt, expiresAt)
	}
}

func TestStoreUpdatesOnlyMatchingCertificate(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	expiresAt := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	identity, privateKey, ca := newTestIdentity(t, "node-1", expiresAt)
	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}

	renewedExpiry := expiresAt.Add(time.Hour)
	renewedPEM := ca.signCertificate(t, privateKey.Public(), "node-1", renewedExpiry, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
	if err := store.UpdateCertificate(renewedPEM, renewedExpiry); err != nil {
		t.Fatal(err)
	}
	other, _, _ := newTestIdentity(t, "node-2", renewedExpiry)
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

func TestStoreRejectsUntrustedMatchingKeyCertificateUpdates(t *testing.T) {
	expiresAt := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	renewedExpiry := expiresAt.Add(time.Hour)
	tests := []struct {
		name        string
		certificate func(*testing.T, ed25519.PrivateKey, *testCA) string
	}{
		{
			name: "self-signed",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, _ *testCA) string {
				return newSelfSignedCertificatePEM(t, privateKey, "node-1", renewedExpiry, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "unrelated CA",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, _ *testCA) string {
				return newTestCA(t).signCertificate(t, privateKey.Public(), "node-1", renewedExpiry, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
			},
		},
		{
			name: "without client auth",
			certificate: func(t *testing.T, privateKey ed25519.PrivateKey, ca *testCA) string {
				return ca.signCertificate(t, privateKey.Public(), "node-1", renewedExpiry, []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth})
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			identity, privateKey, ca := newTestIdentity(t, "node-1", expiresAt)
			if err := store.SaveIdentity(identity); err != nil {
				t.Fatal(err)
			}
			before, ok, err := store.Identity()
			if err != nil || !ok {
				t.Fatalf("load original identity: ok=%v err=%v", ok, err)
			}

			certificatePEM := test.certificate(t, privateKey, ca)
			if err := store.UpdateCertificate(certificatePEM, renewedExpiry.Add(24*time.Hour)); err == nil {
				t.Fatal("expected untrusted certificate update to be rejected")
			}
			after, ok, err := store.Identity()
			if err != nil || !ok {
				t.Fatalf("load identity after rejected update: ok=%v err=%v", ok, err)
			}
			if after.CertificatePEM != before.CertificatePEM || !after.CertificateExpiresAt.Equal(before.CertificateExpiresAt) {
				t.Fatalf("rejected update changed certificate state: before=%#v after=%#v", before, after)
			}
		})
	}
}

func TestStoreDerivesUpdatedCertificateExpiryFromLeaf(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	expiresAt := time.Now().UTC().Add(time.Hour).Truncate(time.Second)
	identity, privateKey, ca := newTestIdentity(t, "node-1", expiresAt)
	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}
	renewedExpiry := expiresAt.Add(time.Hour)
	renewedPEM := ca.signCertificate(t, privateKey.Public(), "node-1", renewedExpiry, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})

	if err := store.UpdateCertificate(renewedPEM, renewedExpiry.Add(24*time.Hour)); err != nil {
		t.Fatal(err)
	}
	got, ok, err := store.Identity()
	if err != nil {
		t.Fatal(err)
	}
	if !ok || got.CertificatePEM != renewedPEM || !got.CertificateExpiresAt.Equal(renewedExpiry) {
		t.Fatalf("unexpected renewed identity: %#v", got)
	}
}

type testCA struct {
	certificate    *x509.Certificate
	privateKey     ed25519.PrivateKey
	certificatePEM string
}

func newTestIdentity(t *testing.T, nodeID string, expiresAt time.Time) (Identity, ed25519.PrivateKey, *testCA) {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	ca := newTestCA(t)
	certificatePEM := ca.signCertificate(t, privateKey.Public(), nodeID, expiresAt, []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth})
	return Identity{
		NodeID:                   nodeID,
		PrivateKeyPEM:            string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER})),
		CertificatePEM:           certificatePEM,
		CACertificatePEM:         ca.certificatePEM,
		CertificateExpiresAt:     expiresAt,
		GatewayURL:               "wss://platform.example.com/agent/v1/ws",
		HeartbeatIntervalSeconds: 30,
	}, privateKey, ca
}

func csrPEMForPrivateKey(t *testing.T, privateKey ed25519.PrivateKey, nodeID string) string {
	t.Helper()
	csrDER, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: nodeID},
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csrDER}))
}

func parseCertificate(certificatePEM string) (*x509.Certificate, error) {
	block, _ := pem.Decode([]byte(certificatePEM))
	if block == nil || block.Type != "CERTIFICATE" {
		return nil, os.ErrInvalid
	}
	return x509.ParseCertificate(block.Bytes)
}

func newTestCA(t *testing.T) *testCA {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Visiox State Test CA"},
		NotBefore:             now.Add(-time.Hour),
		NotAfter:              now.Add(365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := x509.ParseCertificate(certificateDER)
	if err != nil {
		t.Fatal(err)
	}
	return &testCA{
		certificate:    certificate,
		privateKey:     privateKey,
		certificatePEM: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})),
	}
}

func (ca *testCA) signCertificate(t *testing.T, publicKey crypto.PublicKey, nodeID string, expiresAt time.Time, usages []x509.ExtKeyUsage) string {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: nodeID},
		NotBefore:    time.Now().UTC().Add(-time.Hour),
		NotAfter:     expiresAt,
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  usages,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, ca.certificate, publicKey, ca.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}))
}

func newSelfSignedCertificatePEM(t *testing.T, privateKey ed25519.PrivateKey, nodeID string, expiresAt time.Time, usages []x509.ExtKeyUsage) string {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(3),
		Subject:      pkix.Name{CommonName: nodeID},
		NotBefore:    time.Now().UTC().Add(-time.Hour),
		NotAfter:     expiresAt,
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  usages,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}))
}
