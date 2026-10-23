package banksim

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	execution "zhiyu.local/zhiyu-go/internal/execution/domain"
)

type HandlerOptions struct {
	Clock func() time.Time
	// TestFaults must be explicitly enabled in an isolated test bank process.
	// Production/default handlers ignore fault injection headers.
	TestFaults bool
}

func Handler(store *Store, options HandlerOptions) http.Handler {
	clock := options.Clock
	if clock == nil {
		clock = time.Now
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := store.pool.Ping(r.Context()); err != nil {
			writeError(w, http.StatusServiceUnavailable, "BANK_DATABASE_UNAVAILABLE")
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"service": "zhiyu-simulated-bank", "simulation": true})
	})
	mux.HandleFunc("POST /v1/operations", func(w http.ResponseWriter, r *http.Request) {
		r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
		raw, err := io.ReadAll(r.Body)
		if err != nil || !uniqueJSON(raw) {
			writeError(w, http.StatusBadRequest, "INVALID_BANK_REQUEST")
			return
		}
		var request execution.BankRequest
		d := json.NewDecoder(bytes.NewReader(raw))
		d.DisallowUnknownFields()
		if err = d.Decode(&request); err != nil {
			writeError(w, http.StatusBadRequest, "INVALID_BANK_REQUEST")
			return
		}
		if request.Validate() != nil {
			writeError(w, http.StatusBadRequest, "INVALID_BANK_REQUEST")
			return
		}
		result, err := store.Submit(r.Context(), request, r.Header.Get("X-Zhiyu-Permit"), clock().UTC())
		if err != nil {
			bankError(w, err)
			return
		}
		if options.TestFaults && r.Header.Get("X-Zhiyu-Test-Fault") == "drop-after-commit" {
			if hijack, ok := w.(http.Hijacker); ok {
				connection, _, err := hijack.Hijack()
				if err == nil {
					connection.Close()
					return
				}
			}
			writeError(w, http.StatusServiceUnavailable, "TEST_RESPONSE_LOST_AFTER_COMMIT")
			return
		}
		writeJSON(w, http.StatusOK, result)
	})
	mux.HandleFunc("GET /v1/operations/{namespace}/{key}", func(w http.ResponseWriter, r *http.Request) {
		result, err := store.Lookup(r.Context(), r.PathValue("namespace"), r.URL.Query().Get("user_id"), r.PathValue("key"))
		if err != nil {
			bankError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, result)
	})
	mux.HandleFunc("POST /v1/operations/{namespace}/{key}/settle", func(w http.ResponseWriter, r *http.Request) {
		result, err := store.Settle(r.Context(), r.PathValue("namespace"), r.URL.Query().Get("user_id"), r.PathValue("key"), clock().UTC())
		if err != nil {
			bankError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, result)
	})
	mux.HandleFunc("GET /v1/operations", func(w http.ResponseWriter, r *http.Request) {
		query := r.URL.Query()
		after, errA := number(query.Get("after"), 0)
		watermark, errW := number(query.Get("watermark"), 0)
		limit, errL := number(query.Get("limit"), 100)
		if errA != nil || errW != nil || errL != nil {
			writeError(w, http.StatusBadRequest, "INVALID_INVENTORY_BOUNDS")
			return
		}
		result, err := store.Inventory(r.Context(), query.Get("namespace"), query.Get("user_id"), after, watermark, int(limit))
		if err != nil {
			bankError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, result)
	})
	return mux
}

func number(value string, fallback int64) (int64, error) {
	if value == "" {
		return fallback, nil
	}
	return strconv.ParseInt(value, 10, 64)
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func writeError(w http.ResponseWriter, status int, code string) {
	writeJSON(w, status, map[string]string{"error": code})
}
func bankError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrConflict):
		writeError(w, http.StatusConflict, ErrConflict.Error())
	case errors.Is(err, ErrUnauthorized):
		writeError(w, http.StatusUnauthorized, ErrUnauthorized.Error())
	case errors.Is(err, ErrNotFound):
		writeError(w, http.StatusNotFound, ErrNotFound.Error())
	case errors.Is(err, ErrNotDue), errors.Is(err, ErrPendingOrder):
		writeError(w, http.StatusConflict, err.Error())
	default:
		writeError(w, http.StatusInternalServerError, "BANK_PROCESSING_ERROR")
	}
}

// Reject duplicate keys rather than accept encoding/json's last-key-wins
// interpretation before hashing the original request.
func uniqueJSON(raw []byte) bool {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	nodes := 0
	var consume func(int) bool
	consume = func(depth int) bool {
		nodes++
		if depth > 16 || nodes > 1000 {
			return false
		}
		token, err := d.Token()
		if err != nil {
			return false
		}
		switch token {
		case json.Delim('{'):
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return false
				}
				s, ok := key.(string)
				if !ok || seen[s] {
					return false
				}
				seen[s] = true
				if !consume(depth + 1) {
					return false
				}
			}
			end, err := d.Token()
			return err == nil && end == json.Delim('}')
		case json.Delim('['):
			for d.More() {
				if !consume(depth + 1) {
					return false
				}
			}
			end, err := d.Token()
			return err == nil && end == json.Delim(']')
		default:
			return true
		}
	}
	if !consume(0) {
		return false
	}
	_, err := d.Token()
	return err == io.EOF
}

type Client struct {
	base string
	http *http.Client
}

func NewClient(base string, httpClient *http.Client) (*Client, error) {
	u, err := url.Parse(base)
	if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return nil, errors.New("bank base URL must be a local HTTP origin")
	}
	ip := net.ParseIP(u.Hostname())
	if u.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return nil, errors.New("bank HTTP origin must be loopback")
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 5 * time.Second}
	}
	copyClient := *httpClient
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return errors.New("bank redirects are forbidden") }
	return &Client{base: strings.TrimSuffix(base, "/"), http: &copyClient}, nil
}

func (c *Client) Submit(ctx context.Context, request execution.BankRequest, signature string) (execution.BankResult, error) {
	return c.SubmitWithTestFault(ctx, request, signature, "")
}

func (c *Client) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/healthz", nil)
	if err != nil {
		return err
	}
	var result struct {
		Service    string `json:"service"`
		Simulation bool   `json:"simulation"`
	}
	if err = c.do(req, &result); err != nil {
		return err
	}
	if result.Service != "zhiyu-simulated-bank" || !result.Simulation {
		return errors.New("unexpected bank health identity")
	}
	return nil
}
func (c *Client) SubmitWithTestFault(ctx context.Context, request execution.BankRequest, signature, fault string) (execution.BankResult, error) {
	var result execution.BankResult
	raw, err := request.CanonicalBytes()
	if err != nil {
		return result, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/v1/operations", bytes.NewReader(raw))
	if err != nil {
		return result, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Zhiyu-Permit", signature)
	if fault != "" {
		req.Header.Set("X-Zhiyu-Test-Fault", fault)
	}
	err = c.do(req, &result)
	return result, err
}

func (c *Client) Lookup(ctx context.Context, namespace, user, key string) (execution.BankResult, error) {
	var result execution.BankResult
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.operationURL(namespace, user, key), nil)
	if err != nil {
		return result, err
	}
	err = c.do(req, &result)
	return result, err
}
func (c *Client) Settle(ctx context.Context, namespace, user, key string) (execution.BankResult, error) {
	var result execution.BankResult
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/v1/operations/"+url.PathEscape(namespace)+"/"+url.PathEscape(key)+"/settle?"+url.Values{"user_id": {user}}.Encode(), nil)
	if err != nil {
		return result, err
	}
	err = c.do(req, &result)
	return result, err
}
func (c *Client) Inventory(ctx context.Context, namespace, user string, after, watermark int64, limit int) (execution.BankInventory, error) {
	var result execution.BankInventory
	query := url.Values{"namespace": {namespace}, "user_id": {user}, "after": {strconv.FormatInt(after, 10)}, "watermark": {strconv.FormatInt(watermark, 10)}, "limit": {strconv.Itoa(limit)}}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/v1/operations?"+query.Encode(), nil)
	if err != nil {
		return result, err
	}
	err = c.do(req, &result)
	return result, err
}
func (c *Client) operationURL(namespace, user, key string) string {
	return c.base + "/v1/operations/" + url.PathEscape(namespace) + "/" + url.PathEscape(key) + "?" + url.Values{"user_id": {user}}.Encode()
}
func (c *Client) do(request *http.Request, result any) error {
	response, err := c.http.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		var body struct {
			Error string `json:"error"`
		}
		_ = json.NewDecoder(io.LimitReader(response.Body, 8192)).Decode(&body)
		switch body.Error {
		case ErrConflict.Error():
			return ErrConflict
		case ErrUnauthorized.Error():
			return ErrUnauthorized
		case ErrNotDue.Error():
			return ErrNotDue
		case ErrNotFound.Error():
			return ErrNotFound
		case ErrPendingOrder.Error():
			return ErrPendingOrder
		}
		return fmt.Errorf("bank HTTP %d", response.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(response.Body, 2*1024*1024)).Decode(result)
}
