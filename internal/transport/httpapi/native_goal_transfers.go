package httpapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeGoalTransferRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Post("/api/v2/goal-transfer-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeGoalTransferPreviewRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.PreviewNativeGoalTransfer(req.Context(), principal(req), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, value)
	})
	r.Get("/api/v2/goal-transfer-previews/{preview_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "preview_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativeGoalTransferPreview(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Post("/api/v2/goal-transfers", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeGoalTransferCommand
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		value, err := s.AcceptNativeGoalTransfer(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 202, value)
	})
	r.Get("/api/v2/goal-transfers/{operation_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "operation_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		value, err := s.NativeGoalTransferOperation(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
	r.Get("/api/v2/goal-transfer-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		value, err := s.NativeGoalTransferByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, value)
	})
}
