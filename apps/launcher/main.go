package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
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
	// portable layout: launcher next to docker-compose.yml or in apps/launcher/bin / dist/
	candidates := []string{
		dir,
		filepath.Join(dir, ".."),
		filepath.Join(dir, "..", ".."),
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

func logPath(root string) string {
	dir := filepath.Join(root, "logs")
	_ = os.MkdirAll(dir, 0o755)
	return filepath.Join(dir, "launcher.log")
}

func appendLog(root, line string) {
	f, err := os.OpenFile(logPath(root), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = fmt.Fprintf(f, "%s %s\n", time.Now().Format(time.RFC3339), line)
}

func pauseExit(code int) {
	if runtime.GOOS == "windows" {
		fmt.Println()
		fmt.Println("Нажмите Enter, чтобы закрыть это окно…")
		_, _ = fmt.Scanln()
	}
	os.Exit(code)
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

func dockerBinWSL(ctx context.Context) string {
	// Prefer non-interactive docker (user in docker group). Avoid sudo password prompts from .exe.
	out, err := runOut(ctx, "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc",
		"export PATH=/usr/bin:/bin:/usr/sbin:/sbin; if docker info >/dev/null 2>&1; then echo docker; elif sudo -n docker info >/dev/null 2>&1; then echo 'sudo docker'; else echo docker; fi")
	if err != nil {
		return "docker"
	}
	b := strings.TrimSpace(out)
	if b == "sudo docker" {
		return "sudo docker"
	}
	return "docker"
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
		dock := dockerBinWSL(ctx)
		script := fmt.Sprintf("%scd %q && export PATH=/usr/bin:/bin:/usr/sbin:/sbin && %s compose --profile core %s",
			envPrefix, wslPath, dock, strings.Join(args, " "))
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

func shortBody(b []byte) string {
	s := strings.TrimSpace(string(b))
	s = strings.ReplaceAll(s, "\n", " ")
	if len(s) > 80 {
		return s[:80] + "…"
	}
	return s
}

func healthOK(body []byte) bool {
	var payload struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return false
	}
	return payload.Status == "ok"
}

// waitHealthy requires several consecutive real JSON {"status":"ok"} replies.
// A single flaky 200 is not enough (WSL/docker port proxy can flap).
func waitHealthy(ctx context.Context, url string, timeout time.Duration) error {
	const need = 3
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 3 * time.Second}
	var last string
	started := time.Now()
	n := 0
	streak := 0
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		n++
		elapsed := int(time.Since(started).Seconds())
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		resp, err := client.Do(req)
		if err == nil {
			body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
			resp.Body.Close()
			last = fmt.Sprintf("HTTP %d %s", resp.StatusCode, shortBody(body))
			if resp.StatusCode == 200 && healthOK(body) {
				streak++
				fmt.Printf("[RAG5060] health ok %d/%d… %d с\n", streak, need, elapsed)
				if streak >= need {
					fmt.Printf("[RAG5060] API стабильно готов за %d с\n", elapsed)
					return nil
				}
			} else {
				streak = 0
				fmt.Printf("[RAG5060] ждём API… %d с | попытка %d | %s\n", elapsed, n, last)
			}
		} else {
			streak = 0
			last = err.Error()
			fmt.Printf("[RAG5060] ждём API… %d с | попытка %d | %s\n", elapsed, n, last)
		}
		time.Sleep(1 * time.Second)
	}
	return fmt.Errorf("timeout waiting for %s (last: %s)", url, last)
}

func uiPort() string {
	port := strings.TrimSpace(os.Getenv("RAG_UI_PORT"))
	if port == "" {
		port = "18080"
	}
	return port
}

func uiBaseURL() string {
	return "http://127.0.0.1:" + uiPort()
}

// wslEthIP returns the primary WSL eth0 IPv4 (fallback when Windows↔WSL localhost relay is stale).
func wslEthIP(ctx context.Context) string {
	out, err := runOut(ctx, "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc",
		`ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1`)
	if err != nil {
		return ""
	}
	ip := strings.TrimSpace(out)
	if net.ParseIP(ip) == nil || strings.HasPrefix(ip, "127.") {
		return ""
	}
	return ip
}

// resolveReachableUI picks 127.0.0.1 when healthy, else WSL eth0 IP (stale wslrelay workaround).
func resolveReachableUI(ctx context.Context) string {
	local := uiBaseURL()
	if err := waitHealthy(ctx, local+"/api/health", 8*time.Second); err == nil {
		return local
	}
	if runtime.GOOS == "windows" {
		if ip := wslEthIP(ctx); ip != "" {
			alt := "http://" + ip + ":" + uiPort()
			fmt.Println("[RAG5060] 127.0.0.1 недоступен (сломан WSL localhost) — пробую", alt)
			if err := waitHealthy(ctx, alt+"/api/health", 20*time.Second); err == nil {
				return alt
			}
		}
	}
	return local
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
		return
	}
	fmt.Println("[RAG5060] GPU:", strings.TrimSpace(strings.Split(out, "\n")[0]))
}

func preflight(root string) error {
	fmt.Println("[RAG5060] preflight…")
	if runtime.GOOS == "windows" {
		dock := dockerBinWSL(context.Background())
		script := fmt.Sprintf("export PATH=/usr/bin:/bin:/usr/sbin:/sbin; %s version >/dev/null", dock)
		if err := run(context.Background(), "wsl", "-d", "Ubuntu-24.04", "--", "bash", "-lc", script); err != nil {
			return fmt.Errorf("Docker Engine in WSL Ubuntu-24.04 not available: %w\nHint: install Docker Engine inside Ubuntu-24.04; add user to docker group (no sudo password)", err)
		}
		fmt.Println("[RAG5060] docker via:", dock)
	} else {
		if err := run(context.Background(), "docker", "version"); err != nil {
			return fmt.Errorf("docker not available: %w", err)
		}
	}
	checkGPU()

	diskPath := root
	if runtime.GOOS == "windows" {
		if _, err := os.Stat(`D:\`); err == nil {
			diskPath = `D:\`
		}
	}
	if gb, err := checkFreeDiskGB(diskPath, 20); err != nil {
		fmt.Fprintf(os.Stderr, "[RAG5060] disk warning: %v\n", err)
	} else {
		fmt.Printf("[RAG5060] free disk ≈ %.1f GB (%s)\n", gb, diskPath)
	}

	uiPort := strings.TrimSpace(os.Getenv("RAG_UI_PORT"))
	if uiPort == "" {
		uiPort = "18080"
	}
	if !portFree("127.0.0.1:" + uiPort) {
		fmt.Printf("[RAG5060] port %s already in use — will wait for healthy API\n", uiPort)
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
	ui := uiBaseURL()
	appendLog(root, "cmd="+cmd+" root="+root+" ui="+ui)

	switch cmd {
	case "start":
		if err := preflight(root); err != nil {
			fmt.Fprintf(os.Stderr, "preflight failed: %v\n", err)
			appendLog(root, "preflight failed: "+err.Error())
			fmt.Println("Лог:", logPath(root))
			pauseExit(1)
		}
		openUI := func(base string) {
			url := fmt.Sprintf("%s/?boot=%d", base, time.Now().Unix())
			openBrowser(url)
			fmt.Println("[RAG5060] готово:", url)
			fmt.Println("Логин по умолчанию: admin / admin")
			fmt.Println("В браузере дождитесь зелёного «API готов», затем «Войти».")
			if !strings.Contains(base, "127.0.0.1") {
				fmt.Println("Открыт IP WSL — так надо, когда сломан проброс localhost.")
			}
			appendLog(root, "opened "+url)
		}

		// If API already healthy on localhost or WSL IP — do not recreate containers.
		if base := resolveReachableUI(ctx); base != "" {
			if err := waitHealthy(ctx, base+"/api/health", 3*time.Second); err == nil {
				fmt.Println("[RAG5060] стек уже работает — открываю UI без перезапуска")
				openUI(base)
				pauseExit(0)
			}
		}
		fmt.Println("[RAG5060] compose up…")
		// Never --force-recreate on every start: it drops :18080 mid-login and looks like «Войти всё ломает».
		// Port/mapping changes apply via normal `up -d` when compose config actually changes.
		if err := compose(ctx, root, "up", "-d"); err != nil {
			fmt.Fprintf(os.Stderr, "compose up failed: %v\n", err)
			appendLog(root, "compose up failed: "+err.Error())
			fmt.Println("Лог:", logPath(root))
			pauseExit(1)
		}
		fmt.Println("[RAG5060] ждём стабильной готовности API…")
		base := resolveReachableUI(ctx)
		if err := waitHealthy(ctx, base+"/api/health", 5*time.Minute); err != nil {
			// Last chance: refresh WSL IP after compose settled.
			base = resolveReachableUI(ctx)
			if err2 := waitHealthy(ctx, base+"/api/health", 30*time.Second); err2 != nil {
				fmt.Fprintf(os.Stderr, "health wait: %v\n", err)
				appendLog(root, "health wait: "+err.Error())
				fmt.Println("Стек ещё не готов — браузер НЕ открываю.")
				fmt.Println("Проверьте: wsl -d Ubuntu-24.04 -- docker compose -f", toWSLPath(root)+"/docker-compose.yml", "--profile core ps")
				fmt.Println("Лог:", logPath(root))
				pauseExit(1)
			}
		}
		openUI(base)
		pauseExit(0)
	case "stop":
		_ = compose(ctx, root, "down")
		fmt.Println("[RAG5060] stopped (volumes kept)")
		pauseExit(0)
	case "status":
		_ = compose(ctx, root, "ps")
		pauseExit(0)
	case "open":
		base := resolveReachableUI(ctx)
		if err := waitHealthy(ctx, base+"/api/health", 30*time.Second); err != nil {
			fmt.Fprintf(os.Stderr, "UI not healthy: %v\n", err)
			pauseExit(1)
		}
		openBrowser(base)
		pauseExit(0)
	default:
		usage()
		pauseExit(2)
	}
}
