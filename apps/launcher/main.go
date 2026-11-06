package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func projectRoot() string {
	exe, err := os.Executable()
	if err != nil {
		wd, _ := os.Getwd()
		return wd
	}
	dir := filepath.Dir(exe)
	// portable layout: launcher next to docker-compose.yml or in apps/launcher/bin
	candidates := []string{
		dir,
		filepath.Join(dir, "..", ".."),
		filepath.Join(dir, ".."),
	}
	for _, c := range candidates {
		if _, err := os.Stat(filepath.Join(c, "docker-compose.yml")); err == nil {
			abs, _ := filepath.Abs(c)
			return abs
		}
	}
	wd, _ := os.Getwd()
	return wd
}

func run(ctx context.Context, name string, args ...string) error {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = os.Environ()
	return cmd.Run()
}

func runOut(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = os.Environ()
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func compose(ctx context.Context, root string, args ...string) error {
	dataRoot := os.Getenv("RAG_DATA_ROOT")
	if dataRoot == "" {
		dataRoot = os.Getenv("DATA_ROOT")
	}
	envPrefix := ""
	if dataRoot != "" {
		envPrefix = fmt.Sprintf("export RAG_DATA_ROOT=%q DATA_ROOT=%q; ", dataRoot, dataRoot)
	}
	if runtime.GOOS == "windows" {
		wslPath := toWSLPath(root)
		script := fmt.Sprintf("%scd %q && export PATH=/usr/bin:/bin:/usr/sbin:/sbin && sudo docker compose --profile core %s", envPrefix, wslPath, strings.Join(args, " "))
		return run(ctx, "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc", script)
	}
	all := append([]string{"compose", "--profile", "core"}, args...)
	cmd := exec.CommandContext(ctx, "docker", all...)
	cmd.Dir = root
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = os.Environ()
	if dataRoot != "" {
		cmd.Env = append(cmd.Env, "RAG_DATA_ROOT="+dataRoot, "DATA_ROOT="+dataRoot)
	}
	return cmd.Run()
}

func toWSLPath(p string) string {
	p = filepath.Clean(p)
	if len(p) >= 2 && p[1] == ':' {
		drive := strings.ToLower(string(p[0]))
		rest := strings.ReplaceAll(p[2:], "\\", "/")
		return "/mnt/" + drive + rest
	}
	return strings.ReplaceAll(p, "\\", "/")
}

func portFree(addr string) bool {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return false
	}
	_ = ln.Close()
	return true
}

func waitHealthy(ctx context.Context, url string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 3 * time.Second}
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		resp, err := client.Do(req)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode < 500 {
				return nil
			}
		}
		time.Sleep(2 * time.Second)
	}
	return fmt.Errorf("timeout waiting for %s", url)
}

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	_ = cmd.Start()
}

func checkFreeDiskGB(path string, minGB float64) (float64, error) {
	if runtime.GOOS == "windows" {
		// PowerShell: free bytes on drive of path
		drive := filepath.VolumeName(path)
		if drive == "" {
			drive = "C:"
		}
		ps := fmt.Sprintf("(Get-PSDrive -Name '%s').Free", strings.TrimSuffix(drive, ":"))
		out, err := runOut(context.Background(), "powershell", "-NoProfile", "-Command", ps)
		if err != nil {
			return 0, err
		}
		free, err := strconv.ParseFloat(strings.TrimSpace(out), 64)
		if err != nil {
			return 0, err
		}
		gb := free / (1024 * 1024 * 1024)
		if gb < minGB {
			return gb, fmt.Errorf("only %.1f GB free on %s (need ≥ %.0f GB)", gb, drive, minGB)
		}
		return gb, nil
	}
	out, err := runOut(context.Background(), "df", "-BG", "--output=avail", path)
	if err != nil {
		return 0, err
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) < 2 {
		return 0, fmt.Errorf("df parse failed")
	}
	avail := strings.TrimSpace(strings.TrimSuffix(lines[len(lines)-1], "G"))
	gb, err := strconv.ParseFloat(avail, 64)
	if err != nil {
		return 0, err
	}
	if gb < minGB {
		return gb, fmt.Errorf("only %.1f GB free under %s (need ≥ %.0f GB)", gb, path, minGB)
	}
	return gb, nil
}

func checkGPU() {
	ctx := context.Background()
	var out string
	var err error
	if runtime.GOOS == "windows" {
		out, err = runOut(ctx, "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc", "nvidia-smi -L 2>/dev/null | head -n 3")
	} else {
		out, err = runOut(ctx, "nvidia-smi", "-L")
	}
	if err != nil || strings.TrimSpace(out) == "" {
		fmt.Println("[RAG5060] GPU: nvidia-smi not available — Ollama may fall back to CPU (slow)")
		fmt.Println("[RAG5060] Install NVIDIA driver + Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/")
		return
	}
	fmt.Println("[RAG5060] GPU:", strings.TrimSpace(strings.Split(out, "\n")[0]))
}

func preflight(root string) error {
	fmt.Println("[RAG5060] preflight…")
	if runtime.GOOS == "windows" {
		if err := run(context.Background(), "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc", "export PATH=/usr/bin:/bin; sudo docker version >/dev/null"); err != nil {
			return fmt.Errorf("Docker Engine in WSL Ubuntu-24.04 not available: %w\nHint: install Docker Engine inside Ubuntu-24.04 (Desktop not required)", err)
		}
	} else {
		if err := run(context.Background(), "docker", "version"); err != nil {
			return fmt.Errorf("docker not available: %w", err)
		}
	}
	checkGPU()

	diskPath := root
	if runtime.GOOS == "windows" {
		// Prefer D: for heavy data when present
		if _, err := os.Stat(`D:\`); err == nil {
			diskPath = `D:\`
		}
	}
	if gb, err := checkFreeDiskGB(diskPath, 20); err != nil {
		fmt.Fprintf(os.Stderr, "[RAG5060] disk warning: %v\n", err)
	} else {
		fmt.Printf("[RAG5060] free disk ≈ %.1f GB (%s)\n", gb, diskPath)
	}

	if !portFree("127.0.0.1:8080") {
		fmt.Println("[RAG5060] port 8080 already in use — assuming stack is up")
	}
	if v := os.Getenv("RAG_DATA_ROOT"); v != "" {
		fmt.Println("[RAG5060] RAG_DATA_ROOT=", v)
	}
	return nil
}

func usage() {
	fmt.Println(`RAG5060 launcher
Usage:
  RAG5060 start|stop|status|open

Env:
  RAG_DATA_ROOT / DATA_ROOT  optional host data root hint for compose
`)
}

func main() {
	root := projectRoot()
	args := os.Args[1:]
	cmd := "start"
	if len(args) > 0 {
		cmd = args[0]
	}
	ctx := context.Background()
	ui := "http://127.0.0.1:8080"

	switch cmd {
	case "start":
		if err := preflight(root); err != nil {
			fmt.Fprintf(os.Stderr, "preflight failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("[RAG5060] compose up…")
		if err := compose(ctx, root, "up", "-d", "--build"); err != nil {
			fmt.Fprintf(os.Stderr, "compose up failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("[RAG5060] waiting for UI…")
		if err := waitHealthy(ctx, ui+"/api/health", 5*time.Minute); err != nil {
			fmt.Fprintf(os.Stderr, "health wait: %v\n", err)
		}
		openBrowser(ui)
		fmt.Println("[RAG5060] opened", ui)
	case "stop":
		_ = compose(ctx, root, "down")
		fmt.Println("[RAG5060] stopped (volumes kept)")
	case "status":
		_ = compose(ctx, root, "ps")
	case "open":
		openBrowser(ui)
	default:
		usage()
		os.Exit(2)
	}
}
