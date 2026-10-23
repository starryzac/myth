// Package httpapi owns the HTTP transport and derives identity from sessions.
package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	assistant "zhiyu.local/zhiyu-go/internal/agent"
	agentdomain "zhiyu.local/zhiyu-go/internal/agent/domain"
	"zhiyu.local/zhiyu-go/internal/app"
	history "zhiyu.local/zhiyu-go/internal/history/domain"
	"zhiyu.local/zhiyu-go/internal/platform"
)

type Error struct {
	Code        string            `json:"code"`
	Message     string            `json:"message"`
	RequestID   string            `json:"request_id"`
	RetryClass  string            `json:"retry_class"`
	OperationID *string           `json:"operation_id"`
	Details     map[string]string `json:"details"`
}

func Handler(c platform.Config, logger *slog.Logger) http.Handler {
	return handler(c, logger, nil, "", nil)
}

func HandlerWithService(c platform.Config, logger *slog.Logger, service *app.Service, webOrigin string) http.Handler {
	return handler(c, logger, service, webOrigin, nil)
}

func HandlerWithAgentProvider(c platform.Config, logger *slog.Logger, service *app.Service, webOrigin string, provider assistant.Provider, limits agentdomain.Limits) http.Handler {
	return handler(c, logger, service, webOrigin, &AgentOptions{provider, limits})
}

func handler(c platform.Config, logger *slog.Logger, service *app.Service, webOrigin string, options *AgentOptions) http.Handler {
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			var token [16]byte
			if _, err := rand.Read(token[:]); err != nil {
				http.Error(w, "request identity unavailable", http.StatusServiceUnavailable)
				return
			}
			id := hex.EncodeToString(token[:])
			w.Header().Set("X-Request-ID", id)
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("X-Content-Type-Options", "nosniff")
			maximum := int64(64 << 10)
			if req.Method == "POST" && req.URL.Path == "/api/v2/history-archives" {
				maximum = history.MaxWireBytes
			}
			if req.Method == "POST" && (req.URL.Path == "/api/v2/history-import-previews" || req.URL.Path == "/api/v2/history-imports") {
				maximum = 8 << 20
			}
			req.Body = http.MaxBytesReader(w, req.Body, maximum)
			started := time.Now()
			defer func() {
				logger.Info("http_request", "request_id", id, "method", req.Method, "duration_ms", time.Since(started).Milliseconds())
			}()
			next.ServeHTTP(w, req)
		})
	})
	r.Get("/livez", func(w http.ResponseWriter, req *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "alive", "simulation": true})
	})
	r.Get("/readyz", func(w http.ResponseWriter, req *http.Request) {
		if service != nil {
			if err := service.Check(req.Context()); err == nil {
				writeJSON(w, http.StatusOK, map[string]any{"status": "ready", "stage": "R2_INTEGRATION", "simulation": true})
				return
			}
			writeJSON(w, http.StatusServiceUnavailable, Error{Code: "NOT_READY", Message: "数据库或独立模拟银行尚未就绪", RequestID: w.Header().Get("X-Request-ID"), RetryClass: "AFTER_CONFIGURATION", Details: map[string]string{}})
			return
		}
		writeJSON(w, http.StatusServiceUnavailable, Error{Code: "NOT_READY", Message: "R1 foundation: database migrations and financial execution are not enabled", RequestID: w.Header().Get("X-Request-ID"), RetryClass: "AFTER_CONFIGURATION", Details: map[string]string{"stage": "R1_FOUNDATION"}})
	})
	r.Get("/api/v2/status", func(w http.ResponseWriter, req *http.Request) {
		if service != nil {
			writeJSON(w, http.StatusOK, map[string]any{"environment": c.Environment, "stage": "R2_INTEGRATION", "simulation": true, "financial_operations_enabled": true, "verification_scope": "INTEGRATION_NOT_RELEASE"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"environment": c.Environment, "stage": "R1_FOUNDATION", "simulation": true, "financial_operations_enabled": false, "database_configured": c.DatabaseURL != "", "database_verified": false})
	})
	if service != nil {
		financialRoutes(r, service, webOrigin, options)
	}
	r.NotFound(func(w http.ResponseWriter, req *http.Request) {
		writeJSON(w, http.StatusNotFound, Error{Code: "NOT_FOUND", Message: "Route is not available", RequestID: w.Header().Get("X-Request-ID"), RetryClass: "NEVER", Details: map[string]string{}})
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, req *http.Request) {
		writeJSON(w, http.StatusMethodNotAllowed, Error{Code: "METHOD_NOT_ALLOWED", Message: "Method is not available", RequestID: w.Header().Get("X-Request-ID"), RetryClass: "NEVER", Details: map[string]string{}})
	})
	return r
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func Server(c platform.Config, logger *slog.Logger) *http.Server {
	return &http.Server{Addr: c.ListenAddr, Handler: Handler(c, logger), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 << 10}
}
