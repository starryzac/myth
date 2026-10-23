// Package ownedpg contains test-fixture connection guards only. Runtime role,
// database, loopback, namespace and credential-epoch checks stay with callers.
package ownedpg

import (
	"os"
	"runtime"
)

// AllowedPort permits the owned host PG port, or its container-internal port
// only for an explicitly identified Linux test container. It grants no access
// and is never a substitute for the caller's other identity checks.
func AllowedPort(port uint16) bool {
	_, err := os.Stat("/.dockerenv")
	return allowedPort(port, runtime.GOOS, os.Getenv("ZHIYU_TEST_CONTAINER_PG"), err == nil)
}

func allowedPort(port uint16, goos, marker string, dockerEnv bool) bool {
	return port == 55439 || port == 5432 && goos == "linux" && marker == "zhiyu-go-r1-postgres" && dockerEnv
}
