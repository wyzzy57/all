package inventory

import (
	"context"
	"errors"
	"io/fs"
	"strings"
	"testing"

	"github.com/wyzzy57/all/apps/node-agent/internal/protocol"
)

func TestProbeDetectsJetsonBeforeX86NVIDIA(t *testing.T) {
	files := fakeFS{
		"/etc/nv_tegra_release":       "# R36 (release), REVISION: 4.3\n",
		"/proc/device-tree/model":     "NVIDIA Jetson AGX Orin\x00",
		"/proc/meminfo":               "MemTotal:       32517888 kB\nMemAvailable:   30100000 kB\n",
		"/proc/driver/nvidia/version": "NVRM version: test-driver\x00\n",
	}
	runner := &fakeRunner{outputs: map[string]string{
		"uname -m":                                    "aarch64\n",
		"getconf _NPROCESSORS_ONLN":                   "12\n",
		"df -B1 --output=size,avail /":                "       1B-blocks       Avail\n100000000000 75000000000\n",
		"dpkg-query -W -f=${Version} nvidia-l4t-core": "36.4.3\x00\n",
		"dpkg-query -W -f=${Version} nvidia-jetpack":  "6.2.0\n",
		"dpkg-query -W -f=${Version} libnvinfer10":    "10.3.0\n",
		"nvcc --version":                              "Cuda compilation tools, release 12.6\x00\n",
		"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "0, GPU-should-not-be-read, Wrong GPU, 9.0, 1, 1, wrong\n",
	}}

	got, err := Probe(context.Background(), runner, files)
	if err != nil {
		t.Fatal(err)
	}
	if got.Envelope != (protocol.Envelope{ProtocolVersion: 1, Type: "inventory"}) {
		t.Fatalf("unexpected envelope: %#v", got.Envelope)
	}
	if got.PlatformKind != "jetson" || got.Architecture != "arm64" {
		t.Fatalf("unexpected platform: %#v", got)
	}
	assertInt64Resource(t, got.Resources, "cpu_logical_cores", 12)
	assertInt64Resource(t, got.Resources, "memory_total_bytes", int64(32517888*1024))
	assertInt64Resource(t, got.Resources, "memory_available_bytes", int64(30100000*1024))
	assertInt64Resource(t, got.Resources, "disk_total_bytes", 100000000000)
	assertInt64Resource(t, got.Resources, "disk_available_bytes", 75000000000)

	gpus, ok := got.Resources["gpus"].([]map[string]any)
	if !ok || len(gpus) != 1 {
		t.Fatalf("unexpected Jetson GPUs: %#v", got.Resources["gpus"])
	}
	if gpus[0]["index"] != int64(0) || gpus[0]["name"] != "NVIDIA Jetson AGX Orin" || gpus[0]["memory_shared"] != true {
		t.Fatalf("unexpected integrated GPU: %#v", gpus[0])
	}
	if _, exists := gpus[0]["memory_total_bytes"]; exists {
		t.Fatalf("Jetson GPU must not fabricate dedicated memory: %#v", gpus[0])
	}

	wantFingerprint := map[string]any{
		"platform_kind": "jetson",
		"architecture":  "arm64",
		"driver":        "NVRM version: test-driver",
		"cuda":          "Cuda compilation tools, release 12.6",
		"tensorrt":      "10.3.0",
		"jetpack":       "6.2.0",
		"l4t":           "36.4.3",
	}
	for key, want := range wantFingerprint {
		if got.Fingerprint[key] != want {
			t.Errorf("fingerprint[%q] = %#v, want %#v", key, got.Fingerprint[key], want)
		}
	}
	if names, ok := got.Fingerprint["gpu_names"].([]string); !ok || len(names) != 1 || names[0] != "NVIDIA Jetson AGX Orin" {
		t.Fatalf("unexpected GPU names: %#v", got.Fingerprint["gpu_names"])
	}
	for _, call := range runner.calls {
		if strings.HasPrefix(call, "nvidia-smi ") {
			t.Fatalf("Jetson detection must precede x86 NVIDIA probing; called %q", call)
		}
	}
}

func TestProbeDetectsAndPreservesEveryX86NVIDIAGPU(t *testing.T) {
	files := fakeFS{
		"/proc/meminfo": "MemTotal:       66000000 kB\nMemAvailable:   32000000 kB\n",
	}
	runner := &fakeRunner{outputs: map[string]string{
		"uname -m":                     "x86_64\n",
		"getconf _NPROCESSORS_ONLN":    "32\n",
		"df -B1 --output=size,avail /": "1B-blocks Avail\n2000000000000 1500000000000\n",
		"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "0, GPU-abc, NVIDIA RTX 4090, 8.9, 24564, 24000, 550.54.15\n1, N/A, NVIDIA RTX 4000 Ada, 8.9, 20475, 19000, 550.54.15\n",
		"trtexec --version": "TensorRT version: 10.3.0\x00\n",
		"nvcc --version":    "Cuda compilation tools, release 12.4\n",
	}}

	got, err := Probe(context.Background(), runner, files)
	if err != nil {
		t.Fatal(err)
	}
	if got.PlatformKind != "x86_nvidia" || got.Architecture != "amd64" {
		t.Fatalf("unexpected platform: %#v", got)
	}
	gpus, ok := got.Resources["gpus"].([]map[string]any)
	if !ok || len(gpus) != 2 {
		t.Fatalf("unexpected GPUs: %#v", got.Resources["gpus"])
	}
	if gpus[0]["index"] != int64(0) || gpus[0]["uuid"] != "GPU-abc" || gpus[0]["name"] != "NVIDIA RTX 4090" {
		t.Fatalf("unexpected first GPU: %#v", gpus[0])
	}
	if gpus[0]["memory_total_bytes"] != int64(24564*1024*1024) || gpus[0]["memory_free_bytes"] != int64(24000*1024*1024) {
		t.Fatalf("first GPU memory was not normalized: %#v", gpus[0])
	}
	if _, exists := gpus[1]["uuid"]; exists {
		t.Fatalf("unavailable UUID must be omitted: %#v", gpus[1])
	}
	if gpus[1]["memory_total_bytes"] != int64(20475*1024*1024) || gpus[1]["memory_free_bytes"] != int64(19000*1024*1024) {
		t.Fatalf("second GPU memory was not normalized: %#v", gpus[1])
	}
	if got.Fingerprint["driver"] != "550.54.15" || got.Fingerprint["tensorrt"] != "TensorRT version: 10.3.0" {
		t.Fatalf("unexpected version fingerprint: %#v", got.Fingerprint)
	}
	if names, ok := got.Fingerprint["gpu_names"].([]string); !ok || len(names) != 2 || names[1] != "NVIDIA RTX 4000 Ada" {
		t.Fatalf("unexpected GPU names: %#v", got.Fingerprint["gpu_names"])
	}
	if capabilities, ok := got.Fingerprint["compute_capabilities"].([]string); !ok || len(capabilities) != 2 || capabilities[0] != "8.9" || capabilities[1] != "8.9" {
		t.Fatalf("unexpected compute capabilities: %#v", got.Fingerprint["compute_capabilities"])
	}
}

func TestProbeOrdersX86NVIDIAGPUsByIndex(t *testing.T) {
	runner := &fakeRunner{outputs: map[string]string{
		"uname -m": "x86_64\n",
		"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "1, GPU-one, GPU One, 8.9, 2, 1, 550\n0, GPU-zero, GPU Zero, 8.6, 2, 1, 550\n",
	}}

	got, err := Probe(context.Background(), runner, fakeFS{})
	if err != nil {
		t.Fatal(err)
	}
	gpus := got.Resources["gpus"].([]map[string]any)
	if gpus[0]["index"] != int64(0) || gpus[1]["index"] != int64(1) {
		t.Fatalf("GPUs are not ordered by index: %#v", gpus)
	}
	names := got.Fingerprint["gpu_names"].([]string)
	capabilities := got.Fingerprint["compute_capabilities"].([]string)
	if names[0] != "GPU Zero" || names[1] != "GPU One" || capabilities[0] != "8.6" || capabilities[1] != "8.9" {
		t.Fatalf("fingerprint does not follow canonical GPU order: %#v", got.Fingerprint)
	}
}

func TestProbeOmitsUnavailableJetsonModel(t *testing.T) {
	files := fakeFS{"/etc/nv_tegra_release": "# R36\n"}
	runner := &fakeRunner{outputs: map[string]string{"uname -m": "aarch64\n"}}

	got, err := Probe(context.Background(), runner, files)
	if err != nil {
		t.Fatal(err)
	}
	gpus := got.Resources["gpus"].([]map[string]any)
	if _, exists := gpus[0]["name"]; exists {
		t.Fatalf("missing device-tree model must not be fabricated: %#v", gpus[0])
	}
	if _, exists := got.Fingerprint["gpu_names"]; exists {
		t.Fatalf("missing GPU names must be omitted: %#v", got.Fingerprint)
	}
}

func TestProbeRejectsUnsupportedPlatforms(t *testing.T) {
	tests := []struct {
		name   string
		arch   string
		smiErr error
	}{
		{name: "arm64 without Jetson markers", arch: "aarch64\n"},
		{name: "amd64 without working nvidia-smi", arch: "x86_64\n", smiErr: errors.New("not found")},
		{name: "unsupported architecture", arch: "riscv64\n"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{
				outputs: map[string]string{"uname -m": test.arch},
				errors: map[string]error{
					"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": test.smiErr,
				},
			}
			_, err := Probe(context.Background(), runner, fakeFS{})
			if !errors.Is(err, ErrUnsupportedPlatform) {
				t.Fatalf("error = %v, want ErrUnsupportedPlatform", err)
			}
		})
	}
}

func TestProbeRejectsNumericOverflow(t *testing.T) {
	t.Run("memory KiB", func(t *testing.T) {
		files := fakeFS{
			"/etc/nv_tegra_release":   "# R36",
			"/proc/device-tree/model": "NVIDIA Jetson Orin\x00",
			"/proc/meminfo":           "MemTotal: 9007199254740992 kB\n",
		}
		_, err := Probe(context.Background(), &fakeRunner{outputs: map[string]string{"uname -m": "aarch64"}}, files)
		if err == nil || !strings.Contains(err.Error(), "overflows int64 bytes") {
			t.Fatalf("error = %v, want byte overflow", err)
		}
	})

	t.Run("GPU MiB", func(t *testing.T) {
		runner := &fakeRunner{outputs: map[string]string{
			"uname -m": "x86_64",
			"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "0, GPU-abc, NVIDIA GPU, 8.9, 8796093022208, 1, 550\n",
		}}
		_, err := Probe(context.Background(), runner, fakeFS{})
		if err == nil || !strings.Contains(err.Error(), "overflows int64 bytes") {
			t.Fatalf("error = %v, want byte overflow", err)
		}
	})

	t.Run("GPU index", func(t *testing.T) {
		runner := &fakeRunner{outputs: map[string]string{
			"uname -m": "x86_64",
			"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "9223372036854775808, GPU-abc, NVIDIA GPU, 8.9, 1, 1, 550\n",
		}}
		_, err := Probe(context.Background(), runner, fakeFS{})
		if err == nil || !strings.Contains(err.Error(), "GPU index") {
			t.Fatalf("error = %v, want GPU index overflow", err)
		}
	})

	t.Run("disk bytes", func(t *testing.T) {
		runner := &fakeRunner{outputs: map[string]string{
			"uname -m": "x86_64",
			"nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "0, GPU-abc, NVIDIA GPU, 8.9, 1, 1, 550\n",
			"df -B1 --output=size,avail /": "1B-blocks Avail\n9223372036854775808 1\n",
		}}
		_, err := Probe(context.Background(), runner, fakeFS{})
		if err == nil || !strings.Contains(err.Error(), "overflows int64 bytes") {
			t.Fatalf("error = %v, want disk byte overflow", err)
		}
	})
}

func assertInt64Resource(t *testing.T, resources map[string]any, key string, want int64) {
	t.Helper()
	got, ok := resources[key].(int64)
	if !ok || got != want {
		t.Fatalf("resources[%q] = %#v, want int64(%d)", key, resources[key], want)
	}
}

type fakeFS map[string]string

func (f fakeFS) ReadFile(name string) ([]byte, error) {
	value, ok := f[name]
	if !ok {
		return nil, fs.ErrNotExist
	}
	return []byte(value), nil
}

type fakeRunner struct {
	outputs map[string]string
	errors  map[string]error
	calls   []string
}

func (r *fakeRunner) Run(ctx context.Context, name string, args ...string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	command := strings.Join(append([]string{name}, args...), " ")
	r.calls = append(r.calls, command)
	if err, ok := r.errors[command]; ok && err != nil {
		return "", err
	}
	if output, ok := r.outputs[command]; ok {
		return output, nil
	}
	return "", errors.New("command not found")
}
