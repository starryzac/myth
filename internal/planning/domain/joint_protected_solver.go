package domain

import (
	"sort"

	funds "zhiyu.local/zhiyu-go/internal/funds/domain"
)

// Protection intersects arbitrary subsets of goals at different checkpoints.
// Unlike the source Hall ranks, these intersections need not be a polymatroid.
// Therefore a greedy allocation is only an incumbent, never an optimality proof.
func jointProtectedFeasible(values []int64, points []JointProtectionPoint) bool {
	for _, point := range points {
		var occupied int64
		for i, term := range point.GoalTerms {
			m, err := funds.NewMoney(values[i])
			if err != nil {
				return false
			}
			q, err := term.Occupancy(m)
			if err != nil {
				return false
			}
			occupied += q.Cents()
		}
		if occupied > point.Available.Cents() {
			return false
		}
	}
	return true
}

func jointProtectedRegions(g jointGoal, terms []JointGoalProtection) []jointRegion {
	result := []jointRegion{}
	for _, base := range jointRegions(g) {
		cuts := []int64{base.low, base.high}
		for _, term := range terms {
			if !term.Active {
				continue
			}
			f := term.FutureMinimum.Cents()
			h := jointMax(term.CurrentMinimum.Cents()+f, term.Guarantee.Cents())
			if h >= term.Remaining.Cents() {
				continue
			}
			for _, cut := range []int64{h - f, term.Remaining.Cents() - f} {
				if cut > base.low && cut < base.high {
					cuts = append(cuts, cut)
				}
			}
		}
		sort.Slice(cuts, func(i, j int) bool { return cuts[i] < cuts[j] })
		unique := cuts[:0]
		for _, cut := range cuts {
			if len(unique) == 0 || unique[len(unique)-1] != cut {
				unique = append(unique, cut)
			}
		}
		if len(unique) == 1 {
			result = append(result, jointRegion{unique[0], unique[0]})
		} else {
			for i := 1; i < len(unique); i++ {
				result = append(result, jointRegion{unique[i-1], unique[i]})
			}
		}
	}
	return result
}

// Each q is affine (slope zero or one) inside a declared box. Merging its
// subset cap with the Hall caps is exact; no constant-cash relaxation is used.
func jointProtectedCaps(box []jointRegion, ranks []int64, points []JointProtectionPoint) ([]int64, error) {
	caps := append([]int64(nil), ranks...)
	for _, point := range points {
		mask := 0
		var constant int64
		for i, term := range point.GoalTerms {
			low, _ := funds.NewMoney(box[i].low)
			q, err := term.Occupancy(low)
			if err != nil {
				return nil, err
			}
			slope := int64(0)
			if box[i].high > box[i].low {
				next, _ := funds.NewMoney(box[i].low + 1)
				qn, e := term.Occupancy(next)
				if e != nil {
					return nil, e
				}
				slope = qn.Cents() - q.Cents()
				high, _ := funds.NewMoney(box[i].high)
				qh, e := term.Occupancy(high)
				if e != nil || (slope != 0 && slope != 1) || qh.Cents() != q.Cents()+slope*(box[i].high-box[i].low) {
					return nil, ErrJointInput
				}
			}
			constant += q.Cents() - slope*box[i].low
			if slope == 1 {
				mask |= 1 << i
			}
		}
		capacity := point.Available.Cents() - constant
		if capacity < 0 {
			return nil, nil
		}
		if mask != 0 {
			caps[mask] = jointMin(caps[mask], capacity)
		}
	}
	return caps, nil
}

func jointOptimumBetter(score [7]int64, values []int64, best *jointOptimum) bool {
	return best == nil || jointCompare(score[:], best.score[:]) < 0 || (score == best.score && jointCompare(values, best.values) < 0)
}

// Integer interval branch-and-bound. It splits ranges, not individual cents.
// Each objective component gets an optimistic bound from every packing cap:
// an independent unit-weight bounded knapsack relaxation. Componentwise lower
// bounds form a valid lexicographic lower bound even when jointly unattainable.
func jointProtectedBox(goals []jointGoal, box []jointRegion, caps []int64, today string, best **jointOptimum, counter *jointCounter) error {
	n := len(goals)
	baseValues := make([]int64, n)
	for i := range box {
		baseValues[i] = box[i].low
	}
	base := jointScore(goals, baseValues, today)
	deltas := make([][7]int64, n)
	for i := range box {
		if box[i].high > box[i].low {
			baseValues[i]++
			next := jointScore(goals, baseValues, today)
			baseValues[i]--
			for k := range base {
				deltas[i][k] = next[k] - base[k]
			}
		}
	}
	order := make([]int, n)
	for i := range order {
		order[i] = i
	}
	sort.Slice(order, func(i, j int) bool {
		cmp := jointCompare(deltas[order[i]][:], deltas[order[j]][:])
		if cmp != 0 {
			return cmp < 0
		}
		return order[i] > order[j]
	})
	benefitOrder := [7][]int{}
	for k := range benefitOrder {
		for i := range goals {
			if deltas[i][k] < 0 {
				benefitOrder[k] = append(benefitOrder[k], i)
			}
		}
		sort.Slice(benefitOrder[k], func(i, j int) bool { return deltas[benefitOrder[k][i]][k] < deltas[benefitOrder[k][j]][k] })
	}
	var search func([]jointRegion) error
	search = func(ranges []jointRegion) error {
		if err := counter.visit(); err != nil {
			return err
		}
		lo := make([]int64, n)
		for i := range ranges {
			lo[i] = ranges[i].low
		}
		if !jointFeasible(lo, caps) {
			return nil
		}
		// Tighten each upper bound using all others' mandatory lower bounds.
		for mask, cap := range caps {
			var mandatory int64
			for i, x := range lo {
				if mask&(1<<i) != 0 {
					mandatory += x
				}
			}
			for i := range ranges {
				if mask&(1<<i) != 0 {
					ranges[i].high = jointMin(ranges[i].high, cap-mandatory+lo[i])
				}
			}
		}
		optimistic := jointScore(goals, lo, today)
		for k, indices := range benefitOrder {
			var benefit int64
			for _, i := range indices {
				benefit += -deltas[i][k] * (ranges[i].high - lo[i])
			}
			for mask, cap := range caps {
				var used, bound int64
				for i, x := range lo {
					if mask&(1<<i) != 0 {
						used += x
					}
				}
				slack := cap - used
				for _, i := range indices {
					width := ranges[i].high - lo[i]
					if mask&(1<<i) != 0 {
						width = jointMin(width, slack)
						slack -= width
					}
					bound += -deltas[i][k] * width
				}
				benefit = jointMin(benefit, bound)
			}
			optimistic[k] -= benefit
		}
		if *best != nil && !jointOptimumBetter(optimistic, lo, *best) {
			return nil
		}
		// A deterministic greedy feasible point improves pruning only. Search
		// still proves optimality or exhausts its budget with no public amounts.
		candidate := append([]int64(nil), lo...)
		for _, i := range order {
			if jointCompare(deltas[i][:], make([]int64, 7)) >= 0 {
				continue
			}
			hi := ranges[i].high
			for mask, cap := range caps {
				if mask&(1<<i) == 0 {
					continue
				}
				var others int64
				for j, x := range candidate {
					if j != i && mask&(1<<j) != 0 {
						others += x
					}
				}
				hi = jointMin(hi, cap-others)
			}
			candidate[i] = hi
		}
		score := jointScore(goals, candidate, today)
		if jointOptimumBetter(score, candidate, *best) {
			*best = &jointOptimum{score, candidate}
		}
		if !jointOptimumBetter(optimistic, lo, *best) {
			return nil
		}
		branch := -1
		for _, i := range order {
			if ranges[i].high > ranges[i].low {
				branch = i
				break
			}
		}
		if branch < 0 {
			return nil
		}
		mid := ranges[branch].low + (ranges[branch].high-ranges[branch].low)/2
		lower, upper := append([]jointRegion(nil), ranges...), append([]jointRegion(nil), ranges...)
		lower[branch].high, upper[branch].low = mid, mid+1
		if jointCompare(deltas[branch][:], make([]int64, 7)) < 0 {
			lower, upper = upper, lower
		}
		if err := search(lower); err != nil {
			return err
		}
		return search(upper)
	}
	return search(append([]jointRegion(nil), box...))
}

func jointBestProtected(lots []funds.IncomeLot, goals []jointGoal, edges map[jointEdge]bool, budget int64, today string, points []JointProtectionPoint, counter *jointCounter) (*jointOptimum, error) {
	ranks, err := jointRanks(lots, goals, edges, budget, counter)
	if err != nil {
		return nil, err
	}
	lower, targets := make([]int64, len(goals)), make([]int64, len(goals))
	targetValid := true
	for i, goal := range goals {
		if goal.low > goal.high {
			return nil, nil
		}
		lower[i], targets[i] = goal.low, goal.target
		if targets[i] < goal.low || targets[i] > goal.high {
			targetValid = false
		}
	}
	if !jointFeasible(lower, ranks) || !jointProtectedFeasible(lower, points) {
		return nil, nil
	}
	if targetValid && jointFeasible(targets, ranks) && jointProtectedFeasible(targets, points) && jointScore(goals, targets, today) == ([7]int64{}) {
		return &jointOptimum{values: targets}, nil
	}
	regions := make([][]jointRegion, len(goals))
	for i, goal := range goals {
		terms := make([]JointGoalProtection, len(points))
		for j, point := range points {
			terms[j] = point.GoalTerms[i]
		}
		regions[i] = jointProtectedRegions(goal, terms)
	}
	var best *jointOptimum
	box := make([]jointRegion, len(goals))
	var visit func(int) error
	visit = func(index int) error {
		if err := counter.visit(); err != nil {
			return err
		}
		if index < len(goals) {
			for _, region := range regions[index] {
				box[index] = region
				if err := visit(index + 1); err != nil {
					return err
				}
			}
			return nil
		}
		caps, err := jointProtectedCaps(box, ranks, points)
		if err != nil || caps == nil {
			return err
		}
		return jointProtectedBox(goals, box, caps, today, &best, counter)
	}
	err = visit(0)
	return best, err
}
