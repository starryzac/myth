// Package deepseek is an explicit, finite adapter for the official DeepSeek
// endpoint. It never discovers legacy settings, .env files or credentials.
package deepseek

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"zhiyu.local/zhiyu-go/internal/agent/domain"
	"zhiyu.local/zhiyu-go/internal/audit"
)

const Endpoint = "https://api.deepseek.com/chat/completions"
const Model = "deepseek-flash"

var ErrNotConfigured = errors.New("DEEPSEEK_NOT_CONFIGURED")
var ErrConfig = errors.New("DEEPSEEK_INVALID_CONFIG")

// Config keeps credentials private, and fmt/JSON representations cannot print
// them. Only Load can create production configuration for this adapter.
type Config struct {
	endpoint string
	model    string
	key      string
}

func (Config) String() string   { return "DeepSeekConfig{credentials:redacted}" }
func (Config) GoString() string { return "DeepSeekConfig{credentials:redacted}" }

type fileConfig struct {
	Enabled    bool   `json:"enabled"`
	Endpoint   string `json:"endpoint"`
	Model      string `json:"model"`
	APIKeyEnv  string `json:"api_key_env,omitempty"`
	APIKeyFile string `json:"api_key_file,omitempty"`
}

func Limits() domain.Limits {
	l := domain.DefaultLimits()
	l.Timeout = 20 * time.Second
	l.MaxProviderCalls = 3
	l.MaxToolCalls = 3
	l.MaxOutputTokens = 1024
	l.MaxCostMicros = 20000
	// USD peak/cache-miss prices checked against official docs 2026-10-20.
	l.InputPriceMicrosPerMillion = 300000
	l.OutputPriceMicrosPerMillion = 1200000
	return l
}
func boundedFile(root, relative string, maximum int64) ([]byte, error) {
	if filepath.IsAbs(relative) || filepath.VolumeName(relative) != "" {
		return nil, ErrConfig
	}
	path := filepath.Join(root, filepath.FromSlash(relative))
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return nil, ErrConfig
	}
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrNotConfigured
		}
		return nil, ErrConfig
	}
	if !strings.EqualFold(filepath.Clean(real), filepath.Clean(path)) {
		return nil, ErrConfig
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > maximum {
		return nil, ErrConfig
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, ErrConfig
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, maximum+1))
	if err != nil || int64(len(raw)) > maximum {
		return nil, ErrConfig
	}
	return raw, nil
}
func keyValid(key string) bool {
	if len(key) < 16 || len(key) > 256 {
		return false
	}
	for _, c := range key {
		if c < 33 || c > 126 {
			return false
		}
	}
	return true
}

// Load reads only absolute new-runtime/deepseek.json. Missing/disabled config
// or missing key returns ErrNotConfigured so the caller can retain offline mode.
// A file key is restricted to this runtime's credentials/deepseek.key; an env
// key name must be explicitly selected in the new ZYGO_DEEPSEEK_ namespace.
func Load(runtimeDir string, getenv func(string) string) (Config, error) {
	if !filepath.IsAbs(runtimeDir) || filepath.Base(filepath.Clean(runtimeDir)) != ".runtime" {
		return Config{}, ErrConfig
	}
	real, err := filepath.EvalSymlinks(runtimeDir)
	if err != nil {
		if os.IsNotExist(err) {
			return Config{}, ErrNotConfigured
		}
		return Config{}, ErrConfig
	}
	if !strings.EqualFold(filepath.Clean(real), filepath.Clean(runtimeDir)) {
		return Config{}, ErrConfig
	}
	raw, err := boundedFile(runtimeDir, "deepseek.json", 8192)
	if err != nil {
		return Config{}, err
	}
	object, err := audit.ParseRawObject(raw)
	if err != nil {
		return Config{}, ErrConfig
	}
	for key := range object {
		switch key {
		case "enabled", "endpoint", "model", "api_key_env", "api_key_file":
		default:
			return Config{}, ErrConfig
		}
	}
	for _, key := range []string{"enabled", "endpoint", "model"} {
		if _, ok := object[key]; !ok {
			return Config{}, ErrConfig
		}
	}
	if _, ok := object["enabled"].(bool); !ok {
		return Config{}, ErrConfig
	}
	var in fileConfig
	if json.Unmarshal(raw, &in) != nil || in.Endpoint != Endpoint || in.Model != Model {
		return Config{}, ErrConfig
	}
	_, hasEnv := object["api_key_env"]
	_, hasFile := object["api_key_file"]
	if hasEnv == hasFile || (hasEnv && (in.APIKeyEnv == "" || !regexp.MustCompile(`^ZYGO_DEEPSEEK_[A-Z0-9_]{1,64}$`).MatchString(in.APIKeyEnv))) || (hasFile && in.APIKeyFile != "credentials/deepseek.key") {
		return Config{}, ErrConfig
	}
	if !in.Enabled {
		return Config{}, ErrNotConfigured
	}
	var key string
	if hasEnv {
		if getenv == nil {
			return Config{}, ErrConfig
		}
		key = getenv(in.APIKeyEnv)
	} else {
		var value []byte
		value, err = boundedFile(runtimeDir, in.APIKeyFile, 258)
		if err != nil {
			return Config{}, err
		}
		key = strings.TrimSuffix(strings.TrimSuffix(string(value), "\n"), "\r")
	}
	if key == "" {
		return Config{}, ErrNotConfigured
	}
	if !keyValid(key) {
		return Config{}, ErrConfig
	}
	return Config{Endpoint, Model, key}, nil
}
