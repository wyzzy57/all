package protocol

import "time"

const ProtocolVersion = 1

type Envelope struct {
	ProtocolVersion int    `json:"protocol_version"`
	Type            string `json:"type"`
}

type Event struct {
	Sequence   uint64         `json:"sequence"`
	EventType  string         `json:"event_type"`
	CommandID  string         `json:"command_id,omitempty"`
	Stage      string         `json:"stage,omitempty"`
	Payload    map[string]any `json:"payload"`
	OccurredAt time.Time      `json:"occurred_at"`
}

type EnrollmentFacts struct {
	Architecture string
	PlatformKind string
}

type EnrollmentRequest struct {
	ProtocolVersion     int    `json:"protocol_version"`
	Token               string `json:"token"`
	EnrollmentRequestID string `json:"enrollment_request_id"`
	NodeName            string `json:"node_name"`
	Architecture        string `json:"architecture"`
	PlatformKind        string `json:"platform_kind"`
	AgentVersion        string `json:"agent_version"`
	CSRPEM              string `json:"csr_pem"`
}

type EnrollmentResponse struct {
	ProtocolVersion          int    `json:"protocol_version"`
	EnrollmentRequestID      string `json:"enrollment_request_id"`
	NodeID                   string `json:"node_id"`
	CertificatePEM           string `json:"certificate_pem"`
	CACertificatePEM         string `json:"ca_certificate_pem"`
	GatewayURL               string `json:"gateway_url"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
}

type ChallengeMessage struct {
	Envelope
	Nonce string `json:"nonce"`
}

type AuthenticateMessage struct {
	Envelope
	NodeID         string `json:"node_id"`
	CertificatePEM string `json:"certificate_pem"`
	Signature      string `json:"signature"`
}

type AuthenticatedMessage struct {
	Envelope
	HeartbeatIntervalSeconds int `json:"heartbeat_interval_seconds"`
}

type InventoryMessage struct {
	Envelope
	Architecture string         `json:"architecture"`
	PlatformKind string         `json:"platform_kind"`
	Capabilities map[string]any `json:"capabilities"`
	Resources    map[string]any `json:"resources"`
	Fingerprint  map[string]any `json:"fingerprint"`
	AgentVersion string         `json:"agent_version"`
}

type HeartbeatMessage struct {
	Envelope
	OccurredAt time.Time `json:"occurred_at"`
}

type EventBatchMessage struct {
	Envelope
	Events []Event `json:"events"`
}

type EventsAckMessage struct {
	Envelope
	ThroughSequence uint64 `json:"through_sequence"`
}

type CertificateRenewalRequest struct {
	Envelope
	RenewalRequestID string `json:"renewal_request_id"`
	CSRPEM           string `json:"csr_pem"`
}

type CertificateRenewalCandidateMessage struct {
	Envelope
	RenewalRequestID             string `json:"renewal_request_id"`
	CertificatePEM               string `json:"certificate_pem"`
	CertificateFingerprintSHA256 string `json:"certificate_fingerprint_sha256"`
}

type CertificateRenewalAckMessage struct {
	Envelope
	RenewalRequestID             string `json:"renewal_request_id"`
	CertificateFingerprintSHA256 string `json:"certificate_fingerprint_sha256"`
}

type CertificateRenewalActivatedMessage struct {
	Envelope
	RenewalRequestID             string `json:"renewal_request_id"`
	CertificateFingerprintSHA256 string `json:"certificate_fingerprint_sha256"`
}

type ErrorMessage struct {
	Envelope
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}
