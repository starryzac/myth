package httpapi

import (
	"github.com/go-chi/chi/v5"
	"io"
	"mime"
	"net/http"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	history "zhiyu.local/zhiyu-go/internal/history/domain"
)

type historyPreviewRequest struct {
	ArchiveID funds.ID               `json:"archive_id"`
	Entries   []history.MappingEntry `json:"entries"`
}
type historyStepRequest struct {
	Batch int `json:"batch"`
}
type historyPausedRequest struct {
	Paused *bool `json:"paused"`
}

func historyRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	id := func(w http.ResponseWriter, req *http.Request) (funds.ID, bool) {
		v, e := funds.ParseID(chi.URLParam(req, "id"))
		if e != nil {
			fail(w, "INVALID_ID")
			return v, false
		}
		return v, true
	}
	respond := func(w http.ResponseWriter, status int, v any, e error) {
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, status, v)
	}
	r.Get("/api/v2/history-archives", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.ListHistoryArchives(req.Context(), principal(req))
		respond(w, 200, v, e)
	})
	r.Post("/api/v2/history-archives", func(w http.ResponseWriter, req *http.Request) {
		media, _, e := mime.ParseMediaType(req.Header.Get("Content-Type"))
		if e != nil || media != "application/vnd.zhiyu.history+json" {
			fail(w, "INVALID_JSON")
			return
		}
		raw, e := io.ReadAll(req.Body)
		if e != nil || len(raw) == 0 || len(raw) > history.MaxWireBytes {
			fail(w, "HISTORY_CAPACITY_EXCEEDED")
			return
		}
		v, e := s.CreateHistoryArchive(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), app.HistoryArchiveRequest{Bundle: raw})
		respond(w, 201, v, e)
	})
	r.Post("/api/v2/history-captures", func(w http.ResponseWriter, req *http.Request) {
		var in struct{}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, e := s.CaptureHistoryCurrent(req.Context(), principal(req), req.Header.Get("Idempotency-Key"))
		respond(w, 201, v, e)
	})
	r.Get("/api/v2/history-archives/{id}", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		out, e := s.HistoryArchive(req.Context(), principal(req), v)
		respond(w, 200, out, e)
	})
	r.Get("/api/v2/history-archive-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.HistoryArchiveByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		respond(w, 200, v, e)
	})
	r.Get("/api/v2/history-archives/{id}/export", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		raw, e := s.ExportHistoryArchive(req.Context(), principal(req), v)
		if e != nil {
			fail(w, app.Code(e))
			return
		}
		w.Header().Set("Content-Type", "application/vnd.zhiyu.history+json")
		w.Header().Set("Content-Disposition", `attachment; filename="zhiyu-history-`+v.String()+`.json"`)
		w.WriteHeader(200)
		_, _ = w.Write(raw)
	})
	r.Post("/api/v2/history-import-previews", func(w http.ResponseWriter, req *http.Request) {
		var in historyPreviewRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, e := s.PreviewHistoryImport(req.Context(), principal(req), in.ArchiveID, in.Entries)
		respond(w, 200, v, e)
	})
	r.Get("/api/v2/history-imports", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.ListHistoryImports(req.Context(), principal(req))
		respond(w, 200, v, e)
	})
	r.Post("/api/v2/history-imports", func(w http.ResponseWriter, req *http.Request) {
		var in app.HistoryImportRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, e := s.PrepareHistoryImport(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		respond(w, 201, v, e)
	})
	r.Get("/api/v2/history-imports/{id}", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		out, e := s.HistoryImport(req.Context(), principal(req), v)
		respond(w, 200, out, e)
	})
	r.Get("/api/v2/history-import-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.HistoryImportByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		respond(w, 200, v, e)
	})
	r.Post("/api/v2/history-imports/{id}/step", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		var in historyStepRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		out, e := s.HistoryImportStep(req.Context(), principal(req), v, in.Batch)
		respond(w, 200, out, e)
	})
	r.Post("/api/v2/history-imports/{id}/state", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		var in historyPausedRequest
		if body(req, &in) != nil || in.Paused == nil {
			fail(w, "INVALID_JSON")
			return
		}
		out, e := s.SetHistoryImportPaused(req.Context(), principal(req), v, *in.Paused)
		respond(w, 200, out, e)
	})
}
