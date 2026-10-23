package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/audit"
)

type nativeBillSourceConfig struct {
	Protocol string `json:"protocol"`
	Sources  []struct {
		ID   string `json:"id"`
		File string `json:"file"`
	} `json:"sources"`
}

// Optional local operator configuration, separate from role credentials. No
// statement content is read at startup; failures never disclose file contents.
func configuredNativeBills(configPath string, logger *slog.Logger) app.NativeBillSources {
	closed := func(status string) app.NativeBillSources {
		if logger != nil {
			logger.Warn("bill_sources_configuration", "status", status)
		}
		return app.NativeBillSources{}
	}
	absolute, e := filepath.Abs(configPath)
	if e != nil {
		return closed("BILL_SOURCES_INVALID")
	}
	base, e := filepath.EvalSymlinks(filepath.Dir(absolute))
	if e != nil {
		return closed("BILL_SOURCES_INVALID")
	}
	path := filepath.Join(base, "bill-sources.json")
	info, e := os.Lstat(path)
	if os.IsNotExist(e) {
		return closed("BILL_SOURCES_NOT_CONFIGURED")
	}
	if e != nil || !info.Mode().IsRegular() || info.Size() > 65536 {
		return closed("BILL_SOURCES_INVALID")
	}
	raw, e := os.ReadFile(path)
	if e != nil || len(raw) > 65536 {
		return closed("BILL_SOURCES_INVALID")
	}
	if _, e = audit.ParseRawObject(raw); e != nil {
		return closed("BILL_SOURCES_INVALID")
	}
	var cfg nativeBillSourceConfig
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&cfg) != nil || d.Decode(new(any)) != io.EOF || cfg.Protocol != "native-bill-sources-config-v1" || len(cfg.Sources) != 1 {
		return closed("BILL_SOURCES_INVALID")
	}
	result := app.NativeBillSources{Readers: map[string]app.NativeBillStatementReader{}}
	for _, source := range cfg.Sources {
		if source.File == "" || filepath.IsAbs(source.File) || filepath.VolumeName(source.File) != "" {
			return closed("BILL_SOURCES_INVALID")
		}
		clean := filepath.Clean(source.File)
		if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
			return closed("BILL_SOURCES_INVALID")
		}
		file := filepath.Join(base, clean)
		resolved, e := filepath.EvalSymlinks(file)
		if e != nil || resolved != file {
			return closed("BILL_SOURCES_INVALID")
		}
		info, e := os.Lstat(file)
		if e != nil || !info.Mode().IsRegular() || info.Size() > 4<<20 {
			return closed("BILL_SOURCES_INVALID")
		}
		reader, e := app.NewNativeBillFileReader(file, source.ID)
		if e != nil {
			return closed("BILL_SOURCES_INVALID")
		}
		result.Readers[source.ID] = reader
	}
	if logger != nil {
		logger.Info("bill_sources_configuration", "status", "CONFIGURED_NOT_OBSERVED")
	}
	return result
}
