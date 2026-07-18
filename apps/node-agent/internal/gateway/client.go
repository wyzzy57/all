package gateway

import (
	"bytes"
	"context"
	"crypto/ed25519"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math"
	mathrand "math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/coder/websocket"
	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"github.com/wyzzy57/all/apps/node-agent/internal/state"
)

const (
	maximumMessageBytes      = 1 << 20
	maximumPendingEventBatch = 100
	minimumHeartbeatSeconds  = 5
	maximumHeartbeatSeconds  = 300
	certificateRenewalWindow = 30 * 24 * time.Hour
	authenticationTimeout    = 15 * time.Second
	pingInterval             = 30 * time.Second
	pongReadDeadline         = 10 * time.Second
)

var (
	errGatewayServerRejected            = errors.New("Gateway server rejected connection")
	errGatewayServerRequestedRetry      = errors.New("Gateway server requested retry")
	errGatewayAuthenticationRejected    = errors.New("Gateway authentication was rejected")
	errInvalidStoredCertificate         = errors.New("invalid stored certificate")
	errInvalidRenewedCertificate        = errors.New("invalid renewed certificate")
	errRenewalRecoveryRequired          = errors.New("expired pending renewal requires node re-enrollment")
	errInvalidHeartbeatInterval         = errors.New("heartbeat interval is outside allowed range")
	errEventACKAhead                    = errors.New("event ACK is ahead of highest sent sequence")
	errInvalidServerMessage             = errors.New("invalid server message")
	errUnexpectedServerMessageType      = errors.New("unexpected server message type")
	errUnsupportedServerProtocolVersion = errors.New("unsupported server protocol version")
)

type Option func(*Client)

type Client struct {
	cfg                   config.Config
	store                 *state.Store
	inventory             protocol.InventoryMessage
	backoff               func(attempt int) time.Duration
	authenticationTimeout time.Duration
	pingInterval          time.Duration
	pongReadDeadline      time.Duration
}

func New(cfg config.Config, store *state.Store, inventory protocol.InventoryMessage, options ...Option) *Client {
	inventory.Envelope = protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "inventory"}
	if cfg.AgentVersion != "" {
		inventory.AgentVersion = cfg.AgentVersion
	}
	if inventory.Capabilities == nil {
		inventory.Capabilities = map[string]any{}
	}
	if inventory.Resources == nil {
		inventory.Resources = map[string]any{}
	}
	if inventory.Fingerprint == nil {
		inventory.Fingerprint = map[string]any{}
	}
	client := &Client{
		cfg:                   cfg,
		store:                 store,
		inventory:             inventory,
		backoff:               jitteredExponentialBackoff,
		authenticationTimeout: authenticationTimeout,
		pingInterval:          pingInterval,
		pongReadDeadline:      pongReadDeadline,
	}
	for _, option := range options {
		if option != nil {
			option(client)
		}
	}
	return client
}

func WithBackoff(backoff func(attempt int) time.Duration) Option {
	return func(client *Client) {
		if backoff != nil {
			client.backoff = backoff
		}
	}
}

// WithConnectionDeadlines overrides connection liveness timings for tests and
// controlled deployments. Non-positive values retain the production defaults.
func WithConnectionDeadlines(
	authenticationTimeoutValue time.Duration,
	pingIntervalValue time.Duration,
	pongReadDeadlineValue time.Duration,
) Option {
	return func(client *Client) {
		if authenticationTimeoutValue > 0 {
			client.authenticationTimeout = authenticationTimeoutValue
		}
		if pingIntervalValue > 0 {
			client.pingInterval = pingIntervalValue
		}
		if pongReadDeadlineValue > 0 {
			client.pongReadDeadline = pongReadDeadlineValue
		}
	}
}

func (c *Client) Run(ctx context.Context) error {
	if c == nil {
		return fmt.Errorf("Gateway client must not be nil")
	}
	if c.store == nil {
		return fmt.Errorf("Gateway state store must not be nil")
	}
	if ctx == nil {
		return fmt.Errorf("Gateway context must not be nil")
	}

	attempt := 0
	for {
		if ctx.Err() != nil {
			return nil
		}
		stable, err := c.runConnection(ctx)
		if ctx.Err() != nil {
			return nil
		}
		var fatal *fatalError
		if errors.As(err, &fatal) {
			return fatal.err
		}
		if err == nil {
			attempt = 0
			continue
		}
		if stable {
			attempt = 0
		}
		delay := c.backoff(attempt)
		if delay < 0 {
			delay = 0
		}
		if attempt < math.MaxInt {
			attempt++
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
	}
}

func (c *Client) runConnection(ctx context.Context) (bool, error) {
	identity, found, err := c.store.Identity()
	if err != nil {
		return false, fatalErrorf("load stored identity: %v", err)
	}
	if !found {
		return false, fatalErrorf("stored identity is not initialized")
	}
	if identity.NodeID == "" {
		return false, fatalErrorf("stored identity node ID is empty")
	}
	signer, err := parseStoredPrivateKey(identity.PrivateKeyPEM)
	if err != nil {
		return false, fatal(err)
	}
	pending, pendingFound, err := c.store.PendingRenewal()
	if err != nil {
		return false, fatalErrorf("load pending renewal: %v", err)
	}
	now := time.Now()
	expiredPendingCandidate := pendingFound && pending.CertificatePEM != "" && !pending.CertificateExpiresAt.After(now)
	if expiredPendingCandidate {
		if err := c.store.ClearPendingRenewal(); err != nil {
			return false, fatal(fmt.Errorf("clear expired pending renewal: %w", err))
		}
		pending = state.PendingRenewal{}
		pendingFound = false
	}

	activeValid := validStoredCredential(identity, signer, now)
	candidateIdentity := identity
	candidateValid := false
	if pendingFound && pending.CertificatePEM != "" {
		candidateIdentity.CertificatePEM = pending.CertificatePEM
		candidateIdentity.CertificateExpiresAt = pending.CertificateExpiresAt
		if validStoredCredential(candidateIdentity, signer, now) != nil {
			return false, fatal(errInvalidRenewedCertificate)
		}
		candidateValid = true
	}

	if activeValid == nil {
		if !candidateValid {
			return c.runAuthenticatedConnection(ctx, identity, pending, pendingFound, false)
		}
		stable, err := c.runAuthenticatedConnection(ctx, identity, pending, true, false)
		if !errors.Is(err, errGatewayAuthenticationRejected) {
			return stable, err
		}
		stable, err = c.runAuthenticatedConnection(ctx, candidateIdentity, pending, true, true)
		if errors.Is(err, errGatewayAuthenticationRejected) {
			return stable, fatal(err)
		}
		return stable, err
	}
	if candidateValid {
		stable, err := c.runAuthenticatedConnection(ctx, candidateIdentity, pending, true, true)
		if errors.Is(err, errGatewayAuthenticationRejected) {
			return stable, fatal(err)
		}
		return stable, err
	}
	if expiredPendingCandidate {
		return false, fatal(errRenewalRecoveryRequired)
	}
	return false, fatal(errInvalidStoredCertificate)
}

func validStoredCredential(
	identity state.Identity,
	signer ed25519.PrivateKey,
	now time.Time,
) error {
	certificate, err := validateDeviceCertificate(
		identity.CertificatePEM,
		identity.CACertificatePEM,
		identity.NodeID,
		signer.Public().(ed25519.PublicKey),
		now,
	)
	if err != nil || !identity.CertificateExpiresAt.Equal(certificate.NotAfter) {
		return errInvalidStoredCertificate
	}
	return nil
}

func (c *Client) runAuthenticatedConnection(
	ctx context.Context,
	identity state.Identity,
	pending state.PendingRenewal,
	pendingFound bool,
	promotePendingAfterAuthentication bool,
) (bool, error) {
	signer, err := parseStoredPrivateKey(identity.PrivateKeyPEM)
	if err != nil {
		return false, fatal(err)
	}
	certificate, err := validateDeviceCertificate(
		identity.CertificatePEM,
		identity.CACertificatePEM,
		identity.NodeID,
		signer.Public().(ed25519.PublicKey),
		time.Now(),
	)
	if err != nil {
		return false, fatal(errInvalidStoredCertificate)
	}
	if !identity.CertificateExpiresAt.Equal(certificate.NotAfter) {
		return false, fatal(errInvalidStoredCertificate)
	}

	httpClient, transport, err := c.gatewayHTTPClient(identity)
	if err != nil {
		return false, fatal(err)
	}
	defer transport.CloseIdleConnections()
	conn, _, err := websocket.Dial(ctx, identity.GatewayURL, &websocket.DialOptions{HTTPClient: httpClient})
	if err != nil {
		return false, fmt.Errorf("Gateway connection failed")
	}
	defer conn.CloseNow()
	conn.SetReadLimit(maximumMessageBytes)

	authenticationCtx, cancelAuthentication := context.WithTimeout(ctx, c.authenticationTimeout)
	heartbeatSeconds, err := authenticate(authenticationCtx, conn, identity, signer)
	cancelAuthentication()
	if err != nil {
		return false, err
	}
	if promotePendingAfterAuthentication {
		if err := c.store.PromotePendingRenewal(pending.RequestID, pending.CertificateFingerprintSHA256); err != nil {
			return false, fatal(fmt.Errorf("promote pending renewal: %w", err))
		}
		return c.runLiveConnection(ctx, conn, time.Duration(heartbeatSeconds)*time.Second)
	}
	if pendingFound || !identity.CertificateExpiresAt.After(time.Now().Add(certificateRenewalWindow)) {
		if !pendingFound {
			pending, err = newPendingRenewal(identity, signer)
			if err != nil {
				return false, err
			}
			if err := c.store.SavePendingRenewal(pending); err != nil {
				return false, fatal(fmt.Errorf("persist renewal request: %w", err))
			}
		}
		if pending.CertificatePEM == "" {
			candidate, err := requestRenewalCandidate(ctx, conn, identity, signer, pending)
			if err != nil {
				return false, err
			}
			if err := c.store.SaveRenewalCandidate(
				pending.RequestID,
				candidate.CertificatePEM,
				candidate.CertificateFingerprintSHA256,
			); err != nil {
				return false, fatal(fmt.Errorf("persist renewal candidate: %w", err))
			}
			pending.CertificatePEM = candidate.CertificatePEM
			pending.CertificateFingerprintSHA256 = candidate.CertificateFingerprintSHA256
			pending.CertificateExpiresAt = candidate.CertificateExpiresAt
		}
		if err := acknowledgeRenewalCandidate(ctx, conn, pending); err != nil {
			return false, err
		}
		if err := c.store.PromotePendingRenewal(pending.RequestID, pending.CertificateFingerprintSHA256); err != nil {
			return false, fatal(fmt.Errorf("promote pending renewal: %w", err))
		}
	}

	return c.runLiveConnection(ctx, conn, time.Duration(heartbeatSeconds)*time.Second)
}

func (c *Client) gatewayHTTPClient(identity state.Identity) (*http.Client, *http.Transport, error) {
	parsed, err := url.Parse(identity.GatewayURL)
	if err != nil {
		return nil, nil, fmt.Errorf("stored Gateway URL is invalid")
	}
	hostname := parsed.Hostname()
	if parsed.Host == "" || hostname == "" || parsed.User != nil {
		return nil, nil, fmt.Errorf("stored Gateway URL is invalid")
	}
	switch strings.ToLower(parsed.Scheme) {
	case "wss":
	case "ws":
		if !c.cfg.AllowInsecureLocal || !isLocalDevelopmentGatewayHost(hostname) {
			return nil, nil, fmt.Errorf("stored Gateway URL requires insecure local mode")
		}
	default:
		return nil, nil, fmt.Errorf("stored Gateway URL has an invalid scheme")
	}

	transport, err := c.cfg.ServerHTTPTransport()
	if err != nil {
		return nil, nil, fmt.Errorf("configure Gateway TLS client: %w", err)
	}
	client := &http.Client{
		Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return client, transport, nil
}

func isLocalDevelopmentGatewayHost(hostname string) bool {
	switch strings.ToLower(hostname) {
	case "localhost", "127.0.0.1", "api-service", "host.docker.internal":
		return true
	default:
		return false
	}
}

func authenticate(
	ctx context.Context,
	conn *websocket.Conn,
	identity state.Identity,
	signer ed25519.PrivateKey,
) (int, error) {
	rawChallenge, err := readTextMessage(ctx, conn)
	if err != nil {
		return 0, err
	}
	var challenge protocol.ChallengeMessage
	if err := decodeStrictMessage(rawChallenge, "challenge", &challenge); err != nil {
		return 0, fatal(err)
	}
	if err := requireEnvelope(challenge.Envelope, "challenge"); err != nil {
		return 0, fatal(err)
	}
	nonce, err := base64.StdEncoding.Strict().DecodeString(challenge.Nonce)
	if err != nil || len(nonce) == 0 {
		return 0, fatalErrorf("challenge nonce is not valid base64")
	}
	signature := ed25519.Sign(signer, nonce)
	if err := writeJSONMessage(ctx, conn, protocol.AuthenticateMessage{
		Envelope:       protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "authenticate"},
		NodeID:         identity.NodeID,
		CertificatePEM: identity.CertificatePEM,
		Signature:      base64.StdEncoding.EncodeToString(signature),
	}); err != nil {
		return 0, err
	}

	rawAuthenticated, err := readTextMessage(ctx, conn)
	if err != nil {
		return 0, err
	}
	var authenticated protocol.AuthenticatedMessage
	if err := decodeStrictMessage(rawAuthenticated, "authenticated", &authenticated); err != nil {
		return 0, fatal(err)
	}
	if err := requireEnvelope(authenticated.Envelope, "authenticated"); err != nil {
		return 0, fatal(err)
	}
	if authenticated.HeartbeatIntervalSeconds < minimumHeartbeatSeconds ||
		authenticated.HeartbeatIntervalSeconds > maximumHeartbeatSeconds {
		return 0, fatal(errInvalidHeartbeatInterval)
	}
	return authenticated.HeartbeatIntervalSeconds, nil
}

func newPendingRenewal(identity state.Identity, signer ed25519.PrivateKey) (state.PendingRenewal, error) {
	csrDER, err := x509.CreateCertificateRequest(cryptorand.Reader, &x509.CertificateRequest{
		Subject: pkix.Name{CommonName: identity.NodeID},
	}, signer)
	if err != nil {
		return state.PendingRenewal{}, fatal(fmt.Errorf("create certificate renewal request: %w", err))
	}
	requestID, err := renewalRequestID()
	if err != nil {
		return state.PendingRenewal{}, err
	}
	return state.PendingRenewal{
		RequestID: requestID,
		CSRPEM:    string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csrDER})),
	}, nil
}

func renewalRequestID() (string, error) {
	value := make([]byte, 16)
	if _, err := cryptorand.Read(value); err != nil {
		return "", fatal(fmt.Errorf("generate renewal request ID: %w", err))
	}
	return hex.EncodeToString(value), nil
}

func requestRenewalCandidate(
	ctx context.Context,
	conn *websocket.Conn,
	identity state.Identity,
	signer ed25519.PrivateKey,
	pending state.PendingRenewal,
) (state.PendingRenewal, error) {
	if err := writeJSONMessage(ctx, conn, protocol.CertificateRenewalRequest{
		Envelope:         protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "certificate_renewal_request"},
		RenewalRequestID: pending.RequestID,
		CSRPEM:           pending.CSRPEM,
	}); err != nil {
		return state.PendingRenewal{}, err
	}

	rawCandidate, err := readTextMessage(ctx, conn)
	if err != nil {
		return state.PendingRenewal{}, err
	}
	var candidate protocol.CertificateRenewalCandidateMessage
	if err := decodeStrictMessage(rawCandidate, "certificate_renewal_candidate", &candidate); err != nil {
		return state.PendingRenewal{}, fatal(err)
	}
	if err := requireEnvelope(candidate.Envelope, "certificate_renewal_candidate"); err != nil {
		return state.PendingRenewal{}, fatal(err)
	}
	if candidate.RenewalRequestID != pending.RequestID || candidate.CertificateFingerprintSHA256 == "" {
		return state.PendingRenewal{}, fatal(errInvalidRenewedCertificate)
	}
	certificate, err := validateDeviceCertificate(
		candidate.CertificatePEM,
		identity.CACertificatePEM,
		identity.NodeID,
		signer.Public().(ed25519.PublicKey),
		time.Now(),
	)
	if err != nil {
		return state.PendingRenewal{}, fatal(errInvalidRenewedCertificate)
	}
	if !certificate.NotAfter.After(identity.CertificateExpiresAt) || certificateFingerprint(certificate) != candidate.CertificateFingerprintSHA256 {
		return state.PendingRenewal{}, fatal(errInvalidRenewedCertificate)
	}
	pending.CertificatePEM = candidate.CertificatePEM
	pending.CertificateFingerprintSHA256 = candidate.CertificateFingerprintSHA256
	pending.CertificateExpiresAt = certificate.NotAfter
	return pending, nil
}

func acknowledgeRenewalCandidate(ctx context.Context, conn *websocket.Conn, pending state.PendingRenewal) error {
	if err := writeJSONMessage(ctx, conn, protocol.CertificateRenewalAckMessage{
		Envelope:                     protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "certificate_renewal_ack"},
		RenewalRequestID:             pending.RequestID,
		CertificateFingerprintSHA256: pending.CertificateFingerprintSHA256,
	}); err != nil {
		return err
	}
	rawActivated, err := readTextMessage(ctx, conn)
	if err != nil {
		return err
	}
	var activated protocol.CertificateRenewalActivatedMessage
	if err := decodeStrictMessage(rawActivated, "certificate_renewal_activated", &activated); err != nil {
		return fatal(err)
	}
	if err := requireEnvelope(activated.Envelope, "certificate_renewal_activated"); err != nil {
		return fatal(err)
	}
	if activated.RenewalRequestID != pending.RequestID || activated.CertificateFingerprintSHA256 != pending.CertificateFingerprintSHA256 {
		return fatal(errInvalidRenewedCertificate)
	}
	return nil
}

func (c *Client) runLiveConnection(
	ctx context.Context,
	conn *websocket.Conn,
	heartbeatInterval time.Duration,
) (bool, error) {
	readerCtx, cancelReader := context.WithCancel(ctx)
	readerResults := make(chan serverResult, 1)
	readerDone := make(chan struct{})
	go readServerMessages(readerCtx, conn, readerResults, readerDone)
	defer func() {
		cancelReader()
		_ = conn.CloseNow()
		<-readerDone
	}()

	stable := false
	highestSent := uint64(0)
	if err := writeJSONMessage(ctx, conn, c.inventory); err != nil {
		return stable, err
	}
	if err := writeHeartbeat(ctx, conn); err != nil {
		return stable, err
	}
	if err := c.writePendingEvents(ctx, conn, &highestSent); err != nil {
		return stable, err
	}

	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	pingTicker := time.NewTicker(c.pingInterval)
	defer pingTicker.Stop()
	for {
		select {
		case <-ctx.Done():
			return stable, ctx.Err()
		case <-ticker.C:
			if err := writeHeartbeat(ctx, conn); err != nil {
				return stable, err
			}
			if err := c.writePendingEvents(ctx, conn, &highestSent); err != nil {
				return stable, err
			}
			stable = true
		case <-pingTicker.C:
			// Ping waits for a Pong while readServerMessages keeps consuming control frames.
			// Its deadline therefore bounds a half-open Gateway read without imposing an
			// application-message deadline on an otherwise healthy idle connection.
			pongCtx, cancelPong := context.WithTimeout(ctx, c.pongReadDeadline)
			err := conn.Ping(pongCtx)
			cancelPong()
			if err != nil {
				return stable, fmt.Errorf("Gateway pong read deadline exceeded: %w", err)
			}
			stable = true
		case result := <-readerResults:
			if result.err != nil {
				return stable, result.err
			}
			if result.ack != nil {
				if result.ack.ThroughSequence > highestSent {
					return stable, fatal(errEventACKAhead)
				}
				if err := c.store.AckEvents(result.ack.ThroughSequence); err != nil {
					return stable, fatal(fmt.Errorf("persist event ACK: %w", err))
				}
				if result.ack.ThroughSequence > 0 {
					stable = true
				}
				continue
			}
			if result.serverError.Retryable {
				return stable, errGatewayServerRequestedRetry
			}
			return stable, fatal(errGatewayServerRejected)
		}
	}
}

func (c *Client) writePendingEvents(
	ctx context.Context,
	conn *websocket.Conn,
	highestSent *uint64,
) error {
	events, err := c.store.PendingEvents(maximumPendingEventBatch)
	if err != nil {
		return fatal(fmt.Errorf("load pending events: %w", err))
	}
	if len(events) == 0 {
		return nil
	}
	previous := uint64(0)
	for _, event := range events {
		if event.Sequence == 0 || (previous != 0 && event.Sequence <= previous) {
			return fatalErrorf("pending events are not strictly ordered")
		}
		previous = event.Sequence
	}
	if err := writeJSONMessage(ctx, conn, protocol.EventBatchMessage{
		Envelope: protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "event_batch"},
		Events:   events,
	}); err != nil {
		return err
	}
	if previous > *highestSent {
		*highestSent = previous
	}
	return nil
}

func writeHeartbeat(ctx context.Context, conn *websocket.Conn) error {
	return writeJSONMessage(ctx, conn, protocol.HeartbeatMessage{
		Envelope:   protocol.Envelope{ProtocolVersion: protocol.ProtocolVersion, Type: "heartbeat"},
		OccurredAt: time.Now().UTC(),
	})
}

type serverResult struct {
	ack         *protocol.EventsAckMessage
	serverError protocol.ErrorMessage
	err         error
}

func readServerMessages(
	ctx context.Context,
	conn *websocket.Conn,
	results chan<- serverResult,
	done chan<- struct{},
) {
	defer close(done)
	for {
		raw, err := readTextMessage(ctx, conn)
		if err != nil {
			publishServerResult(ctx, results, serverResult{err: err})
			return
		}
		result, err := decodeServerMessage(raw)
		if err != nil {
			publishServerResult(ctx, results, serverResult{err: fatal(err)})
			return
		}
		if !publishServerResult(ctx, results, result) {
			return
		}
	}
}

func publishServerResult(ctx context.Context, results chan<- serverResult, result serverResult) bool {
	select {
	case results <- result:
		return true
	case <-ctx.Done():
		return false
	}
}

func decodeServerMessage(raw []byte) (serverResult, error) {
	var envelope protocol.Envelope
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return serverResult{}, errInvalidServerMessage
	}
	if envelope.ProtocolVersion != protocol.ProtocolVersion {
		return serverResult{}, errUnsupportedServerProtocolVersion
	}
	switch envelope.Type {
	case "events_acked":
		var acknowledged protocol.EventsAckMessage
		if err := decodeStrictMessage(raw, "events_acked", &acknowledged); err != nil {
			return serverResult{}, err
		}
		if err := requireEnvelope(acknowledged.Envelope, "events_acked"); err != nil {
			return serverResult{}, err
		}
		return serverResult{ack: &acknowledged}, nil
	case "error":
		var serverError protocol.ErrorMessage
		if err := decodeStrictMessage(raw, "error", &serverError); err != nil {
			return serverResult{}, err
		}
		if err := requireEnvelope(serverError.Envelope, "error"); err != nil {
			return serverResult{}, err
		}
		if !validServerErrorCode(serverError.Code) || serverError.Message == "" || utf8.RuneCountInString(serverError.Message) > 512 {
			return serverResult{}, fmt.Errorf("server error message is invalid")
		}
		return serverResult{serverError: serverError}, nil
	default:
		return serverResult{}, errUnexpectedServerMessageType
	}
}

func validServerErrorCode(code string) bool {
	if len(code) == 0 || len(code) > 80 {
		return false
	}
	for _, character := range code {
		if (character < 'a' || character > 'z') &&
			(character < '0' || character > '9') &&
			character != '_' {
			return false
		}
	}
	return true
}

func readTextMessage(ctx context.Context, conn *websocket.Conn) ([]byte, error) {
	messageType, raw, err := conn.Read(ctx)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if errors.Is(err, websocket.ErrMessageTooBig) || websocket.CloseStatus(err) == websocket.StatusMessageTooBig {
			return nil, fatalErrorf("Gateway message exceeds 1 MiB")
		}
		switch websocket.CloseStatus(err) {
		case websocket.StatusProtocolError, websocket.StatusUnsupportedData, websocket.StatusPolicyViolation:
			return nil, fatalErrorf("Gateway closed the connection for a protocol violation")
		case websocket.StatusCode(4403):
			return nil, errGatewayAuthenticationRejected
		default:
			return nil, fmt.Errorf("Gateway connection closed")
		}
	}
	if messageType != websocket.MessageText {
		return nil, fatalErrorf("Gateway message type must be text")
	}
	return raw, nil
}

func writeJSONMessage(ctx context.Context, conn *websocket.Conn, message any) error {
	encoded, err := json.Marshal(message)
	if err != nil {
		return fatal(fmt.Errorf("encode Gateway message: %w", err))
	}
	if err := conn.Write(ctx, websocket.MessageText, encoded); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("write Gateway message: %w", err)
	}
	return nil
}

func decodeStrictMessage(raw []byte, _ string, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errInvalidServerMessage
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return errInvalidServerMessage
	}
	return nil
}

func requireEnvelope(envelope protocol.Envelope, expectedType string) error {
	if envelope.ProtocolVersion != protocol.ProtocolVersion {
		return errUnsupportedServerProtocolVersion
	}
	if envelope.Type != expectedType {
		return errUnexpectedServerMessageType
	}
	return nil
}

func parseStoredPrivateKey(privateKeyPEM string) (ed25519.PrivateKey, error) {
	block, rest := pem.Decode([]byte(privateKeyPEM))
	if block == nil || block.Type != "PRIVATE KEY" || len(block.Headers) != 0 || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("stored private key is not one PKCS#8 PEM block")
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse stored PKCS#8 private key: %w", err)
	}
	privateKey, ok := parsed.(ed25519.PrivateKey)
	if !ok || len(privateKey) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("stored private key is not Ed25519")
	}
	return privateKey, nil
}

func validateDeviceCertificate(
	certificatePEM string,
	caCertificatePEM string,
	nodeID string,
	expectedPublicKey ed25519.PublicKey,
	now time.Time,
) (*x509.Certificate, error) {
	certificate, err := parseSingleCertificate(certificatePEM, "device certificate")
	if err != nil {
		return nil, err
	}
	if certificate.Subject.CommonName != nodeID {
		return nil, fmt.Errorf("device certificate common name does not match node ID")
	}
	if certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign != 0 {
		return nil, fmt.Errorf("device certificate must be an end-entity certificate")
	}
	if !hasClientAuth(certificate.ExtKeyUsage) {
		return nil, fmt.Errorf("device certificate must explicitly allow client authentication")
	}
	publicKey, ok := certificate.PublicKey.(ed25519.PublicKey)
	if !ok || !bytes.Equal(publicKey, expectedPublicKey) {
		return nil, fmt.Errorf("device certificate public key does not match stored private key")
	}
	caCertificate, err := parseEnrolledCA(caCertificatePEM, now)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	roots.AddCert(caCertificate)
	if _, err := certificate.Verify(x509.VerifyOptions{
		Roots:       roots,
		CurrentTime: now,
		KeyUsages:   []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}); err != nil {
		return nil, fmt.Errorf("verify device certificate chain: %w", err)
	}
	return certificate, nil
}

func certificateFingerprint(certificate *x509.Certificate) string {
	return fmt.Sprintf("%x", sha256.Sum256(certificate.Raw))
}

func parseSingleCertificate(certificatePEM, label string) (*x509.Certificate, error) {
	block, rest := pem.Decode([]byte(certificatePEM))
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, fmt.Errorf("%s is not one certificate PEM block", label)
	}
	certificate, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", label, err)
	}
	return certificate, nil
}

func parseEnrolledCA(caCertificatePEM string, now time.Time) (*x509.Certificate, error) {
	certificate, err := parseSingleCertificate(caCertificatePEM, "enrolled CA certificate")
	if err != nil {
		return nil, err
	}
	if !certificate.BasicConstraintsValid || !certificate.IsCA || certificate.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, fmt.Errorf("enrolled CA certificate does not permit certificate signing")
	}
	if now.Before(certificate.NotBefore) || now.After(certificate.NotAfter) {
		return nil, fmt.Errorf("enrolled CA certificate is not currently valid")
	}
	if !bytes.Equal(certificate.RawIssuer, certificate.RawSubject) || certificate.CheckSignatureFrom(certificate) != nil {
		return nil, fmt.Errorf("enrolled CA certificate is not self-signed")
	}
	return certificate, nil
}

func hasClientAuth(usages []x509.ExtKeyUsage) bool {
	for _, usage := range usages {
		if usage == x509.ExtKeyUsageClientAuth {
			return true
		}
	}
	return false
}

type fatalError struct {
	err error
}

func (e *fatalError) Error() string {
	return e.err.Error()
}

func (e *fatalError) Unwrap() error {
	return e.err
}

func fatal(err error) error {
	if err == nil {
		return nil
	}
	var existing *fatalError
	if errors.As(err, &existing) {
		return err
	}
	return &fatalError{err: err}
}

func fatalErrorf(format string, arguments ...any) error {
	return fatal(fmt.Errorf(format, arguments...))
}

func jitteredExponentialBackoff(attempt int) time.Duration {
	base := time.Second
	switch {
	case attempt <= 0:
	case attempt >= 5:
		base = 30 * time.Second
	default:
		base *= time.Duration(1 << attempt)
	}
	jitterRange := base / 5
	jitter := time.Duration(mathrand.Int64N(int64(2*jitterRange)+1)) - jitterRange
	delay := base + jitter
	if delay < time.Second {
		return time.Second
	}
	if delay > 30*time.Second {
		return 30 * time.Second
	}
	return delay
}
