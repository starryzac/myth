package httpapi

import (
	"github.com/go-chi/chi/v5"
	"net/http"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func nativeGrantRoutes(r chi.Router, s *app.Service) {
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
	r.Get("/api/v2/automation-capabilities", func(w http.ResponseWriter, req *http.Request) { writeJSON(w, 200, app.NativeAutomationCapabilities()) })
	r.Post("/api/v2/standing-grant-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeStandingGrantRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, e := s.PreviewNativeStandingGrant(req.Context(), principal(req), in)
		respond(w, 201, v, e)
	})
	r.Get("/api/v2/standing-grant-previews/{id}", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		out, e := s.NativeGrantPreview(req.Context(), principal(req), v)
		respond(w, 200, out, e)
	})
	r.Get("/api/v2/standing-grants", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.ListNativeStandingGrants(req.Context(), principal(req))
		respond(w, 200, v, e)
	})
	r.Post("/api/v2/standing-grants", func(w http.ResponseWriter, req *http.Request) {
		var in app.NativeStandingGrantConfirmation
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, e := s.ConfirmNativeStandingGrant(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		respond(w, 201, v, e)
	})
	r.Get("/api/v2/standing-grants/{id}", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		out, e := s.NativeStandingGrant(req.Context(), principal(req), v)
		respond(w, 200, out, e)
	})
	r.Get("/api/v2/standing-grant-commands/{key}", func(w http.ResponseWriter, req *http.Request) {
		v, e := s.NativeGrantByKey(req.Context(), principal(req), chi.URLParam(req, "key"))
		respond(w, 200, v, e)
	})
	r.Post("/api/v2/standing-grants/{id}/state", func(w http.ResponseWriter, req *http.Request) {
		v, ok := id(w, req)
		if !ok {
			return
		}
		var in app.NativeGrantStateRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		out, e := s.SetNativeStandingGrantState(req.Context(), principal(req), v, in.ExpectedSequence, in.Action)
		respond(w, 200, out, e)
	})
	r.Post("/api/v2/income-intake/resume", func(w http.ResponseWriter, req *http.Request) {
		var in struct{}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		if e := s.ResumeNativeIncomeIntake(req.Context(), principal(req)); e != nil {
			fail(w, app.Code(e))
			return
		}
		writeJSON(w, 200, map[string]any{"status": "INCOME_OBSERVATION_RESUMED", "bank_authority": false})
	})
}
