package main

import (
	"context"
	"strings"
	"testing"

	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
)

func TestInventoryForAgentUsesFixtureForSmokeBuildWithTestVersion(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{"protocol_version":1,"type":"inventory","architecture":"amd64","platform_kind":"x86_nvidia","capabilities":{"nvidia_gpu":true},"resources":{"cpu_logical_cores":8},"fingerprint":{"platform_kind":"x86_nvidia"},"agent_version":"fixture"}`)

	called := false
	probe := func(context.Context) (protocol.InventoryMessage, error) {
		called = true
		return protocol.InventoryMessage{}, nil
	}

	got, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0-test"}, probe)
	if err != nil {
		t.Fatal(err)
	}
	if got.Architecture != "amd64" || got.PlatformKind != "x86_nvidia" || called {
		t.Fatalf("unexpected fixture result: %#v, hardware probe called=%v", got, called)
	}
}

func TestInventoryForAgentRejectsFixtureForReleaseBuild(t *testing.T) {
	if testInventoryFixtureBuild != "false" {
		t.Fatalf("release test must use the default disabled build gate, got %q", testInventoryFixtureBuild)
	}
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_BUILD", "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{"protocol_version":1,"type":"inventory","architecture":"amd64","platform_kind":"x86_nvidia"}`)

	_, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0-test"}, func(context.Context) (protocol.InventoryMessage, error) {
		t.Fatal("hardware probe must not run when a release build rejects the fixture")
		return protocol.InventoryMessage{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "disabled in this build") {
		t.Fatalf("expected release build rejection, got %v", err)
	}
}

func TestInventoryForAgentRejectsFixtureWithoutTestVersionForSmokeBuild(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{"protocol_version":1,"type":"inventory","architecture":"amd64","platform_kind":"x86_nvidia"}`)

	called := false
	probe := func(context.Context) (protocol.InventoryMessage, error) {
		called = true
		return protocol.InventoryMessage{}, nil
	}

	_, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0"}, probe)
	if err == nil || !strings.Contains(err.Error(), "only allowed for test agent versions") {
		t.Fatalf("expected production fixture rejection, got %v", err)
	}
	if called {
		t.Fatal("production fixture rejection must happen before the hardware probe")
	}
}

func TestInventoryForAgentProbesHardwareWithoutFixture(t *testing.T) {
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", "")

	want := protocol.InventoryMessage{Architecture: "amd64", PlatformKind: "x86_nvidia"}
	called := false
	probe := func(context.Context) (protocol.InventoryMessage, error) {
		called = true
		return want, nil
	}

	got, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0"}, probe)
	if err != nil {
		t.Fatal(err)
	}
	if !called || got.Architecture != want.Architecture || got.PlatformKind != want.PlatformKind {
		t.Fatalf("hardware probe was not used: got %#v called=%v", got, called)
	}
}

func TestInventoryForAgentRejectsMalformedFixture(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", "not-json")

	_, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0-test"}, func(context.Context) (protocol.InventoryMessage, error) {
		t.Fatal("hardware probe must not run for a malformed fixture")
		return protocol.InventoryMessage{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "decode test inventory fixture") {
		t.Fatalf("expected fixture decode error, got %v", err)
	}
}

func TestInventoryForAgentRequiresInventoryEnvelope(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{"protocol_version":1,"type":"heartbeat","architecture":"amd64","platform_kind":"x86_nvidia"}`)

	_, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0-test"}, func(context.Context) (protocol.InventoryMessage, error) {
		t.Fatal("hardware probe must not run for an invalid fixture")
		return protocol.InventoryMessage{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "inventory fixture must use protocol type") {
		t.Fatalf("expected inventory envelope error, got %v", err)
	}
}

func TestInventoryForAgentRequiresVersionToEndWithTest(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{}`)

	_, err := inventoryForAgent(context.Background(), config.Config{AgentVersion: "0.1.0-test-build"}, func(context.Context) (protocol.InventoryMessage, error) {
		t.Fatal("hardware probe must not run for a test-suffixed version")
		return protocol.InventoryMessage{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "only allowed for test agent versions") {
		t.Fatalf("expected non-suffixed version rejection, got %v", err)
	}
}

func TestInventoryForAgentDoesNotTreatEmptyVersionAsTest(t *testing.T) {
	setTestInventoryFixtureBuild(t, "true")
	t.Setenv("VISIOX_AGENT_TEST_INVENTORY_JSON", `{}`)

	_, err := inventoryForAgent(context.Background(), config.Config{}, func(context.Context) (protocol.InventoryMessage, error) {
		t.Fatal("hardware probe must not run when an injection is present")
		return protocol.InventoryMessage{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "only allowed for test agent versions") {
		t.Fatalf("expected empty-version rejection, got %v", err)
	}
}

func setTestInventoryFixtureBuild(t *testing.T, value string) {
	t.Helper()
	previous := testInventoryFixtureBuild
	testInventoryFixtureBuild = value
	t.Cleanup(func() {
		testInventoryFixtureBuild = previous
	})
}
