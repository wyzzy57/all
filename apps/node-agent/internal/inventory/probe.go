package inventory

import (
	"bufio"
	"context"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"unicode"

	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
)

const nvidiaSMIQuery = "--query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version"

var ErrUnsupportedPlatform = errors.New("unsupported NVIDIA platform")

type CommandRunner interface {
	Run(ctx context.Context, name string, args ...string) (string, error)
}

type FileSystem interface {
	ReadFile(name string) ([]byte, error)
}

type ExecRunner struct{}

func (ExecRunner) Run(ctx context.Context, name string, args ...string) (string, error) {
	output, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
	return string(output), err
}

type OSFileSystem struct{}

func (OSFileSystem) ReadFile(name string) ([]byte, error) {
	return os.ReadFile(name)
}

func Probe(ctx context.Context, runner CommandRunner, fileSystem FileSystem) (protocol.InventoryMessage, error) {
	if runner == nil {
		return protocol.InventoryMessage{}, fmt.Errorf("command runner must not be nil")
	}
	if fileSystem == nil {
		return protocol.InventoryMessage{}, fmt.Errorf("file system must not be nil")
	}
	if err := ctx.Err(); err != nil {
		return protocol.InventoryMessage{}, err
	}

	tegraRelease, tegraFound := readOptionalFile(fileSystem, "/etc/nv_tegra_release")
	model, modelFound := readOptionalFile(fileSystem, "/proc/device-tree/model")
	isJetson := tegraFound || (modelFound && strings.Contains(strings.ToLower(model), "jetson"))

	rawArchitecture, architectureFound, err := runOptional(ctx, runner, "uname", "-m")
	if err != nil {
		return protocol.InventoryMessage{}, err
	}
	architecture := normalizeArchitecture(rawArchitecture)

	if isJetson {
		if architectureFound && architecture != "arm64" {
			return protocol.InventoryMessage{}, fmt.Errorf("%w: Jetson marker reported on architecture %q", ErrUnsupportedPlatform, rawArchitecture)
		}
		return probeJetson(ctx, runner, fileSystem, model, tegraRelease)
	}
	if !architectureFound || architecture != "amd64" {
		return protocol.InventoryMessage{}, fmt.Errorf("%w: architecture %q", ErrUnsupportedPlatform, rawArchitecture)
	}

	rawGPUs, err := runner.Run(ctx, "nvidia-smi", nvidiaSMIQuery, "--format=csv,noheader,nounits")
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return protocol.InventoryMessage{}, ctxErr
		}
		return protocol.InventoryMessage{}, fmt.Errorf("%w: nvidia-smi is unavailable", ErrUnsupportedPlatform)
	}
	gpus, gpuNames, computeCapabilities, driver, err := parseNVIDIAGPUs(rawGPUs)
	if err != nil {
		return protocol.InventoryMessage{}, err
	}
	if len(gpus) == 0 {
		return protocol.InventoryMessage{}, fmt.Errorf("%w: nvidia-smi returned no GPUs", ErrUnsupportedPlatform)
	}

	resources, err := probeHostResources(ctx, runner, fileSystem, gpus)
	if err != nil {
		return protocol.InventoryMessage{}, err
	}
	fingerprint := baseFingerprint("x86_nvidia", "amd64", gpuNames, computeCapabilities)
	setOptional(fingerprint, "driver", driver)
	if value, found, err := runOptional(ctx, runner, "nvcc", "--version"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["cuda"] = value
	}
	if value, found, err := runOptional(ctx, runner, "trtexec", "--version"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["tensorrt"] = value
	}

	return newInventory("amd64", "x86_nvidia", resources, fingerprint), nil
}

func probeJetson(
	ctx context.Context,
	runner CommandRunner,
	fileSystem FileSystem,
	model string,
	tegraRelease string,
) (protocol.InventoryMessage, error) {
	gpu := map[string]any{
		"index":         int64(0),
		"memory_shared": true,
	}
	var gpuNames []string
	if model != "" {
		gpu["name"] = model
		gpuNames = []string{model}
	}
	gpus := []map[string]any{gpu}
	resources, err := probeHostResources(ctx, runner, fileSystem, gpus)
	if err != nil {
		return protocol.InventoryMessage{}, err
	}
	fingerprint := baseFingerprint("jetson", "arm64", gpuNames, nil)
	if driver, found := readOptionalFile(fileSystem, "/proc/driver/nvidia/version"); found {
		fingerprint["driver"] = driver
	}
	if value, found, err := runOptional(ctx, runner, "nvcc", "--version"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["cuda"] = value
	}
	if value, found, err := runOptional(ctx, runner, "dpkg-query", "-W", "-f=${Version}", "libnvinfer10"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["tensorrt"] = value
	} else if value, found, err = runOptional(ctx, runner, "trtexec", "--version"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["tensorrt"] = value
	}
	if value, found, err := runOptional(ctx, runner, "dpkg-query", "-W", "-f=${Version}", "nvidia-jetpack"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["jetpack"] = value
	}
	if value, found, err := runOptional(ctx, runner, "dpkg-query", "-W", "-f=${Version}", "nvidia-l4t-core"); err != nil {
		return protocol.InventoryMessage{}, err
	} else if found {
		fingerprint["l4t"] = value
	} else if tegraRelease != "" {
		fingerprint["l4t"] = tegraRelease
	}

	return newInventory("arm64", "jetson", resources, fingerprint), nil
}

func probeHostResources(
	ctx context.Context,
	runner CommandRunner,
	fileSystem FileSystem,
	gpus []map[string]any,
) (map[string]any, error) {
	resources := map[string]any{"gpus": gpus}

	if rawMemory, found := readOptionalFile(fileSystem, "/proc/meminfo"); found {
		memory, err := parseMeminfo(rawMemory)
		if err != nil {
			return nil, err
		}
		for key, value := range memory {
			resources[key] = value
		}
	}
	if rawCPUCount, found, err := runOptional(ctx, runner, "getconf", "_NPROCESSORS_ONLN"); err != nil {
		return nil, err
	} else if found {
		count, err := parsePositiveInt64(rawCPUCount, "logical CPU count")
		if err != nil {
			return nil, err
		}
		resources["cpu_logical_cores"] = count
	}
	if rawDisk, found, err := runOptional(ctx, runner, "df", "-B1", "--output=size,avail", "/"); err != nil {
		return nil, err
	} else if found {
		total, available, err := parseDiskBytes(rawDisk)
		if err != nil {
			return nil, err
		}
		resources["disk_total_bytes"] = total
		resources["disk_available_bytes"] = available
	}
	return resources, nil
}

func parseMeminfo(raw string) (map[string]int64, error) {
	wanted := map[string]string{
		"MemTotal:":     "memory_total_bytes",
		"MemAvailable:": "memory_available_bytes",
	}
	values := make(map[string]int64, len(wanted))
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) == 0 {
			continue
		}
		resourceKey, wantedField := wanted[fields[0]]
		if !wantedField {
			continue
		}
		if len(fields) != 3 || fields[2] != "kB" {
			return nil, fmt.Errorf("invalid %s entry in /proc/meminfo", fields[0])
		}
		bytes, err := checkedBytes(fields[1], 1024, fields[0])
		if err != nil {
			return nil, err
		}
		values[resourceKey] = bytes
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read /proc/meminfo: %w", err)
	}
	return values, nil
}

func parseDiskBytes(raw string) (int64, int64, error) {
	lines := strings.Split(raw, "\n")
	for index := len(lines) - 1; index >= 0; index-- {
		fields := strings.Fields(lines[index])
		if len(fields) == 0 {
			continue
		}
		if len(fields) != 2 {
			continue
		}
		if _, err := strconv.ParseUint(fields[0], 10, 64); err != nil {
			continue
		}
		total, err := checkedBytes(fields[0], 1, "disk total")
		if err != nil {
			return 0, 0, err
		}
		available, err := checkedBytes(fields[1], 1, "disk available")
		if err != nil {
			return 0, 0, err
		}
		return total, available, nil
	}
	return 0, 0, fmt.Errorf("invalid df output")
}

func parseNVIDIAGPUs(raw string) ([]map[string]any, []string, []string, string, error) {
	reader := csv.NewReader(strings.NewReader(raw))
	reader.TrimLeadingSpace = true
	reader.FieldsPerRecord = 7

	var gpus []map[string]any
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, nil, nil, "", fmt.Errorf("parse nvidia-smi CSV: %w", err)
		}
		for index := range record {
			record[index] = trimRaw(record[index])
		}
		gpuIndex, err := strconv.ParseInt(record[0], 10, 64)
		if err != nil || gpuIndex < 0 {
			return nil, nil, nil, "", fmt.Errorf("invalid GPU index %q", record[0])
		}
		if !available(record[2]) {
			return nil, nil, nil, "", fmt.Errorf("GPU %d has no name", gpuIndex)
		}
		gpu := map[string]any{
			"index": gpuIndex,
			"name":  record[2],
		}
		if available(record[1]) {
			gpu["uuid"] = record[1]
		}
		if available(record[3]) {
			gpu["compute_capability"] = record[3]
		}
		if available(record[4]) {
			value, err := checkedBytes(record[4], 1024*1024, fmt.Sprintf("GPU %d memory total", gpuIndex))
			if err != nil {
				return nil, nil, nil, "", err
			}
			gpu["memory_total_bytes"] = value
		}
		if available(record[5]) {
			value, err := checkedBytes(record[5], 1024*1024, fmt.Sprintf("GPU %d memory free", gpuIndex))
			if err != nil {
				return nil, nil, nil, "", err
			}
			gpu["memory_free_bytes"] = value
		}
		if available(record[6]) {
			gpu["driver"] = record[6]
		}
		gpus = append(gpus, gpu)
	}
	sort.Slice(gpus, func(left, right int) bool {
		return gpus[left]["index"].(int64) < gpus[right]["index"].(int64)
	})
	var (
		gpuNames            []string
		computeCapabilities []string
		fingerprintDriver   string
	)
	for index, gpu := range gpus {
		if index > 0 && gpu["index"] == gpus[index-1]["index"] {
			return nil, nil, nil, "", fmt.Errorf("duplicate GPU index %d", gpu["index"])
		}
		gpuNames = append(gpuNames, gpu["name"].(string))
		if capability, ok := gpu["compute_capability"].(string); ok {
			computeCapabilities = append(computeCapabilities, capability)
		}
		if driver, ok := gpu["driver"].(string); ok && fingerprintDriver == "" {
			fingerprintDriver = driver
		}
	}
	return gpus, gpuNames, computeCapabilities, fingerprintDriver, nil
}

func checkedBytes(raw string, multiplier uint64, label string) (int64, error) {
	value, err := strconv.ParseUint(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid %s value %q", label, raw)
	}
	if value > uint64(math.MaxInt64)/multiplier {
		return 0, fmt.Errorf("%s value %q overflows int64 bytes", label, raw)
	}
	return int64(value * multiplier), nil
}

func parsePositiveInt64(raw string, label string) (int64, error) {
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("invalid %s %q", label, raw)
	}
	return value, nil
}

func runOptional(ctx context.Context, runner CommandRunner, name string, args ...string) (string, bool, error) {
	output, err := runner.Run(ctx, name, args...)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return "", false, ctxErr
		}
		return "", false, nil
	}
	trimmed := trimRaw(output)
	return trimmed, trimmed != "", nil
}

func readOptionalFile(fileSystem FileSystem, name string) (string, bool) {
	contents, err := fileSystem.ReadFile(name)
	if err != nil {
		return "", false
	}
	return trimRaw(string(contents)), true
}

func normalizeArchitecture(raw string) string {
	switch strings.ToLower(trimRaw(raw)) {
	case "aarch64", "arm64":
		return "arm64"
	case "x86_64", "amd64":
		return "amd64"
	default:
		return ""
	}
}

func trimRaw(value string) string {
	return strings.TrimFunc(value, func(r rune) bool {
		return r == 0 || unicode.IsSpace(r)
	})
}

func available(value string) bool {
	switch strings.ToLower(trimRaw(value)) {
	case "", "n/a", "[n/a]", "not supported", "[not supported]":
		return false
	default:
		return true
	}
}

func baseFingerprint(platformKind, architecture string, gpuNames, computeCapabilities []string) map[string]any {
	fingerprint := map[string]any{
		"platform_kind": platformKind,
		"architecture":  architecture,
	}
	if len(gpuNames) != 0 {
		fingerprint["gpu_names"] = gpuNames
	}
	if len(computeCapabilities) != 0 {
		fingerprint["compute_capabilities"] = computeCapabilities
	}
	return fingerprint
}

func setOptional(target map[string]any, key, value string) {
	if available(value) {
		target[key] = value
	}
}

func newInventory(
	architecture string,
	platformKind string,
	resources map[string]any,
	fingerprint map[string]any,
) protocol.InventoryMessage {
	return protocol.InventoryMessage{
		Envelope: protocol.Envelope{
			ProtocolVersion: protocol.ProtocolVersion,
			Type:            "inventory",
		},
		Architecture: architecture,
		PlatformKind: platformKind,
		Capabilities: map[string]any{"nvidia_gpu": true},
		Resources:    resources,
		Fingerprint:  fingerprint,
	}
}
