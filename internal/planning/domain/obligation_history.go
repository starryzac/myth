package domain

import (
	"errors"
	"sort"
	"time"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
	policy "zhiyu.local/zhiyu-go/internal/policy/domain"
)

const HistoricalObligationAlgorithm = "confirmed-obligation-history-go-v1"

var ErrObligationHistory = errors.New("OBLIGATION_HISTORY_INCOMPLETE")

// All provenance is supplied by a complete scoped native-registry read, never
// an HTTP command. Historical versions keep their real Current=false value.
type HistoricalPolicyEvent struct {
	ID         funds.ID  `json:"id"`
	VersionID  funds.ID  `json:"version_id"`
	Sequence   uint64    `json:"sequence"`
	Action     string    `json:"action"`
	OldState   string    `json:"old_state"`
	NewState   string    `json:"new_state"`
	ActorKind  string    `json:"actor_kind"`
	At         time.Time `json:"at"`
	Verified   bool      `json:"verified"`
	SourceHash string    `json:"source_hash"`
}
type HistoricalBillFact struct {
	Bill            policy.TemplateBillFact `json:"bill"`
	FirstObservedAt time.Time               `json:"first_observed_at"`
	SourceHash      string                  `json:"source_hash"`
}
type HistoricalObligationInput struct {
	Scope         policy.TemplateOwnerScope `json:"scope"`
	AsOf          time.Time                 `json:"as_of"`
	Timezone      string                    `json:"timezone"`
	Complete      bool                      `json:"complete"`
	InventoryHash string                    `json:"inventory_hash"`
	Versions      []policy.TemplateVersion  `json:"versions"`
	Events        []HistoricalPolicyEvent   `json:"events"`
	// Amount is the already verified current REMAINING amount, including
	// explicit zero-paid-off records. It must not have payments subtracted twice.
	Registered  []ForecastObligation            `json:"registered"`
	Settlements []policy.TemplateSettlementFact `json:"settlements"`
	Bills       []HistoricalBillFact            `json:"bills"`
}
type HistoricalObligationAdoption struct {
	Original  ForecastObligation `json:"original"`
	Remaining funds.Money        `json:"remaining_cents"`
	AdoptedAt time.Time          `json:"adopted_at"`
	Period    string             `json:"period"`
	Kind      string             `json:"kind"`
}
type HistoricalObligationResult struct {
	Algorithm     string                         `json:"algorithm"`
	InputHash     string                         `json:"input_hash"`
	Obligations   []ForecastObligation           `json:"obligations"`
	Adoptions     []HistoricalObligationAdoption `json:"adoptions"`
	BankAuthority bool                           `json:"bank_authority"`
}
type historicalActiveInterval struct{ start, end time.Time }

// ExpandHistoricalObligations reproduces calendar adoption independently from
// process uptime. A month adopts at its FIRST actual ACTIVE instant if that
// version's due date has not passed and lies inside its confirmed validity.
// Later suspension, expiry or replacement never erases that adopted debt.
func ExpandHistoricalObligations(in HistoricalObligationInput) (HistoricalObligationResult, error) {
	result, err := expandHistoricalObligations(in)
	if err != nil {
		return HistoricalObligationResult{Algorithm: HistoricalObligationAlgorithm, Obligations: []ForecastObligation{}, Adoptions: []HistoricalObligationAdoption{}}, err
	}
	return result, nil
}

func expandHistoricalObligations(in HistoricalObligationInput) (HistoricalObligationResult, error) {
	r := HistoricalObligationResult{Algorithm: HistoricalObligationAlgorithm, Obligations: []ForecastObligation{}, Adoptions: []HistoricalObligationAdoption{}}
	zone, err := policy.TemplateLocation(in.Timezone)
	if err != nil || in.AsOf.IsZero() || in.AsOf.Year() < 1 || in.AsOf.Year() > 9998 || !in.Complete || !validHash(in.InventoryHash) || in.Scope.EnvironmentID == (funds.ID{}) || in.Scope.UserID == (funds.ID{}) {
		return r, ErrObligationHistory
	}
	if len(in.Versions) > 1000 || len(in.Events) > 10000 || len(in.Registered) > 10000 || len(in.Settlements) > 10000 || len(in.Bills) > 10000 {
		return r, ErrForecastCapacity
	}
	in.AsOf = in.AsOf.UTC()
	in.Versions = append([]policy.TemplateVersion(nil), in.Versions...)
	in.Events = append([]HistoricalPolicyEvent(nil), in.Events...)
	in.Registered = append([]ForecastObligation(nil), in.Registered...)
	in.Settlements = append([]policy.TemplateSettlementFact(nil), in.Settlements...)
	in.Bills = append([]HistoricalBillFact(nil), in.Bills...)
	for i := range in.Versions {
		in.Versions[i].ConfirmedAt = in.Versions[i].ConfirmedAt.UTC()
	}
	for i := range in.Events {
		in.Events[i].At = in.Events[i].At.UTC()
	}
	for i := range in.Bills {
		in.Bills[i].FirstObservedAt = in.Bills[i].FirstObservedAt.UTC()
	}
	sort.Slice(in.Versions, func(i, j int) bool {
		a, b := in.Versions[i], in.Versions[j]
		if a.PolicyID != b.PolicyID {
			return a.PolicyID.String() < b.PolicyID.String()
		}
		return a.Version < b.Version
	})
	sort.Slice(in.Events, func(i, j int) bool {
		a, b := in.Events[i], in.Events[j]
		if a.VersionID != b.VersionID {
			return a.VersionID.String() < b.VersionID.String()
		}
		return a.Sequence < b.Sequence
	})
	sort.Slice(in.Registered, func(i, j int) bool { return in.Registered[i].ID < in.Registered[j].ID })
	sort.Slice(in.Settlements, func(i, j int) bool {
		a, b := in.Settlements[i], in.Settlements[j]
		if a.PolicyID != b.PolicyID {
			return a.PolicyID.String() < b.PolicyID.String()
		}
		return a.Period < b.Period
	})
	sort.Slice(in.Bills, func(i, j int) bool { return in.Bills[i].Bill.ID.String() < in.Bills[j].Bill.ID.String() })
	r.InputHash = hash(in)
	if r.InputHash == "" {
		return r, ErrObligationHistory
	}
	versions := map[funds.ID]policy.TemplateVersion{}
	for i, v := range in.Versions {
		if v.Scope != in.Scope || v.PolicyID == (funds.ID{}) || v.VersionID == (funds.ID{}) || v.Version == 0 || versions[v.VersionID].Version != 0 || !v.ConfirmationVerified || v.ConfirmedConfigurationHash != v.Compiled.Hash || v.ConfirmedAt.IsZero() || v.ConfirmedAt.After(in.AsOf) {
			return r, ErrObligationHistory
		}
		if _, e := v.Compiled.Decode(); e != nil {
			return r, ErrObligationHistory
		}
		first := i == 0 || in.Versions[i-1].PolicyID != v.PolicyID
		last := i+1 == len(in.Versions) || in.Versions[i+1].PolicyID != v.PolicyID
		if first && v.Version != 1 || !first && (v.Version != in.Versions[i-1].Version+1 || v.ConfirmedAt.Before(in.Versions[i-1].ConfirmedAt) || v.Compiled.Name != in.Versions[i-1].Compiled.Name) || v.Current != last {
			return r, ErrObligationHistory
		}
		versions[v.VersionID] = v
	}
	events := map[funds.ID][]HistoricalPolicyEvent{}
	eventIDs := map[funds.ID]bool{}
	for _, e := range in.Events {
		v, ok := versions[e.VersionID]
		prior := events[e.VersionID]
		if !ok || e.ID == (funds.ID{}) || eventIDs[e.ID] || !e.Verified || !validHash(e.SourceHash) || e.ActorKind != "USER" || e.At.Before(v.ConfirmedAt) || e.At.After(in.AsOf) || e.Sequence != uint64(len(prior)+1) {
			return r, ErrObligationHistory
		}
		eventIDs[e.ID] = true
		if len(prior) == 0 {
			if e.Action != "CONFIRM" || e.OldState != "" || e.NewState != "ACTIVE" || !e.At.Equal(v.ConfirmedAt) {
				return r, ErrObligationHistory
			}
		} else {
			old := prior[len(prior)-1]
			if e.At.Before(old.At) || e.OldState != old.NewState {
				return r, ErrObligationHistory
			}
			valid := e.Action == "PAUSE" && e.OldState == "ACTIVE" && e.NewState == "SUSPENDED" || e.Action == "RESUME" && e.OldState == "SUSPENDED" && e.NewState == "ACTIVE" || e.Action == "REVOKE" && (e.OldState == "ACTIVE" || e.OldState == "SUSPENDED") && e.NewState == "REVOKED"
			if !valid {
				return r, ErrObligationHistory
			}
		}
		events[e.VersionID] = append(prior, e)
	}
	settlements := map[string]policy.TemplateSettlementFact{}
	for _, s := range in.Settlements {
		key := s.PolicyID.String() + "/" + s.Period
		if s.PolicyID == (funds.ID{}) || s.Period == "" || !s.Verified || settlements[key].Verified || s.FinalTotal != nil && s.Paid.Cents() > s.FinalTotal.Cents() {
			return r, ErrObligationHistory
		}
		settlements[key] = s
	}
	known := map[string]ForecastObligation{}
	for _, o := range in.Registered {
		if o.ID == "" || o.PolicyID == (funds.ID{}) || o.VersionID == (funds.ID{}) || !o.Verified || !validHash(o.SourceHash) || known[o.ID].ID != "" {
			return r, ErrObligationHistory
		}
		if _, e := policy.TemplateDate(o.DueDate); e != nil {
			return r, ErrObligationHistory
		}
		known[o.ID] = o
		r.Obligations = append(r.Obligations, o)
	}
	billIDs := map[funds.ID]bool{}
	for _, b := range in.Bills {
		if !b.Bill.Verified || b.Bill.ID == (funds.ID{}) || b.Bill.AccountID == (funds.ID{}) || billIDs[b.Bill.ID] || b.Bill.Paid.Cents() > b.Bill.Total.Cents() || b.FirstObservedAt.IsZero() || b.FirstObservedAt.After(in.AsOf) || !validHash(b.SourceHash) {
			return r, ErrObligationHistory
		}
		if _, e := policy.TemplateDate(b.Bill.DueDate); e != nil {
			return r, ErrObligationHistory
		}
		billIDs[b.Bill.ID] = true
	}
	add := func(v policy.TemplateVersion, id, period, kind, due string, at time.Time, total, paid funds.Money) error {
		if old, ok := known[id]; ok {
			if old.PolicyID != v.PolicyID {
				return ErrObligationHistory
			}
			return nil
		}
		remaining, e := total.Sub(paid)
		if e != nil {
			return ErrObligationHistory
		}
		original := ForecastObligation{ID: id, PolicyID: v.PolicyID, VersionID: v.VersionID, DueDate: due, Amount: total, Verified: true, SourceHash: v.Compiled.Hash}
		current := original
		current.Amount = remaining
		known[id] = current
		r.Obligations = append(r.Obligations, current)
		r.Adoptions = append(r.Adoptions, HistoricalObligationAdoption{original, remaining, at.UTC(), period, kind})
		if len(r.Obligations) > 10000 {
			return ErrForecastCapacity
		}
		return nil
	}
	settle := func(v policy.TemplateVersion, id, period, kind, due string, at time.Time, total funds.Money) error {
		if old, ok := known[id]; ok {
			if old.PolicyID != v.PolicyID || kind == "MONTHLY" && old.DueDate[:7] != period {
				return ErrObligationHistory
			}
			return nil
		}
		s, ok := settlements[v.PolicyID.String()+"/"+period]
		if !ok {
			return ErrObligationHistory
		}
		if s.FinalTotal != nil {
			total = *s.FinalTotal
		}
		return add(v, id, period, kind, due, at, total, s.Paid)
	}
	for i, v := range in.Versions {
		rows := events[v.VersionID]
		if len(rows) == 0 || rows[len(rows)-1].NewState != v.State {
			return r, ErrObligationHistory
		}
		validStart, validEnd, e := policy.TemplateValidity(v.Compiled, v.ConfirmedAt, in.Timezone)
		if e != nil {
			return r, ErrObligationHistory
		}
		cutoff := in.AsOf.Add(time.Nanosecond)
		if i+1 < len(in.Versions) && in.Versions[i+1].PolicyID == v.PolicyID {
			cutoff = in.Versions[i+1].ConfirmedAt
		}
		if rows[len(rows)-1].At.After(cutoff) {
			return r, ErrObligationHistory
		}
		intervals := []historicalActiveInterval{}
		for j, event := range rows {
			if event.NewState != "ACTIVE" {
				continue
			}
			start, end := event.At, cutoff
			if j+1 < len(rows) && rows[j+1].At.Before(end) {
				end = rows[j+1].At
			}
			if start.Before(validStart) {
				start = validStart
			}
			if validEnd != nil && validEnd.Before(end) {
				end = *validEnd
			}
			if start.Before(end) {
				intervals = append(intervals, historicalActiveInterval{start, end})
			}
		}
		config, _ := v.Compiled.Decode()
		for _, interval := range intervals {
			startLocal := interval.start.In(zone)
			month := time.Date(startLocal.Year(), startLocal.Month(), 1, 0, 0, 0, 0, zone)
			for count := 0; month.Before(interval.end) && !month.After(in.AsOf); count++ {
				if count > 120 {
					return r, ErrForecastCapacity
				}
				adopt := month
				if adopt.Before(interval.start) {
					adopt = interval.start
				}
				var rule *policy.TemplateAmountRule
				dueDay := 0
				switch c := config.(type) {
				case *policy.RecurringObligationConfig:
					if c.AmountRule.Kind != "bill_balance" {
						rule = &c.AmountRule
						dueDay = c.DueDay
					}
				case *policy.PeriodicTransferConfig:
					rule = &c.AmountRule
					dueDay = c.DueDay
				}
				if rule != nil {
					due := policy.TemplateDueDate(month.Year(), month.Month(), dueDay)
					if due >= adopt.In(zone).Format("2006-01-02") && (validEnd == nil || due < validEnd.In(zone).Format("2006-01-02")) {
						period := month.Format("2006-01")
						amount := rule.Amount
						if rule.Kind == "range" {
							amount = rule.Max
						}
						if e := settle(v, v.PolicyID.String()+":monthly:"+period, period, "MONTHLY", due, adopt, amount); e != nil {
							return r, e
						}
					}
				}
				month = month.AddDate(0, 1, 0)
			}
			switch c := config.(type) {
			case *policy.DatedExpenseConfig:
				adopt := interval.start
				start, _ := forecastDay(c.Window.Start, zone)
				if adopt.Before(start) {
					adopt = start
				}
				if adopt.Before(interval.end) && !adopt.After(in.AsOf) {
					due := adopt.In(zone).Format("2006-01-02")
					if e := settle(v, v.PolicyID.String()+":dated:"+c.Window.Start, c.Window.Start+"/"+c.Window.End, "DATED", due, adopt, c.Amount.Max); e != nil {
						return r, e
					}
				}
			case *policy.RecurringObligationConfig:
				if c.AmountRule.Kind != "bill_balance" {
					continue
				}
				for _, b := range in.Bills {
					if b.Bill.AccountID != *c.AmountRule.AccountID {
						continue
					}
					adopt := interval.start
					if adopt.Before(b.FirstObservedAt) {
						adopt = b.FirstObservedAt
					}
					due, _ := forecastDay(b.Bill.DueDate, zone)
					if adopt.Before(due) {
						adopt = due
					}
					if adopt.Before(interval.end) && !adopt.After(in.AsOf) {
						if e := add(v, "bill:"+b.Bill.ID.String(), b.Bill.DueDate, "BILL", b.Bill.DueDate, adopt, b.Bill.Total, b.Bill.Paid); e != nil {
							return r, e
						}
					}
				}
			}
		}
	}
	sort.Slice(r.Obligations, func(i, j int) bool { return r.Obligations[i].ID < r.Obligations[j].ID })
	sort.Slice(r.Adoptions, func(i, j int) bool {
		if !r.Adoptions[i].AdoptedAt.Equal(r.Adoptions[j].AdoptedAt) {
			return r.Adoptions[i].AdoptedAt.Before(r.Adoptions[j].AdoptedAt)
		}
		return r.Adoptions[i].Original.ID < r.Adoptions[j].Original.ID
	})
	return r, nil
}
