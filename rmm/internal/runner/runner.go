// Package runner executes MyPortal scripts and collects their results.
//
// A script receives:
//
//   - Parameters. PowerShell scripts get them splatted into their param()
//     block, with their JSON types (numbers, booleans for switches, arrays).
//     Bash and zsh scripts get "--Name value" pairs in the declared order; a
//     switch that is on is passed as "--Name" alone and one that is off is
//     left out; lists are joined with commas.
//   - Environment variables, plus MYPORTAL_RUN_ID and MYPORTAL_RESULT_FILE.
//
// A script sends custom values back to MyPortal either by printing lines like
//
//	##myportal[asset.BitLocker Status]=On
//	##myportal[company.Tenant ID]=contoso
//	##myportal[session.id]=123456789   (remote control activation scripts)
//
// or by writing JSON to $MYPORTAL_RESULT_FILE: an array of
// {"scope": "asset"|"company"|"session", "name": "...", "value": "..."}
// objects, or an object {"asset": {"Name": "value"}, "company": {...}}.
package runner

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// MaxOutputBytes caps how much stdout/stderr is kept per stream.
const MaxOutputBytes = 1 << 20

var markerPattern = regexp.MustCompile(`^\s*##myportal\[(asset|company|session)\.([^\]]+)\]=(.*?)\s*$`)

// Runner executes jobs inside WorkDir.
type Runner struct {
	WorkDir string
	// Lookup finds interpreters; replaced in tests.
	Lookup func(string) (string, error)
}

// New returns a runner that keeps temporary files under workDir.
func New(workDir string) *Runner {
	return &Runner{WorkDir: workDir, Lookup: exec.LookPath}
}

// AvailableShells lists the interpreters found on this device.
func (r *Runner) AvailableShells() []string {
	var shells []string
	for _, name := range []string{"powershell", "pwsh", "bash", "zsh"} {
		if _, err := r.Lookup(name); err == nil {
			shells = append(shells, name)
		}
	}
	return shells
}

type limitedBuffer struct {
	buf       bytes.Buffer
	limit     int
	truncated bool
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	remaining := b.limit - b.buf.Len()
	if remaining <= 0 {
		b.truncated = true
		return len(p), nil
	}
	if len(p) > remaining {
		b.buf.Write(p[:remaining])
		b.truncated = true
		return len(p), nil
	}
	return b.buf.Write(p)
}

func (b *limitedBuffer) String() string {
	text := strings.ToValidUTF8(b.buf.String(), "�")
	if b.truncated {
		text += "\n… output truncated by the RMM agent …"
	}
	return text
}

// Run executes one job and returns its result. It never returns an error:
// problems are reported in Result.Error so MyPortal can show them.
func (r *Runner) Run(ctx context.Context, job client.Job) client.Result {
	result := client.Result{CustomValues: []client.CustomValue{}}
	sum := sha256.Sum256([]byte(job.Script))
	if job.SHA256 != "" && !strings.EqualFold(hex.EncodeToString(sum[:]), job.SHA256) {
		result.Error = "The script content did not match its checksum, so it was not run."
		return result
	}
	dir, err := os.MkdirTemp(r.WorkDir, fmt.Sprintf("run-%d-", job.ID))
	if err != nil {
		result.Error = "Could not create a working folder: " + err.Error()
		return result
	}
	defer os.RemoveAll(dir)
	resultFile := filepath.Join(dir, "result.json")

	name, args, err := r.command(job, dir)
	if err != nil {
		result.Error = err.Error()
		return result
	}

	timeout := time.Duration(job.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = 10 * time.Minute
	}
	runCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(runCtx, name, args...)
	cmd.Dir = dir
	cmd.Env = buildEnv(os.Environ(), job, resultFile)
	stdout := &limitedBuffer{limit: MaxOutputBytes}
	stderr := &limitedBuffer{limit: MaxOutputBytes}
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	prepareCommand(cmd)
	cmd.WaitDelay = 10 * time.Second

	runErr := cmd.Run()
	result.Stdout = stdout.String()
	result.Stderr = stderr.String()
	if errors.Is(runCtx.Err(), context.DeadlineExceeded) {
		result.TimedOut = true
	}
	var exitErr *exec.ExitError
	switch {
	case runErr == nil:
		code := 0
		result.ExitCode = &code
	case errors.As(runErr, &exitErr) && exitErr.ExitCode() >= 0:
		code := exitErr.ExitCode()
		result.ExitCode = &code
	case !result.TimedOut:
		result.Error = "Could not start the script: " + runErr.Error()
	}

	result.CustomValues = append(result.CustomValues, ParseMarkers(result.Stdout)...)
	if data, err := os.ReadFile(resultFile); err == nil && len(bytes.TrimSpace(data)) > 0 {
		values, parseErr := ParseResultFile(data)
		if parseErr != nil {
			result.Stderr += "\nMyPortal could not read MYPORTAL_RESULT_FILE: " + parseErr.Error()
		}
		result.CustomValues = append(result.CustomValues, values...)
	}
	return result
}

func buildEnv(base []string, job client.Job, resultFile string) []string {
	env := make([]string, 0, len(base)+len(job.Env)+2)
	for _, item := range base {
		if strings.HasPrefix(strings.ToUpper(item), "MYPORTAL_") {
			continue
		}
		env = append(env, item)
	}
	names := make([]string, 0, len(job.Env))
	for name := range job.Env {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if name == "" || strings.ContainsAny(name, "=\x00") {
			continue
		}
		env = append(env, name+"="+strings.ReplaceAll(job.Env[name], "\x00", ""))
	}
	env = append(env, "MYPORTAL_RUN_ID="+strconv.FormatInt(job.ID, 10), "MYPORTAL_RESULT_FILE="+resultFile)
	return env
}

func (r *Runner) command(job client.Job, dir string) (string, []string, error) {
	switch job.Language {
	case "powershell":
		return r.powershellCommand(job, dir)
	case "bash", "zsh":
		if runtime.GOOS == "windows" {
			return "", nil, errors.New("shell scripts cannot run on Windows")
		}
		interpreter, err := r.Lookup(job.Language)
		if err != nil {
			return "", nil, fmt.Errorf("%s is not installed on this device", job.Language)
		}
		script := filepath.Join(dir, "script.sh")
		content := strings.ReplaceAll(job.Script, "\r\n", "\n")
		if err := os.WriteFile(script, []byte(content), 0o700); err != nil {
			return "", nil, err
		}
		return interpreter, append([]string{script}, ShellArgs(job)...), nil
	default:
		return "", nil, fmt.Errorf("unsupported script language %q", job.Language)
	}
}

// ShellArgs turns parameters into "--Name value" arguments in declared order.
func ShellArgs(job client.Job) []string {
	order := job.ParameterOrder
	if len(order) == 0 {
		for name := range job.Parameters {
			order = append(order, name)
		}
		sort.Strings(order)
	}
	var args []string
	for _, name := range order {
		value, ok := job.Parameters[name]
		if !ok {
			continue
		}
		flag := "--" + name
		switch typed := value.(type) {
		case bool:
			if typed {
				args = append(args, flag)
			}
		case []interface{}:
			parts := make([]string, 0, len(typed))
			for _, item := range typed {
				parts = append(parts, fmt.Sprint(item))
			}
			args = append(args, flag, strings.Join(parts, ","))
		case float64:
			args = append(args, flag, strconv.FormatFloat(typed, 'f', -1, 64))
		case nil:
		default:
			args = append(args, flag, fmt.Sprint(typed))
		}
	}
	return args
}

func psQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "''") + "'"
}

// PowerShellWrapper is the command that loads the parameters and runs the script.
func PowerShellWrapper(scriptPath, paramsPath string) string {
	return strings.Join([]string{
		"$ErrorActionPreference = 'Continue'",
		"$p = Get-Content -Raw -Encoding UTF8 -LiteralPath " + psQuote(paramsPath) + " | ConvertFrom-Json",
		"$h = @{}",
		"if ($p) { foreach ($prop in $p.PSObject.Properties) { $h[$prop.Name] = $prop.Value } }",
		"$global:LASTEXITCODE = 0",
		"try { & " + psQuote(scriptPath) + " @h } catch { Write-Error $_; exit 1 }",
		"exit $LASTEXITCODE",
	}, "\n")
}

func encodePowerShell(command string) string {
	units := utf16.Encode([]rune(command))
	buf := make([]byte, len(units)*2)
	for i, unit := range units {
		buf[i*2] = byte(unit)
		buf[i*2+1] = byte(unit >> 8)
	}
	return base64.StdEncoding.EncodeToString(buf)
}

func (r *Runner) powershellCommand(job client.Job, dir string) (string, []string, error) {
	interpreter := ""
	candidates := []string{"pwsh"}
	if runtime.GOOS == "windows" {
		candidates = []string{"powershell", "pwsh"}
	}
	for _, candidate := range candidates {
		if path, err := r.Lookup(candidate); err == nil {
			interpreter = path
			break
		}
	}
	if interpreter == "" {
		return "", nil, errors.New("PowerShell is not installed on this device")
	}
	script := filepath.Join(dir, "script.ps1")
	// A UTF-8 BOM makes Windows PowerShell 5.1 read non-ASCII text correctly.
	content := append([]byte{0xEF, 0xBB, 0xBF}, []byte(job.Script)...)
	if err := os.WriteFile(script, content, 0o600); err != nil {
		return "", nil, err
	}
	params := job.Parameters
	if params == nil {
		params = map[string]interface{}{}
	}
	data, err := json.Marshal(params)
	if err != nil {
		return "", nil, err
	}
	paramsPath := filepath.Join(dir, "params.json")
	if err := os.WriteFile(paramsPath, data, 0o600); err != nil {
		return "", nil, err
	}
	return interpreter, []string{
		"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-EncodedCommand", encodePowerShell(PowerShellWrapper(script, paramsPath)),
	}, nil
}

// ParseMarkers extracts ##myportal[scope.Name]=value lines from output.
func ParseMarkers(output string) []client.CustomValue {
	var values []client.CustomValue
	scanner := bufio.NewScanner(strings.NewReader(output))
	scanner.Buffer(make([]byte, 64*1024), MaxOutputBytes)
	for scanner.Scan() {
		match := markerPattern.FindStringSubmatch(scanner.Text())
		if match == nil {
			continue
		}
		values = append(values, client.CustomValue{Scope: match[1], Name: strings.TrimSpace(match[2]), Value: match[3]})
	}
	return values
}

func stringify(value interface{}) string {
	switch typed := value.(type) {
	case nil:
		return ""
	case string:
		return typed
	case bool:
		return strconv.FormatBool(typed)
	case float64:
		return strconv.FormatFloat(typed, 'f', -1, 64)
	default:
		data, _ := json.Marshal(typed)
		return string(data)
	}
}

// ParseResultFile reads custom values written to MYPORTAL_RESULT_FILE.
func ParseResultFile(data []byte) ([]client.CustomValue, error) {
	data = bytes.TrimPrefix(bytes.TrimSpace(data), []byte{0xEF, 0xBB, 0xBF})
	// PowerShell 5.1's Out-File writes UTF-16; accept it.
	if len(data) >= 2 && data[0] == 0xFF && data[1] == 0xFE {
		units := make([]uint16, 0, len(data)/2)
		for i := 2; i+1 < len(data); i += 2 {
			units = append(units, uint16(data[i])|uint16(data[i+1])<<8)
		}
		data = []byte(strings.TrimSpace(string(utf16.Decode(units))))
	}
	var list []map[string]interface{}
	if err := json.Unmarshal(data, &list); err == nil {
		values := make([]client.CustomValue, 0, len(list))
		for _, item := range list {
			values = append(values, client.CustomValue{
				Scope: strings.ToLower(stringify(item["scope"])),
				Name:  stringify(item["name"]),
				Value: stringify(item["value"]),
			})
		}
		return values, nil
	}
	var grouped map[string]map[string]interface{}
	if err := json.Unmarshal(data, &grouped); err != nil {
		return nil, errors.New("expected a JSON array or an object with asset and company keys")
	}
	var values []client.CustomValue
	for _, scope := range []string{"asset", "company", "session"} {
		names := make([]string, 0, len(grouped[scope]))
		for name := range grouped[scope] {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			values = append(values, client.CustomValue{Scope: scope, Name: name, Value: stringify(grouped[scope][name])})
		}
	}
	return values, nil
}
