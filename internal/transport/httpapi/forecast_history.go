package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
)

// The browser chooses an operator-provisioned source. All source authority and
// statement material stay behind the authenticated application boundary.
type ForecastHistorySourceRequest struct {
	SourceID string `json:"source_id"`
}

func forecastHistoryRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Get("/api/v2/forecast-history-sources", func(w http.ResponseWriter, req *http.Request) {
		v, err := s.ForecastHistorySourceInventory(req.Context(), principal(req))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, http.StatusOK, v)
	})
	r.Post("/api/v2/forecast-history-imports", func(w http.ResponseWriter, req *http.Request) {
		var in ForecastHistorySourceRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.ImportForecastHistory(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in.SourceID)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, http.StatusCreated, v)
	})
	r.Get("/api/v2/forecast-history-import-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		v, err := s.ForecastHistoryByKey(req.Context(), principal(req), chi.URLParam(req, "key"), req.URL.Query().Get("source_id"))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, http.StatusOK, v)
	})
}
