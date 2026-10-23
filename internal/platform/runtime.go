package platform

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"os"
	"strings"
)

// Runtime is an explicit role-scoped local configuration. The application
// runtime is forbidden from carrying migration, admin, or bank DB credentials.
type Runtime struct {
	Role             string `json:"role"`
	Environment      string `json:"environment"`
	Namespace        string `json:"namespace"`
	Simulation       bool   `json:"simulation"`
	ListenAddr       string `json:"listen_addr,omitempty"`
	AppDSN           string `json:"app_dsn,omitempty"`
	MigrationDSN     string `json:"migration_dsn,omitempty"`
	BankDSN          string `json:"bank_dsn,omitempty"`
	BankURL          string `json:"bank_url,omitempty"`
	BankPermitSecret string `json:"bank_permit_secret,omitempty"`
	WebOrigin        string `json:"web_origin,omitempty"`
	SeedState        string `json:"seed_state,omitempty"`
}

func LoadRuntime(path string) (Runtime, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Runtime{}, errors.New("runtime configuration unavailable")
	}
	if len(raw) > 64*1024 {
		return Runtime{}, errors.New("runtime configuration exceeds capacity")
	}
	var c Runtime
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&c) != nil {
		return Runtime{}, errors.New("invalid runtime configuration")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return Runtime{}, errors.New("runtime configuration has trailing data")
	}
	if err = c.Validate(); err != nil {
		return Runtime{}, err
	}
	return c, nil
}

func (c Runtime) Validate() error {
	if !c.Simulation || !environmentPattern.MatchString(c.Environment) || !environmentPattern.MatchString(c.Namespace) {
		return errors.New("runtime requires an explicit isolated simulation identity")
	}
	if c.ListenAddr == "" {
		c.ListenAddr = "127.0.0.1:19310"
	}
	if _, err := Load(func(k string) string {
		switch k {
		case "ZYGO_ENVIRONMENT":
			return c.Environment
		case "ZYGO_SIMULATION_MODE":
			return "true"
		case "ZYGO_LISTEN_ADDR":
			return c.ListenAddr
		case "ZYGO_DATABASE_URL":
			return c.AppDSN
		}
		return ""
	}); err != nil {
		return errors.New("invalid isolated runtime endpoint")
	}
	switch c.Role {
	case "app":
		if c.AppDSN == "" || c.MigrationDSN != "" || c.BankDSN != "" || len(c.BankPermitSecret) < 32 || !validRoleDSN(c.AppDSN, "zygo_app", c.Environment+"_app") || !localOrigin(c.BankURL) || !localOrigin(c.WebOrigin) {
			return errors.New("application runtime must contain only application-role credentials and local bank/browser origins")
		}
	case "migration":
		if c.AppDSN != "" || c.BankDSN != "" || c.BankPermitSecret != "" || c.BankURL != "" || c.WebOrigin != "" || !validRoleDSN(c.MigrationDSN, "zygo_app_owner", c.Environment+"_app") {
			return errors.New("migration runtime must contain only the isolated schema-owner credential")
		}
	case "seed":
		if c.MigrationDSN != "" || !validRoleDSN(c.AppDSN, "zygo_app", c.Environment+"_app") || !validRoleDSN(c.BankDSN, "zygo_bank", c.Environment+"_bank") || len(c.BankPermitSecret) < 32 || c.SeedState == "" {
			return errors.New("seed runtime requires explicit isolated application/bank credentials and a persistent seed state path")
		}
	default:
		return errors.New("unsupported runtime role")
	}
	return nil
}

func validRoleDSN(raw, role, database string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.User == nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.User.Username() != role || strings.TrimPrefix(u.Path, "/") != database || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	ip := net.ParseIP(u.Hostname())
	return u.Hostname() == "localhost" || ip != nil && ip.IsLoopback()
}

func localOrigin(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "http" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.Port() == "" {
		return false
	}
	ip := net.ParseIP(u.Hostname())
	return u.Hostname() == "localhost" || ip != nil && ip.IsLoopback()
}

func (c Runtime) HTTPConfig() Config {
	address := c.ListenAddr
	if address == "" {
		address = "127.0.0.1:19310"
	}
	return Config{Environment: c.Environment, ListenAddr: address, DatabaseURL: c.AppDSN}
}
