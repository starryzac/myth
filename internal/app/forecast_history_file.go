package app

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"

	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

type forecastHistoryFileReader struct{ root, path string }

// NewForecastHistoryFileReader accepts only a fixed file under an operator's
// explicit ignored runtime directory, including after symlink resolution.
func NewForecastHistoryFileReader(runtimeDirectory, path string) (ForecastHistoryReader, error) {
	root, e := filepath.EvalSymlinks(runtimeDirectory)
	if e != nil {
		return nil, reject("HISTORY_FILE_SCOPE_INVALID", nil)
	}
	absolute, e := filepath.Abs(path)
	if e != nil {
		return nil, reject("HISTORY_FILE_SCOPE_INVALID", nil)
	}
	resolved, e := filepath.EvalSymlinks(absolute)
	if e != nil {
		return nil, reject("HISTORY_FILE_SCOPE_INVALID", nil)
	}
	relative, e := filepath.Rel(root, resolved)
	if e != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return nil, reject("HISTORY_FILE_SCOPE_INVALID", nil)
	}
	return forecastHistoryFileReader{root: root, path: resolved}, nil
}

func (f forecastHistoryFileReader) ReadHistory(ctx context.Context) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	resolved, err := filepath.EvalSymlinks(f.path)
	if err != nil {
		return nil, reject("HISTORY_SOURCE_UNAVAILABLE", nil)
	}
	relative, err := filepath.Rel(f.root, resolved)
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return nil, reject("HISTORY_FILE_SCOPE_INVALID", nil)
	}
	file, err := os.Open(resolved)
	if err != nil {
		return nil, reject("HISTORY_SOURCE_UNAVAILABLE", nil)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 2*policy.MaxHistoryStatementBytes+4096 {
		return nil, reject("HISTORY_SOURCE_INVALID", nil)
	}
	raw, err := io.ReadAll(io.LimitReader(file, 2*policy.MaxHistoryStatementBytes+4097))
	if err != nil || len(raw) > 2*policy.MaxHistoryStatementBytes+4096 {
		return nil, reject("HISTORY_SOURCE_INVALID", nil)
	}
	return raw, nil
}
