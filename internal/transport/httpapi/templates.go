package httpapi

import (
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"zhiyu.local/zhiyu-go/internal/app"
	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

func templateRoutes(r chi.Router, s *app.Service) {
	principal := func(req *http.Request) app.Principal { return req.Context().Value(principalKey{}).(app.Principal) }
	r.Get("/api/v2/template-policy-versions/{version_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "version_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		v, err := s.TemplatePolicyVersion(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/goals", func(w http.ResponseWriter, req *http.Request) {
		var in app.GoalConfigurationRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.CreateGoal(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, v)
	})
	r.Get("/api/v2/goals/{goal_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "goal_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		v, err := s.Goal(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/goals/{goal_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "goal_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		var in app.GoalConfigurationRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.UpdateGoal(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), id, in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Get("/api/v2/template-policies", func(w http.ResponseWriter, req *http.Request) {
		v, err := s.TemplatePolicies(req.Context(), principal(req))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/template-policy-previews", func(w http.ResponseWriter, req *http.Request) {
		var in app.TemplatePolicyRequest
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.PreviewTemplatePolicy(req.Context(), principal(req), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, v)
	})
	r.Post("/api/v2/template-policies", func(w http.ResponseWriter, req *http.Request) {
		var in app.TemplatePolicyConfirmation
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.ConfirmTemplatePolicy(req.Context(), principal(req), req.Header.Get("Idempotency-Key"), in)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 201, v)
	})
	r.Get("/api/v2/template-policies/{policy_id}", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "policy_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		v, err := s.TemplatePolicy(req.Context(), principal(req), id)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/template-policies/{policy_id}/state", func(w http.ResponseWriter, req *http.Request) {
		id, err := funds.ParseID(chi.URLParam(req, "policy_id"))
		if err != nil {
			fail(w, "INVALID_ID")
			return
		}
		var in struct {
			ExpectedVersionID funds.ID `json:"expected_version_id"`
			Action            string   `json:"action"`
		}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.SetTemplatePolicyState(req.Context(), principal(req), id, in.ExpectedVersionID, in.Action)
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Get("/api/v2/forecast", func(w http.ResponseWriter, req *http.Request) {
		query := req.URL.Query()
		for key, values := range query {
			if (key != "horizon_days" && key != "timezone") || len(values) != 1 {
				fail(w, "INVALID_COMMAND")
				return
			}
		}
		days := 365
		if raw := query.Get("horizon_days"); raw != "" {
			var err error
			days, err = strconv.Atoi(raw)
			if err != nil || days < 0 || days > 365 {
				fail(w, "INVALID_COMMAND")
				return
			}
		}
		v, err := s.Forecast(req.Context(), principal(req), app.ForecastRequest{HorizonDays: days, Timezone: query.Get("timezone")})
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
	r.Post("/api/v2/forecast/refresh-protection", func(w http.ResponseWriter, req *http.Request) {
		var in struct{}
		if body(req, &in) != nil {
			fail(w, "INVALID_JSON")
			return
		}
		v, err := s.RefreshTemplateProtection(req.Context(), principal(req))
		if err != nil {
			fail(w, app.Code(err))
			return
		}
		writeJSON(w, 200, v)
	})
}
