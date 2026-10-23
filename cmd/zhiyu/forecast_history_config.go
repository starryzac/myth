package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// Only fixed issuer public keys and preset files are loaded by the app.
// Signing private keys remain with an independent simulated statement producer.
func configuredForecastHistory(configPath, namespace string, logger *slog.Logger) *app.ForecastHistorySources {
	closed := func(status string) *app.ForecastHistorySources {
		if logger != nil {
			logger.Warn("forecast_history_configuration", "status", status)
		}
		return nil
	}
	absolute, e := filepath.Abs(configPath)
	if e != nil {
		return closed("HISTORY_SOURCES_INVALID")
	}
	base, e := filepath.EvalSymlinks(filepath.Dir(absolute))
	if e != nil {
		return closed("HISTORY_SOURCES_INVALID")
	}
	path := filepath.Join(base, "forecast-history-sources.json")
	info, e := os.Lstat(path)
	if os.IsNotExist(e) {
		return closed("HISTORY_SOURCES_NOT_CONFIGURED")
	}
	if e != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 65536 {
		return closed("HISTORY_SOURCES_INVALID")
	}
	raw, e := os.ReadFile(path)
	if e != nil || len(raw) > 65536 {
		return closed("HISTORY_SOURCES_INVALID")
	}
	if _, e = audit.ParseRawObject(raw); e != nil {
		return closed("HISTORY_SOURCES_INVALID")
	}
	var config struct {
		Protocol string `json:"protocol"`
		Sources  []struct {
			ID        string                       `json:"id"`
			File      string                       `json:"file"`
			Authority app.ForecastHistoryAuthority `json:"authority"`
		} `json:"sources"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&config) != nil || d.Decode(new(any)) != io.EOF || config.Protocol != "forecast-history-sources-config-v1" || len(config.Sources) < 1 || len(config.Sources) > 16 {
		return closed("HISTORY_SOURCES_INVALID")
	}
	out := &app.ForecastHistorySources{Sources: map[string]app.ForecastHistorySource{}}
	for _, source := range config.Sources {
		if source.ID == "" || len(source.ID) > 64 || strings.ContainsAny(source.ID, "/\\\x00\r\n") || source.File == "" || filepath.IsAbs(source.File) || filepath.VolumeName(source.File) != "" {
			return closed("HISTORY_SOURCES_INVALID")
		}
		if _, exists := out.Sources[source.ID]; exists {
			return closed("HISTORY_SOURCES_INVALID")
		}
		clean := filepath.Clean(source.File)
		if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
			return closed("HISTORY_SOURCES_INVALID")
		}
		a := source.Authority
		public, e := hex.DecodeString(a.PublicKeyHex)
		if e != nil || len(public) != ed25519.PublicKeySize || a.Namespace != namespace || a.UserID == (funds.ID{}) || a.IssuerID == "" || len(a.IssuerID) > 128 || len(a.AccountIDs) < 1 || len(a.AccountIDs) > 32 {
			return closed("HISTORY_SOURCES_INVALID")
		}
		for i, id := range a.AccountIDs {
			if id == (funds.ID{}) || i > 0 && a.AccountIDs[i-1].String() >= id.String() {
				return closed("HISTORY_SOURCES_INVALID")
			}
		}
		reader, e := app.NewForecastHistoryFileReader(base, filepath.Join(base, clean))
		if e != nil {
			return closed("HISTORY_SOURCES_INVALID")
		}
		out.Sources[source.ID] = app.ForecastHistorySource{Authority: a, Reader: reader}
	}
	if logger != nil {
		logger.Info("forecast_history_configuration", "status", "CONFIGURED_PUBLIC_VERIFIERS_NOT_OBSERVED")
	}
	return out
}
