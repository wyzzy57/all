package state

import (
	"bytes"
	"crypto"
	"crypto/x509"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math"
	"os"
	"time"

	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"go.etcd.io/bbolt"
)

var (
	identityBucket = []byte("identity")
	eventsBucket   = []byte("events")
	metaBucket     = []byte("meta")

	currentIdentityKey    = []byte("current")
	nextEventSequenceKey  = []byte("next_event_sequence")
	ackedEventSequenceKey = []byte("acked_event_sequence")
)

type Identity struct {
	NodeID                   string
	PrivateKeyPEM            string
	CertificatePEM           string
	CACertificatePEM         string
	CertificateExpiresAt     time.Time
	GatewayURL               string
	HeartbeatIntervalSeconds int
}

type Store struct {
	db *bbolt.DB
}

func Open(path string) (*Store, error) {
	if path == "" {
		return nil, fmt.Errorf("state database path must not be empty")
	}
	db, err := bbolt.Open(path, 0o600, nil)
	if err != nil {
		return nil, fmt.Errorf("open state database: %w", err)
	}
	if err := os.Chmod(path, 0o600); err != nil {
		db.Close()
		return nil, fmt.Errorf("set state database permissions: %w", err)
	}
	if err := db.Update(func(tx *bbolt.Tx) error {
		for _, bucket := range [][]byte{identityBucket, eventsBucket, metaBucket} {
			if _, err := tx.CreateBucketIfNotExists(bucket); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		db.Close()
		return nil, fmt.Errorf("initialize state database: %w", err)
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error {
	return s.db.Close()
}

func (s *Store) Identity() (Identity, bool, error) {
	var identity Identity
	found := false
	err := s.db.View(func(tx *bbolt.Tx) error {
		stored := tx.Bucket(identityBucket).Get(currentIdentityKey)
		if stored == nil {
			return nil
		}
		if err := json.Unmarshal(stored, &identity); err != nil {
			return fmt.Errorf("decode identity: %w", err)
		}
		found = true
		return nil
	})
	return identity, found, err
}

func (s *Store) SaveIdentity(identity Identity) error {
	certificate, err := validateCertificate(identity.PrivateKeyPEM, identity.CertificatePEM, identity.CACertificatePEM)
	if err != nil {
		return err
	}
	identity.CertificateExpiresAt = certificate.NotAfter
	encoded, err := json.Marshal(identity)
	if err != nil {
		return fmt.Errorf("encode identity: %w", err)
	}
	return s.db.Update(func(tx *bbolt.Tx) error {
		return tx.Bucket(identityBucket).Put(currentIdentityKey, encoded)
	})
}

func (s *Store) UpdateCertificate(certificatePEM string, _ time.Time) error {
	return s.db.Update(func(tx *bbolt.Tx) error {
		bucket := tx.Bucket(identityBucket)
		stored := bucket.Get(currentIdentityKey)
		if stored == nil {
			return fmt.Errorf("identity is not initialized")
		}
		var identity Identity
		if err := json.Unmarshal(stored, &identity); err != nil {
			return fmt.Errorf("decode identity: %w", err)
		}
		certificate, err := validateCertificate(identity.PrivateKeyPEM, certificatePEM, identity.CACertificatePEM)
		if err != nil {
			return err
		}
		identity.CertificatePEM = certificatePEM
		identity.CertificateExpiresAt = certificate.NotAfter
		encoded, err := json.Marshal(identity)
		if err != nil {
			return fmt.Errorf("encode identity: %w", err)
		}
		return bucket.Put(currentIdentityKey, encoded)
	})
}

func (s *Store) AppendEvent(eventType string, payload map[string]any) (protocol.Event, error) {
	var event protocol.Event
	err := s.db.Update(func(tx *bbolt.Tx) error {
		meta := tx.Bucket(metaBucket)
		highest, err := readSequence(meta.Get(nextEventSequenceKey))
		if err != nil {
			return fmt.Errorf("read event sequence: %w", err)
		}
		if highest == math.MaxUint64 {
			return fmt.Errorf("event sequence exhausted")
		}
		sequence := highest + 1
		if payload == nil {
			payload = map[string]any{}
		}
		event = protocol.Event{
			Sequence:   sequence,
			EventType:  eventType,
			Payload:    payload,
			OccurredAt: time.Now().UTC(),
		}
		encoded, err := json.Marshal(event)
		if err != nil {
			return fmt.Errorf("encode event: %w", err)
		}
		if err := tx.Bucket(eventsBucket).Put(sequenceKey(sequence), encoded); err != nil {
			return err
		}
		return meta.Put(nextEventSequenceKey, sequenceKey(sequence))
	})
	return event, err
}

func (s *Store) PendingEvents(limit int) ([]protocol.Event, error) {
	if limit < 0 {
		return nil, fmt.Errorf("event limit must not be negative")
	}
	if limit == 0 {
		return []protocol.Event{}, nil
	}

	events := make([]protocol.Event, 0, limit)
	err := s.db.View(func(tx *bbolt.Tx) error {
		cursor := tx.Bucket(eventsBucket).Cursor()
		for key, value := cursor.First(); key != nil && len(events) < limit; key, value = cursor.Next() {
			sequence, err := readSequence(key)
			if err != nil {
				return fmt.Errorf("read event key: %w", err)
			}
			var event protocol.Event
			if err := json.Unmarshal(value, &event); err != nil {
				return fmt.Errorf("decode event %d: %w", sequence, err)
			}
			if event.Sequence != sequence {
				return fmt.Errorf("event sequence mismatch: key %d contains %d", sequence, event.Sequence)
			}
			events = append(events, event)
		}
		return nil
	})
	return events, err
}

func (s *Store) AckEvents(throughSequence uint64) error {
	return s.db.Update(func(tx *bbolt.Tx) error {
		meta := tx.Bucket(metaBucket)
		highest, err := readSequence(meta.Get(nextEventSequenceKey))
		if err != nil {
			return fmt.Errorf("read highest event sequence: %w", err)
		}
		acked, err := readSequence(meta.Get(ackedEventSequenceKey))
		if err != nil {
			return fmt.Errorf("read acknowledged event sequence: %w", err)
		}
		if throughSequence > highest {
			return fmt.Errorf("ACK sequence %d is ahead of highest local sequence %d", throughSequence, highest)
		}
		if throughSequence < acked {
			return fmt.Errorf("ACK sequence %d is behind acknowledged sequence %d", throughSequence, acked)
		}

		cursor := tx.Bucket(eventsBucket).Cursor()
		for key, _ := cursor.First(); key != nil; key, _ = cursor.Next() {
			sequence, err := readSequence(key)
			if err != nil {
				return fmt.Errorf("read event key: %w", err)
			}
			if sequence > throughSequence {
				break
			}
			if err := cursor.Delete(); err != nil {
				return err
			}
		}
		return meta.Put(ackedEventSequenceKey, sequenceKey(throughSequence))
	})
}

func validateCertificate(privateKeyPEM, certificatePEM, caCertificatePEM string) (*x509.Certificate, error) {
	privateBlock, _ := pem.Decode([]byte(privateKeyPEM))
	if privateBlock == nil {
		return nil, fmt.Errorf("private key is not valid PEM")
	}
	privateKey, err := x509.ParsePKCS8PrivateKey(privateBlock.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse PKCS#8 private key: %w", err)
	}
	signer, ok := privateKey.(crypto.Signer)
	if !ok {
		return nil, fmt.Errorf("private key does not expose a public key")
	}

	certificateBlock, _ := pem.Decode([]byte(certificatePEM))
	if certificateBlock == nil {
		return nil, fmt.Errorf("certificate is not valid PEM")
	}
	certificate, err := x509.ParseCertificate(certificateBlock.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse certificate: %w", err)
	}
	privatePublicKey, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil {
		return nil, fmt.Errorf("marshal private-key public key: %w", err)
	}
	certificatePublicKey, err := x509.MarshalPKIXPublicKey(certificate.PublicKey)
	if err != nil {
		return nil, fmt.Errorf("marshal certificate public key: %w", err)
	}
	if !bytes.Equal(privatePublicKey, certificatePublicKey) {
		return nil, fmt.Errorf("certificate public key does not match private key")
	}

	roots := x509.NewCertPool()
	if ok := roots.AppendCertsFromPEM([]byte(caCertificatePEM)); !ok {
		return nil, fmt.Errorf("CA certificate is not valid PEM")
	}
	if _, err := certificate.Verify(x509.VerifyOptions{
		Roots:     roots,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}); err != nil {
		return nil, fmt.Errorf("verify client certificate: %w", err)
	}
	return certificate, nil
}

func sequenceKey(sequence uint64) []byte {
	key := make([]byte, 8)
	binary.BigEndian.PutUint64(key, sequence)
	return key
}

func readSequence(value []byte) (uint64, error) {
	if value == nil {
		return 0, nil
	}
	if len(value) != 8 {
		return 0, fmt.Errorf("invalid uint64 length %d", len(value))
	}
	return binary.BigEndian.Uint64(value), nil
}
