package banksim

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	bankdb "zhiyu.local/zhiyu-go/db/bank"
	bankv2 "zhiyu.local/zhiyu-go/internal/execution/bankv2"
)

// Filled only after the DDL owner freezes and reports the exact source hash.
const frozenBankV2Migration = "86959f6451256411417daa69d3ba7a08820cbf54c48fd47a2d4f0b343677767a"

func (s *Store) CheckV2(ctx context.Context) error {
	var hash string
	if err := s.pool.QueryRow(ctx, "SELECT sha256 FROM bank.protocol_migrations WHERE version=2").Scan(&hash); err != nil || hash != frozenBankV2Migration {
		return errors.New("bank v2 schema is unavailable or differs from freeze")
	}
	return nil
}

func (s *Store) MigrateV2(ctx context.Context) error {
	if frozenBankV2Migration == "" {
		return errors.New("bank v2 migration has not been frozen")
	}
	h := sha256.Sum256([]byte(bankdb.MultiAssetSchema))
	if hex.EncodeToString(h[:]) != frozenBankV2Migration {
		return errors.New("bank v2 migration source differs from freeze")
	}
	if err := s.Migrate(ctx); err != nil {
		return err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended('zhiyu:bank:v2:migration',0))"); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `CREATE TABLE IF NOT EXISTS bank.protocol_migrations(version integer PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT clock_timestamp())`); err != nil {
		return err
	}
	var old string
	err = tx.QueryRow(ctx, "SELECT sha256 FROM bank.protocol_migrations WHERE version=2").Scan(&old)
	if err == nil {
		if old != frozenBankV2Migration {
			return errors.New("bank v2 applied migration differs from freeze")
		}
		return tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if _, err = tx.Exec(ctx, bankdb.MultiAssetSchema); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.protocol_migrations(version,sha256) VALUES(2,$1)", frozenBankV2Migration); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// PublishProductV2 is a trusted local catalog import, never an HTTP action.
// Terms and availability versions are append-only independently of user state.
func (s *Store) PublishProductV2(ctx context.Context, namespace string, product bankv2.ProductSnapshot) error {
	if !validIdentity(namespace, product.Terms.ProductID) || product.Validate() != nil {
		return bankv2.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,2))", namespace+"/catalog"); err != nil {
		return err
	}
	termsRaw, _ := json.Marshal(product.Terms)
	availabilityRaw, _ := json.Marshal(product.Availability)
	if _, err = tx.Exec(ctx, `INSERT INTO bank.v2_product_terms(namespace,product_id,version,original,original_hash) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING`, namespace, product.Terms.ProductID, product.Terms.Version, termsRaw, product.TermsHash); err != nil {
		return err
	}
	var existing string
	if err = tx.QueryRow(ctx, "SELECT original_hash FROM bank.v2_product_terms WHERE namespace=$1 AND product_id=$2 AND version=$3", namespace, product.Terms.ProductID, product.Terms.Version).Scan(&existing); err != nil {
		return err
	}
	if existing != product.TermsHash {
		return ErrConflict
	}
	if _, err = tx.Exec(ctx, `INSERT INTO bank.v2_product_availability(namespace,product_id,terms_version,version,original,original_hash) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING`, namespace, product.Terms.ProductID, product.Terms.Version, product.Availability.Version, availabilityRaw, product.AvailabilityHash); err != nil {
		return err
	}
	if err = tx.QueryRow(ctx, "SELECT original_hash FROM bank.v2_product_availability WHERE namespace=$1 AND product_id=$2 AND terms_version=$3 AND version=$4", namespace, product.Terms.ProductID, product.Terms.Version, product.Availability.Version).Scan(&existing); err != nil {
		return err
	}
	if existing != product.AvailabilityHash {
		return ErrConflict
	}
	return tx.Commit(ctx)
}

func productV2(ctx context.Context, q querier, namespace, productID string, termsVersion int64) (bankv2.ProductSnapshot, error) {
	var p bankv2.ProductSnapshot
	var terms, availability []byte
	err := q.QueryRow(ctx, `SELECT t.original,t.original_hash,a.original,a.original_hash FROM bank.v2_product_terms t JOIN LATERAL(SELECT original,original_hash FROM bank.v2_product_availability WHERE namespace=t.namespace AND product_id=t.product_id AND terms_version=t.version ORDER BY version DESC LIMIT 1)a ON true WHERE t.namespace=$1 AND t.product_id=$2 AND t.version=$3`, namespace, productID, termsVersion).Scan(&terms, &p.TermsHash, &availability, &p.AvailabilityHash)
	if err != nil {
		return p, err
	}
	if json.Unmarshal(terms, &p.Terms) != nil || json.Unmarshal(availability, &p.Availability) != nil || p.Validate() != nil {
		return p, bankv2.ErrInvalid
	}
	return p, nil
}

func positionV2(ctx context.Context, q querier, namespace, user, id string) (bankv2.Position, error) {
	var p bankv2.Position
	var raw []byte
	var closing *string
	err := q.QueryRow(ctx, `SELECT o.original,o.original_hash,s.closing_key,s.closed FROM bank.v2_position_openings o JOIN bank.v2_position_state s USING(namespace,user_id,position_id) WHERE o.namespace=$1 AND o.user_id=$2 AND o.position_id=$3`, namespace, user, id).Scan(&raw, &p.OpeningHash, &closing, &p.Closed)
	if err != nil {
		return p, err
	}
	if closing != nil {
		p.ClosingKey = *closing
	}
	if json.Unmarshal(raw, &p.Opening) != nil {
		return p, bankv2.ErrInvalid
	}
	hash, err := p.Opening.Digest()
	if err != nil || hash != p.OpeningHash {
		return p, bankv2.ErrInvalid
	}
	return p, nil
}

// OpenHistoricalPositionV2 imports explicit already-held principal for bounded
// tests/imports. It neither rewrites purchase dates nor claims the process waited
// for maturity. The independently retained seed evidence hash is mandatory.
func (s *Store) OpenHistoricalPositionV2(ctx context.Context, opening bankv2.PositionOpening) error {
	if opening.Validate() != nil || opening.SourceKind != "TRUSTED_HISTORICAL_IMPORT" {
		return bankv2.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, opening.Namespace, opening.UserID); err != nil {
		return err
	}
	h, _ := opening.Digest()
	raw, _ := json.Marshal(opening)
	var maturity any
	if !opening.MaturityAt.IsZero() {
		maturity = opening.MaturityAt
	}
	tag, err := tx.Exec(ctx, `INSERT INTO bank.v2_position_openings(namespace,user_id,position_id,opening_key,original,original_hash,principal_cents,product_id,terms_version,goal_id,return_account_id,purchased_at,maturity_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT DO NOTHING`, opening.Namespace, opening.UserID, opening.PositionID, opening.OpeningKey, raw, h, opening.Principal.Cents(), opening.Terms.ProductID, opening.Terms.Version, opening.GoalID, opening.ReturnAccountID, opening.PurchasedAt, maturity)
	if err != nil {
		return err
	}
	var existing string
	if err = tx.QueryRow(ctx, "SELECT original_hash FROM bank.v2_position_openings WHERE namespace=$1 AND user_id=$2 AND position_id=$3", opening.Namespace, opening.UserID, opening.PositionID).Scan(&existing); err != nil {
		return err
	}
	if existing != h {
		return ErrConflict
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.v2_position_state(namespace,user_id,position_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", opening.Namespace, opening.UserID, opening.PositionID); err != nil {
		return err
	}
	if opening.GoalID != "" && tag.RowsAffected() == 1 {
		_, err = tx.Exec(ctx, "INSERT INTO bank.v2_goal_principal(namespace,user_id,goal_id,principal_cents) VALUES($1,$2,$3,$4) ON CONFLICT(namespace,user_id,goal_id) DO UPDATE SET principal_cents=bank.v2_goal_principal.principal_cents+EXCLUDED.principal_cents", opening.Namespace, opening.UserID, opening.GoalID, opening.Principal.Cents())
		if err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (s *Store) OpenLiabilityV2(ctx context.Context, namespace, user string, liability bankv2.LiabilityReference) error {
	if !validIdentity(namespace, user) || liability.Validate() != nil {
		return bankv2.ErrInvalid
	}
	raw, _ := json.Marshal(liability)
	h := sha256.Sum256(raw)
	hash := hex.EncodeToString(h[:])
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.v2_liability_originals(namespace,user_id,liability_id,original,original_hash,final_total_cents) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING", namespace, user, liability.ID, raw, hash, liability.FinalTotal.Cents()); err != nil {
		return err
	}
	var existing string
	if err = tx.QueryRow(ctx, "SELECT original_hash FROM bank.v2_liability_originals WHERE namespace=$1 AND user_id=$2 AND liability_id=$3", namespace, user, liability.ID).Scan(&existing); err != nil {
		return err
	}
	if existing != hash {
		return ErrConflict
	}
	if _, err = tx.Exec(ctx, "INSERT INTO bank.v2_liability_state(namespace,user_id,liability_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", namespace, user, liability.ID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) QuoteV2(ctx context.Context, namespace, user, positionID string, kind bankv2.Kind, now time.Time) (bankv2.Quote, error) {
	var quote bankv2.Quote
	now = now.UTC().Truncate(time.Microsecond)
	if !validIdentity(namespace, user, positionID) || (kind != bankv2.Redeem && kind != bankv2.Mature) {
		return quote, bankv2.ErrInvalid
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return quote, err
	}
	defer tx.Rollback(ctx)
	if err = userLock(ctx, tx, namespace, user); err != nil {
		return quote, err
	}
	position, err := positionV2(ctx, tx, namespace, user, positionID)
	if err != nil {
		return quote, err
	}
	if position.Closed || position.ClosingKey != "" {
		return quote, ErrConflict
	}
	product, err := productV2(ctx, tx, namespace, position.Opening.Terms.ProductID, position.Opening.Terms.Version)
	if err != nil {
		return quote, err
	}
	net, fee, loss, at, err := bankv2.RedemptionQuote(position.Opening.Principal, position.Opening.Terms, position.Opening.PurchasedAt, now, kind == bankv2.Mature)
	if err != nil {
		return quote, err
	}
	var random [16]byte
	if _, err = rand.Read(random[:]); err != nil {
		return quote, err
	}
	quote = bankv2.Quote{Protocol: bankv2.Protocol + "/quote", Namespace: namespace, UserID: user, ID: "quote:" + hex.EncodeToString(random[:]), Kind: kind, PositionID: positionID, PositionOpeningHash: position.OpeningHash, Product: product, Principal: position.Opening.Principal, Net: net, Fee: fee, Loss: loss, RequestedAt: now, AvailableAt: at, ExpiresAt: now.Add(5 * time.Minute)}
	if quote.Validate() != nil {
		return quote, bankv2.ErrInvalid
	}
	raw, _ := json.Marshal(quote)
	hash, _ := quote.Digest()
	if _, err = tx.Exec(ctx, "INSERT INTO bank.v2_quotes(namespace,user_id,quote_id,position_id,original,original_hash) VALUES($1,$2,$3,$4,$5,$6)", namespace, user, quote.ID, positionID, raw, hash); err != nil {
		return quote, err
	}
	return quote, tx.Commit(ctx)
}
