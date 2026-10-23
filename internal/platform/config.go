// Package platform contains configuration and process infrastructure, not financial rules.
package platform

import (
	"fmt"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type Config struct {
	Environment string
	ListenAddr  string
	DatabaseURL string
}

var environmentPattern = regexp.MustCompile(`^zygo_[a-z][a-z0-9_]{0,39}$`)

// Load requires an explicit isolated simulation environment. No legacy .env is read.
func Load(getenv func(string) string) (Config, error) {
	c := Config{Environment: getenv("ZYGO_ENVIRONMENT"), ListenAddr: getenv("ZYGO_LISTEN_ADDR"), DatabaseURL: getenv("ZYGO_DATABASE_URL")}
	if getenv("ZYGO_SIMULATION_MODE") != "true" {
		return Config{}, fmt.Errorf("ZYGO_SIMULATION_MODE must explicitly equal true")
	}
	if !environmentPattern.MatchString(c.Environment) {
		return Config{}, fmt.Errorf("ZYGO_ENVIRONMENT must be an isolated zygo_ name")
	}
	if c.ListenAddr == "" {
		c.ListenAddr = "127.0.0.1:19310"
	}
	host, port, err := net.SplitHostPort(c.ListenAddr)
	portNumber, portError := strconv.Atoi(port)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() || portError != nil || portNumber < 1 || portNumber > 65535 || strconv.Itoa(portNumber) != port {
		return Config{}, fmt.Errorf("ZYGO_LISTEN_ADDR must have a literal loopback IP and nonzero port")
	}
	if c.DatabaseURL != "" {
		u, err := url.Parse(c.DatabaseURL)
		if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" {
			return Config{}, fmt.Errorf("ZYGO_DATABASE_URL must be a PostgreSQL URL; value omitted")
		}
		ip := net.ParseIP(u.Hostname())
		if (ip == nil || !ip.IsLoopback()) && u.Hostname() != "localhost" {
			return Config{}, fmt.Errorf("ZYGO_DATABASE_URL must use a loopback host")
		}
		if strings.TrimPrefix(u.Path, "/") != c.Environment+"_app" || u.RawQuery != "" || u.Fragment != "" {
			return Config{}, fmt.Errorf("ZYGO_DATABASE_URL database must equal ZYGO_ENVIRONMENT + _app, with no query or fragment")
		}
	}
	return c, nil
}
