// banksim runs only an isolated local simulated bank. It has its own database
// credentials and commits independently from the application process.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"zhiyu.local/zhiyu-go/internal/adapters/banksim"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	configPath := flag.String("config", ".runtime/bank-runtime.json", "isolated bank-role runtime configuration")
	listen := flag.String("listen", "127.0.0.1:19311", "literal loopback listen address")
	migrate := flag.Bool("migrate", false, "apply isolated bank schema before serving")
	protocolV2 := flag.Bool("protocol-v2", false, "explicitly expose bank v2 alongside unchanged v1 routes")
	migrateV2 := flag.Bool("migrate-v2", false, "explicitly apply frozen additive bank v2 schema; requires -protocol-v2")
	creditInventory := flag.Bool("credit-inventory", false, "explicitly expose read-only external-income inventory; requires -protocol-v2 and frozen bank003")
	migrateCredit := flag.Bool("migrate-credit", false, "explicitly apply frozen additive bank003; requires -credit-inventory")
	creditAuthorities := flag.String("external-credit-authorities", "", "explicit separate external-event authority file; enables privileged credit writer")
	faults := flag.Bool("test-faults", false, "enable explicit response-loss fault header in this test bank")
	flag.Parse()
	if *migrateV2 && !*protocolV2 {
		return fmt.Errorf("bank v2 migration requires explicit -protocol-v2")
	}
	if *creditInventory && !*protocolV2 || *migrateCredit && !*creditInventory || *creditAuthorities != "" && !*creditInventory {
		return fmt.Errorf("external credits require explicit -protocol-v2 and -credit-inventory; migration and writer are separate opt-ins")
	}
	host, _, err := net.SplitHostPort(*listen)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return fmt.Errorf("bank listen address must be literal loopback")
	}
	raw, err := os.ReadFile(*configPath)
	if err != nil {
		return fmt.Errorf("bank runtime configuration unavailable")
	}
	var config struct {
		Role        string `json:"role"`
		Environment string `json:"environment"`
		DSN         string `json:"bank_dsn"`
		Secret      string `json:"bank_permit_secret"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	var extra any
	if len(raw) > 64<<10 || d.Decode(&config) != nil || d.Decode(&extra) != io.EOF || config.Role != "bank" || !strings.HasPrefix(config.Environment, "zygo_") {
		return fmt.Errorf("invalid bank runtime configuration")
	}
	u, err := url.Parse(config.DSN)
	if err != nil || u.User == nil || u.User.Username() != "zygo_bank" || strings.TrimPrefix(u.Path, "/") != config.Environment+"_bank" || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("bank-role database identity mismatch")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	store, err := banksim.New(ctx, config.DSN, []byte(config.Secret))
	if err != nil {
		return err
	}
	defer store.Close()
	if *migrate {
		if store.Migrate(ctx) != nil {
			return fmt.Errorf("bank schema migration failed")
		}
	}
	if *migrateV2 && store.MigrateV2(ctx) != nil {
		return fmt.Errorf("bank v2 schema migration failed")
	}
	if *migrateCredit && store.MigrateExternalCredit(ctx) != nil {
		return fmt.Errorf("external credit schema migration failed")
	}
	var handler http.Handler = banksim.Handler(store, banksim.HandlerOptions{TestFaults: *faults})
	if *protocolV2 {
		if store.CheckV2(ctx) != nil {
			return fmt.Errorf("bank v2 schema is not ready")
		}
		handler = banksim.HandlerV2(store, banksim.HandlerOptions{TestFaults: *faults})
	}
	if *creditInventory {
		if store.CheckExternalCredit(ctx) != nil {
			return fmt.Errorf("external credit schema is not ready")
		}
		handler = banksim.CreditReadHandler(store, handler)
	}
	if *creditAuthorities != "" {
		authorities, err := loadExternalAuthorities(*creditAuthorities)
		if err != nil {
			return err
		}
		gateway, err := banksim.NewCreditGateway(store, authorities)
		if err != nil {
			return fmt.Errorf("external credit authorities rejected")
		}
		handler = banksim.ExternalCreditHandler(gateway, banksim.HandlerOptions{TestFaults: *faults}, handler)
	}
	server := &http.Server{Addr: *listen, Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 * 1024}
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	fmt.Printf("isolated simulated bank listening at http://%s; test faults=%t; credit inventory=%t; external credit writer=%t\n", *listen, *faults, *creditInventory, *creditAuthorities != "")
	if err = server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return fmt.Errorf("bank HTTP server failed")
	}
	<-shutdownDone
	return nil
}
