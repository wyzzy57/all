package testsupport

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"testing"
	"time"

	"github.com/wyzzy57/all/apps/node-agent/internal/state"
)

type CA struct {
	certificate    *x509.Certificate
	privateKey     ed25519.PrivateKey
	certificatePEM string
}

func NewCA(t testing.TB) *CA {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Visiox Test CA"},
		NotBefore:             time.Date(2020, time.January, 1, 0, 0, 0, 0, time.UTC),
		NotAfter:              time.Date(2100, time.January, 1, 0, 0, 0, 0, time.UTC),
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
	return &CA{
		certificate:    certificate,
		privateKey:     privateKey,
		certificatePEM: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER})),
	}
}

func (c *CA) PEM() string {
	return c.certificatePEM
}

func (c *CA) ServerTLSConfig(t testing.TB, hostname string) *tls.Config {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(3),
		Subject:      pkix.Name{CommonName: hostname},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	if ipAddress := net.ParseIP(hostname); ipAddress != nil {
		template.IPAddresses = []net.IP{ipAddress}
	} else {
		template.DNSNames = []string{hostname}
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, c.certificate, publicKey, c.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	privateKeyDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	certificate, err := tls.X509KeyPair(
		pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}),
		pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateKeyDER}),
	)
	if err != nil {
		t.Fatal(err)
	}
	return &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
}

func (c *CA) SignCSR(t testing.TB, csrPEM, nodeID string, expiresAt time.Time) string {
	t.Helper()
	block, _ := pem.Decode([]byte(csrPEM))
	if block == nil {
		t.Fatal("CSR is not valid PEM")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if err := csr.CheckSignature(); err != nil {
		t.Fatalf("verify CSR signature: %v", err)
	}
	if !expiresAt.After(c.certificate.NotBefore) || expiresAt.After(c.certificate.NotAfter) {
		t.Fatalf("certificate expiry %s is outside test CA validity", expiresAt)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: nodeID},
		NotBefore:    c.certificate.NotBefore,
		NotAfter:     expiresAt,
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, c.certificate, csr.PublicKey, c.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificateDER}))
}

func NewStoredIdentity(
	t testing.TB,
	store *state.Store,
	nodeID string,
	gatewayURL string,
	expiresAt time.Time,
) (state.Identity, ed25519.PrivateKey, *CA) {
	t.Helper()
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	csrDER, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: nodeID},
	}, privateKey)
	if err != nil {
		t.Fatal(err)
	}
	csrPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csrDER}))
	ca := NewCA(t)
	certificatePEM := ca.SignCSR(t, csrPEM, nodeID, expiresAt)
	certificateBlock, _ := pem.Decode([]byte(certificatePEM))
	certificate, err := x509.ParseCertificate(certificateBlock.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	privateKeyDER, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	identity := state.Identity{
		NodeID:                   nodeID,
		PrivateKeyPEM:            string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateKeyDER})),
		CertificatePEM:           certificatePEM,
		CACertificatePEM:         ca.PEM(),
		CertificateExpiresAt:     certificate.NotAfter,
		GatewayURL:               gatewayURL,
		HeartbeatIntervalSeconds: 30,
	}
	if err := store.SaveIdentity(identity); err != nil {
		t.Fatal(err)
	}
	return identity, privateKey, ca
}
