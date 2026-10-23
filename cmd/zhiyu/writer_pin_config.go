package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"

	"zhiyu.local/zhiyu-go/internal/app"
	"zhiyu.local/zhiyu-go/internal/audit"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// A local operator pins a process to one owner/generation before startup.
// Never discover the current owner from the database and automatically adopt it.
func configuredRecoveryWriter(configPath, namespace string) (*app.RecoveryWriterPin, error) {
	invalid := errors.New("WRITER_PIN_CONFIGURATION_INVALID")
	path := filepath.Join(filepath.Dir(configPath), "writer-pin.json")
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 4096 {
		return nil, invalid
	}
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 4096 {
		return nil, invalid
	}
	if _, err = audit.ParseRawObject(raw); err != nil {
		return nil, invalid
	}
	var config struct {
		Protocol   string   `json:"protocol"`
		Namespace  string   `json:"namespace"`
		OwnerID    funds.ID `json:"owner_id"`
		Generation uint64   `json:"generation"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&config) != nil || decoder.Decode(new(any)) != io.EOF || config.Protocol != "zhiyu-writer-pin-v1" || config.Namespace != namespace || config.OwnerID == (funds.ID{}) || config.Generation == 0 || config.Generation > math.MaxInt64 {
		return nil, invalid
	}
	return &app.RecoveryWriterPin{OwnerID: config.OwnerID, Generation: config.Generation}, nil
}
