package main

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	tb "gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client"
)

func invoke(t *testing.T, h http.HandlerFunc, args ...string) (int, string, string) {
	t.Helper()
	s := httptest.NewServer(h)
	defer s.Close()
	t.Setenv("TB_API_URL", s.URL)
	t.Setenv("TB_API_KEY", "tb_test_secret")
	var out, err bytes.Buffer
	code := run(context.Background(), args, &out, &err)
	return code, out.String(), err.String()
}
func TestDevicesAllPages(t *testing.T) {
	calls := 0
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer tb_test_secret" {
			t.Error("missing token")
		}
		q := r.URL.Query()
		if q.Get("device_type") != "router" || q.Get("make") != "MikroTik" || q.Get("location") != "Lab A" || q.Get("page_size") != "1" {
			t.Error(q)
		}
		fmt.Fprintf(w, `{"items":[{"id":"%s","unique_id":"dev-%s","data":{"serial":"SN-%s"}}],"total":2}`, q.Get("page"), q.Get("page"), q.Get("page"))
	}, "--devices", "--type", "router", "--filter", "make=MikroTik", "--filter", "location=Lab A", "--page-size", "1", "--columns", "unique_id,serial")
	if code != 0 || calls != 2 || !strings.Contains(out, "dev-2") || !strings.Contains(out, "SN-1") {
		t.Fatal(code, out, err, calls)
	}
}
func TestSoftwareSearch(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if r.URL.Path != "/api/v1/software" || q.Get("search") != "curl" || q.Get("latest_only") != "true" || q.Get("vendor") != "Example" || q.Get("sort") != "name" || q.Get("order") != "desc" {
			t.Error(r.URL)
		}
		fmt.Fprint(w, `{"items":[{"id":"s1","name":"curl","misc_data":{"notes":"a,b\nnext"}}],"total":1}`)
	}, "--software", "--search", "curl", "--latest", "--filter", "vendor=Example", "--sort", "name", "--order", "desc", "--format", "csv", "--columns", "name,misc_data.notes")
	if code != 0 {
		t.Fatal(code, out, err)
	}
	records, e := csv.NewReader(strings.NewReader(out)).ReadAll()
	if e != nil || records[1][1] != "a,b\nnext" {
		t.Fatal(records, e)
	}
}
func TestTestsNameResolution(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/devices/dev-1":
			fmt.Fprint(w, `{"id":"d1"}`)
		case "/api/v1/software/tool":
			fmt.Fprint(w, `{"id":"s1","version":"1"}`)
		case "/api/v1/tests":
			q := r.URL.Query()
			if q.Get("device_id") != "d1" || q.Get("software_id") != "s1" || q.Get("outcome") != "fail" || q.Get("tag") != "automated" {
				t.Error(q)
			}
			fmt.Fprint(w, `{"items":[{"id":"t1","outcome":"fail","misc_data":{"large":9007199254740993}}],"total":1}`)
		default:
			t.Error(r.URL)
			w.WriteHeader(404)
		}
	}, "--tests", "--for-device", "dev-1", "--for-software", "tool", "--version", "1", "--outcome", "fail", "--tag", "automated", "--json")
	if code != 0 || !strings.Contains(out, "9007199254740993") {
		t.Fatal(code, out, err)
	}
	if !json.Valid([]byte(out)) {
		t.Fatal(out)
	}
}
func TestDiscoveryAndDetails(t *testing.T) {
	cases := []struct {
		args             []string
		path, body, want string
	}{
		{[]string{"--types"}, "/api/v1/device-types", `[{"key":"router","label":"Routers"}]`, "Routers"},
		{[]string{"--whoami"}, "/api/v1/auth/me", `{"username":"alice","role":"readonly"}`, "alice"},
		{[]string{"--devices", "--type", "router", "--fields"}, "/api/v1/device-schema/types/router", `{"fields":[{"key":"serial","label":"Serial"}]}`, "serial"},
		{[]string{"--software", "--fields"}, "/api/v1/entity-fields", `{"software":[{"key":"name"}]}`, "name"},
		{[]string{"--tests", "--get", "t1"}, "/api/v1/tests/t1", `{"id":"t1","outcome":"pass"}`, "pass"},
		{[]string{"--devices", "--get", "rack/a", "--json"}, "/api/v1/devices/lookup/by-unique-id", `{"id":"d1","unique_id":"rack/a"}`, "rack/a"},
	}
	for _, tc := range cases {
		t.Run(strings.Join(tc.args, " "), func(t *testing.T) {
			code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != tc.path {
					t.Error(r.URL)
				}
				fmt.Fprint(w, tc.body)
			}, tc.args...)
			if code != 0 || !strings.Contains(out, tc.want) {
				t.Fatal(code, out, err)
			}
		})
	}
}
func TestInvalidArgsNoRequests(t *testing.T) {
	cases := [][]string{{"--devices", "--tests"}, {"--type", "router"}, {"--software", "--type", "router"}, {"--devices", "--filter", "bad"}, {"--devices", "--filter", "offset=10"}, {"--devices", "--limit", "-1"}, {"--tests", "--outcome", "oops"}, {"--devices", "--status", "oops"}, {"--devices", "--get", "dev-1", "--search", "x"}, {"--whoami", "--filter", "role=admin"}, {"--software", "--fields", "--search", "x"}, {"--devices", "--filter", "make=x", "--filter", "make=y"}}
	for _, args := range cases {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) { t.Error("unexpected request") }, args...)
			if code != 2 || out != "" || err == "" {
				t.Fatal(code, out, err)
			}
		})
	}
}
func TestAPIErrorAndEmptyOutput(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
		fmt.Fprint(w, `{"detail":"expired token"}`)
	}, "--devices")
	if code != 1 || out != "" || !strings.Contains(err, "expired token") {
		t.Fatal(code, out, err)
	}
	code, out, err = invoke(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `{"items":[],"total":0}`) }, "--devices", "--json")
	if code != 0 || strings.TrimSpace(out) != "[]" {
		t.Fatal(code, out, err)
	}
}
func TestHelpWithoutToken(t *testing.T) {
	t.Setenv("TB_API_KEY", "")
	var out, err bytes.Buffer
	if code := run(context.Background(), []string{"--help"}, &out, &err); code != 0 || !strings.Contains(err.String(), "--devices") {
		t.Fatal(code, err.String())
	}
}
func TestTerminalControlCharacters(t *testing.T) {
	var out bytes.Buffer
	err := render(&out, []tb.Record{{"name": "a\x1b[31m\n\tb"}}, []string{"name"}, "table")
	if err != nil || strings.ContainsAny(out.String(), "\x1b\t") {
		t.Fatal(out.String(), err)
	}
}

func TestHelpDoesNotExposeToken(t *testing.T) {
	t.Setenv("TB_API_KEY", "tb_private_secret")
	var out, stderr bytes.Buffer
	code := run(context.Background(), []string{"--help"}, &out, &stderr)
	if code != 0 || strings.Contains(stderr.String(), "tb_private_secret") {
		t.Fatal("help exposed credential")
	}
}

func TestVendorDevicesListsTheCatalogue(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if r.URL.Path != "/api/v1/vendor-devices" || q.Get("search") != "ISR" || q.Get("support_status") != "supported" {
			t.Error(r.URL)
		}
		fmt.Fprint(w, `{"items":[{"id":"vd1","make":"Cisco","model":"ISR 4331","support_status":"supported","software_name":"Backup-Restore","software_version":"2.0"}],"total":1}`)
	}, "--vendor-devices", "--search", "ISR", "--support", "supported")
	if code != 0 || !strings.Contains(out, "Backup-Restore") || !strings.Contains(out, "ISR 4331") {
		t.Fatal(code, out, err)
	}
	// The software making the claim leads the row: without it a claim says
	// nothing, because a claim is a statement by one software about hardware.
	if !strings.HasPrefix(strings.TrimSpace(out), "software_name") {
		t.Fatalf("software should lead the default columns: %q", out)
	}
}

func TestVendorDevicesForSoftwareResolvesThenFilters(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/software/curl-smoke":
			fmt.Fprint(w, `{"id":"sw-1","name":"curl-smoke","version":"3.8.5","is_latest":true}`)
		case "/api/v1/vendor-devices":
			// The named flag has to survive the narrowing rather than being
			// dropped by it.
			if q := r.URL.Query(); q.Get("software_id") != "sw-1" || q.Get("support_status") != "partial" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"items":[{"id":"vd1","make":"Juniper"}],"total":1}`)
		default:
			t.Error(r.URL.Path)
		}
	}, "--vendor-devices", "--for-software", "curl-smoke", "--version", "3.8.5", "--support", "partial")
	if code != 0 || !strings.Contains(out, "Juniper") {
		t.Fatal(code, out, err)
	}
}

func TestVendorDevicesForDeviceUsesItsMakeAndModel(t *testing.T) {
	code, out, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/devices/dev-0042":
			fmt.Fprint(w, `{"id":"d1","unique_id":"dev-0042","make":"Cisco","model":"ISR 4331"}`)
		case "/api/v1/vendor-devices":
			if q := r.URL.Query(); q.Get("make") != "Cisco" || q.Get("model") != "ISR 4331" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"items":[{"id":"vd1","software_name":"NetGuard"}],"total":1}`)
		default:
			t.Error(r.URL.Path)
		}
	}, "--vendor-devices", "--for-device", "dev-0042")
	if code != 0 || !strings.Contains(out, "NetGuard") {
		t.Fatal(code, out, err)
	}
}

func TestVendorDeviceFlagCombinationsAreRejected(t *testing.T) {
	refuse := func(t *testing.T, want string, args ...string) {
		t.Helper()
		code, _, err := invoke(t, func(w http.ResponseWriter, r *http.Request) {
			t.Errorf("no request should be made: %s", r.URL)
		}, args...)
		if code != 2 || !strings.Contains(err, want) {
			t.Fatalf("args=%v code=%d err=%q", args, code, err)
		}
	}
	refuse(t, "must be one of", "--vendor-devices", "--support", "maybe")
	refuse(t, "--support requires --vendor-devices", "--devices", "--support", "supported")
	refuse(t, "--get does not apply", "--vendor-devices", "--get", "vd1")
	refuse(t, "not both", "--vendor-devices", "--for-device", "dev-1", "--for-software", "curl")
	refuse(t, "--fields requires", "--vendor-devices", "--fields")
	refuse(t, "select exactly one", "--vendor-devices", "--devices")
	// The test vocabularies are not the claim vocabulary and vice versa.
	refuse(t, "--outcome and --tag require --tests", "--vendor-devices", "--outcome", "pass")
	refuse(t, "must be one of pass,fail,warn", "--tests", "--outcome", "supported")
}
