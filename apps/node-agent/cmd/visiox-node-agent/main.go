package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/wyzzy57/all/apps/node-agent/internal/config"
	"github.com/wyzzy57/all/apps/node-agent/internal/gateway"
	"github.com/wyzzy57/all/apps/node-agent/internal/identity"
	"github.com/wyzzy57/all/apps/node-agent/internal/inventory"
	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
	"github.com/wyzzy57/all/apps/node-agent/internal/state"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx); err != nil {
		if errors.Is(err, context.Canceled) && ctx.Err() != nil {
			return
		}
		_, _ = fmt.Fprintf(os.Stderr, "visiox node agent: %v\n", err)
		os.Exit(1)
	}
}

func run(ctx context.Context) (runErr error) {
	cfg, err := config.Load()
	if err != nil {
		return fmt.Errorf("load configuration: %w", err)
	}
	if err := os.MkdirAll(cfg.StateDir, 0o700); err != nil {
		return fmt.Errorf("create state directory: %w", err)
	}
	if err := os.Chmod(cfg.StateDir, 0o700); err != nil {
		return fmt.Errorf("set state directory permissions: %w", err)
	}
	store, err := state.Open(filepath.Join(cfg.StateDir, "agent.db"))
	if err != nil {
		return err
	}
	defer func() {
		if err := store.Close(); err != nil && runErr == nil {
			runErr = fmt.Errorf("close state database: %w", err)
		}
	}()

	detected, err := inventory.Probe(ctx, inventory.ExecRunner{}, inventory.OSFileSystem{})
	if err != nil {
		return fmt.Errorf("probe hardware inventory: %w", err)
	}
	detected.AgentVersion = cfg.AgentVersion
	facts := protocol.EnrollmentFacts{
		Architecture: detected.Architecture,
		PlatformKind: detected.PlatformKind,
	}
	if _, err := identity.Ensure(ctx, cfg, facts, store, nil); err != nil {
		return fmt.Errorf("ensure node identity: %w", err)
	}
	if _, err := store.AppendEvent("agent_started", map[string]any{}); err != nil {
		return fmt.Errorf("persist agent_started event: %w", err)
	}
	if err := gateway.New(cfg, store, detected).Run(ctx); err != nil {
		return fmt.Errorf("run Gateway client: %w", err)
	}
	return nil
}
