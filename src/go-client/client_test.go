package testbench

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"sync/atomic"
	"testing"
	"time"
)

func newTestClient(t *testing.T, h http.HandlerFunc, o Options) *Client {
	t.Helper()
	server := httptest.NewServer(h)
	t.Cleanup(server.Close)
	c, err := New(server.URL, "tb_test_secret", o)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c
}
func TestPagination(t *testing.T) {
	calls := 0
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer tb_test_secret" {
			t.Error("missing token")
		}
		if r.URL.Path != "/api/v1/devices" || r.URL.Query().Get("serial_number") != "SN/1" || r.URL.Query().Get("page_size") != "2" {
			t.Errorf("bad request %s", r.URL)
		}
		if r.URL.Query().Get("page") == "1" {
			fmt.Fprint(w, `{"items":[{"id":"a","data":{"serial_number":"SN/1","large":9007199254740993}},{"id":"b"}],"total":4}`)
		} else {
			fmt.Fprint(w, `{"items":[{"id":"b"},{"id":"c"}],"total":4}`)
		}
	}, Options{PageSize: 2})
	q := url.Values{"serial_number": {"SN/1"}}
	rows, err := c.Devices.List(context.Background(), ListOptions{Query: q})
	if err != nil || len(rows) != 3 || calls != 2 {
		t.Fatalf("%v rows=%v calls=%d", err, rows, calls)
	}
	if len(q) != 1 || rows[0].Field("large") != json.Number("9007199254740993") {
		t.Fatal("query mutated or numeric precision lost")
	}
}
func TestLimitAndCallback(t *testing.T) {
	calls := 0
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		fmt.Fprint(w, `{"items":[{"id":"a"},{"id":"b"}],"total":10}`)
	}, Options{PageSize: 2})
	rows, err := c.Devices.List(context.Background(), ListOptions{Limit: 1})
	if err != nil || len(rows) != 1 || calls != 1 {
		t.Fatal(rows, err, calls)
	}
	stop := errors.New("stop")
	err = c.Devices.Iterate(context.Background(), ListOptions{}, func(Record) error { return stop })
	if !errors.Is(err, stop) {
		t.Fatal(err)
	}
	_, err = c.Devices.List(context.Background(), ListOptions{Limit: -1})
	if err == nil {
		t.Fatal("negative limit accepted")
	}
}
func TestErrorsRetriesAndCancellation(t *testing.T) {
	for _, code := range []int{401, 403, 404, 409, 422, 500} {
		t.Run(fmt.Sprint(code), func(t *testing.T) {
			var calls atomic.Int32
			c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				w.WriteHeader(code)
				fmt.Fprint(w, `{"detail":{"reason":"denied"}}`)
			}, Options{Retries: 1})
			_, err := c.Whoami(context.Background())
			var api *APIError
			if !errors.As(err, &api) || api.StatusCode != code {
				t.Fatal(err)
			}
			expected := int32(1)
			if code == 500 {
				expected = 2
			}
			if calls.Load() != expected {
				t.Fatal(calls.Load())
			}
			calls.Store(0)
			err = c.Request(context.Background(), "POST", "/tests", nil, Record{}, nil)
			if err == nil || calls.Load() != 1 {
				t.Fatal("write retried", err, calls.Load())
			}
		})
	}
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }, Options{Retries: 100})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, err := c.Whoami(ctx)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
}
func TestRetryRecoversAndNoContent(t *testing.T) {
	calls := 0
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			w.WriteHeader(503)
			return
		}
		if r.Method == "DELETE" {
			w.WriteHeader(204)
			return
		}
		fmt.Fprint(w, `{"username":"tester"}`)
	}, Options{Retries: 1})
	r, err := c.Whoami(context.Background())
	if err != nil || r.String("username") != "tester" || calls != 2 {
		t.Fatal(r, err, calls)
	}
	if err := c.Request(context.Background(), "DELETE", "/tests/x", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
}
func TestURLAndRedirectProtection(t *testing.T) {
	for _, base := range []string{"https://example.test", "https://example.test/api/v1/", "https://example.test/api/v2"} {
		c, err := New(base, "token", Options{})
		if err != nil {
			t.Fatal(err)
		}
		if c.baseURL == "" {
			t.Fatal("empty")
		}
	}
	for _, base := range []string{"file:///tmp/x", "https://user:pass@example.test", "https://example.test?x=1"} {
		if _, err := New(base, "token", Options{}); err == nil {
			t.Fatal(base)
		}
	}
	if _, err := New("", "", Options{}); err == nil {
		t.Fatal("empty token")
	}
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, "/login", 302) }, Options{})
	for _, path := range []string{"https://example.test", "//example.test", "/../auth", "/%2e%2e/auth"} {
		if err := c.Request(context.Background(), "GET", path, nil, nil, nil); err == nil {
			t.Fatal(path)
		}
	}
	_, err := c.Whoami(context.Background())
	var api *APIError
	if !errors.As(err, &api) || api.StatusCode != 302 {
		t.Fatal(err)
	}
}
func TestReadCoverage(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/auth/me":
			fmt.Fprint(w, `{"username":"tester","role":"readonly"}`)
		case "/api/v1/entity-fields":
			fmt.Fprint(w, `{"devices":[{"key":"custom"}]}`)
		case "/api/v1/device-types":
			if r.URL.Query().Get("include_disabled") != "true" {
				t.Error("disabled filter")
			}
			fmt.Fprint(w, `[{"key":"router"}]`)
		case "/api/v1/device-schema/global", "/api/v1/device-schema/types/router":
			fmt.Fprint(w, `{"revision":42,"fields":[]}`)
		case "/api/v1/devices/lookup/by-unique-id":
			if r.URL.Query().Get("unique_id") != "rack/a" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"id":"d1","unique_id":"rack/a","device_type_key":"router","all_tests":[{"id":"t1"}]}`)
		case "/api/v1/devices/d1/actions":
			fmt.Fprint(w, `{"actions":[{"id":"reboot","available":false,"unavailable_reason":"disabled"}]}`)
		case "/api/v1/devices/overdue":
			if r.URL.Query().Get("min_days") != "3" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"items":[{"id":"d1","days_overdue":3}],"total":1}`)
		case "/api/v1/software/lookup/by-name":
			if r.URL.Query().Get("name") != "tool/a" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"id":"s2","name":"tool/a","version":"2"}`)
		case "/api/v1/software/s2/versions":
			fmt.Fprint(w, `[{"id":"s2","version":"2"},{"id":"s1","version":"1"}]`)
		case "/api/v1/software":
			fmt.Fprint(w, `{"items":[{"id":"s2"}],"total":1}`)
		case "/api/v1/tests":
			if r.URL.Query().Get("device_id") != "d1" {
				t.Error(r.URL)
			}
			fmt.Fprint(w, `{"items":[{"id":"t1","outcome":"pass"}],"total":1}`)
		case "/api/v1/tests/t1":
			fmt.Fprint(w, `{"id":"t1","run_at":"2026-09-08"}`)
		default:
			t.Errorf("unexpected %s", r.URL)
			w.WriteHeader(404)
		}
	}, Options{})
	ctx := context.Background()
	if _, e := c.Whoami(ctx); e != nil {
		t.Fatal(e)
	}
	if _, e := c.EntityFields(ctx); e != nil {
		t.Fatal(e)
	}
	if _, e := c.DeviceTypes(ctx, true); e != nil {
		t.Fatal(e)
	}
	if r, e := c.DeviceSchemaRevision(ctx); e != nil || r != 42 {
		t.Fatal(r, e)
	}
	if _, e := c.Devices.Schema(ctx, "rack/a"); e != nil {
		t.Fatal(e)
	}
	if r, e := c.Devices.Actions(ctx, "rack/a"); e != nil || len(r) != 1 {
		t.Fatal(r, e)
	}
	if r, e := c.Devices.Exists(ctx, "rack/a"); e != nil || !r {
		t.Fatal(r, e)
	}
	if _, e := c.Devices.Overdue(ctx, 3, 10); e != nil {
		t.Fatal(e)
	}
	if r, e := c.Software.Get(ctx, "tool/a", "1"); e != nil || r.String("id") != "s1" {
		t.Fatal(r, e)
	}
	if _, e := c.Software.Get(ctx, "tool/a", "missing"); e == nil {
		t.Fatal("missing version accepted")
	}
	if _, e := c.Software.Versions(ctx, "tool/a"); e != nil {
		t.Fatal(e)
	}
	if _, e := c.Software.List(ctx, ListOptions{}); e != nil {
		t.Fatal(e)
	}
	if _, e := c.Tests.ForDevice(ctx, "rack/a", 10); e != nil {
		t.Fatal(e)
	}
	if _, e := c.Tests.Get(ctx, "t1"); e != nil {
		t.Fatal(e)
	}
	q := url.Values{"outcome": {"pass"}}
	if _, e := c.Tests.List(ctx, TestListOptions{Device: "rack/a", Software: "tool/a", SoftwareVersion: "1", ListOptions: ListOptions{Query: q}}); e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(q, url.Values{"outcome": {"pass"}}) {
		t.Fatal("query mutated")
	}
}
func TestFromEnv(t *testing.T) {
	t.Setenv("TB_API_URL", "https://example.test")
	t.Setenv("TB_API_KEY", "tb_x_y")
	c, err := FromEnv(Options{})
	if err != nil || c.baseURL != "https://example.test/api/v1" {
		t.Fatal(c, err)
	}
}

func TestEscapedIdentifiersAndStreaming(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/devices/lab #1?":
			fmt.Fprint(w, `{"id":"d1","unique_id":"lab #1?"}`)
		case "/api/v1/software", "/api/v1/tests":
			fmt.Fprint(w, `{"items":[{"id":"one"}],"total":1}`)
		default:
			t.Errorf("unexpected URL %s", r.URL)
			w.WriteHeader(404)
		}
	}, Options{})
	ctx := context.Background()
	if _, err := c.Devices.Get(ctx, "lab #1?"); err != nil {
		t.Fatal(err)
	}
	count := 0
	yield := func(Record) error { count++; return nil }
	if err := c.Software.Iterate(ctx, ListOptions{}, yield); err != nil {
		t.Fatal(err)
	}
	if err := c.Tests.Iterate(ctx, TestListOptions{}, yield); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatal(count)
	}
}

type failingTransport struct{ calls int }

func (f *failingTransport) RoundTrip(*http.Request) (*http.Response, error) {
	f.calls++
	return nil, errors.New("unreachable")
}
func TestTransportRetry(t *testing.T) {
	tr := &failingTransport{}
	c, err := New("https://example.test", "token", Options{HTTPClient: &http.Client{Transport: tr}, Retries: 1})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = c.Whoami(context.Background()); err == nil || tr.calls != 2 {
		t.Fatal(err, tr.calls)
	}
}

func TestVendorDeviceFiltersTravelAsQueryParameters(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if r.URL.Path != "/api/v1/vendor-devices" {
			t.Errorf("bad path %s", r.URL.Path)
		}
		if q.Get("search") != "ISR" || q.Get("make") != "Cisco" || q.Get("support_status") != "supported" || q.Get("software") != "Backup-Restore" {
			t.Errorf("bad query %v", q)
		}
		fmt.Fprint(w, `{"items":[{"id":"vd1","make":"Cisco","software_name":"Backup-Restore","software_is_latest":false}],"total":1}`)
	}, Options{})
	rows, err := c.VendorDevices.List(context.Background(), VendorDeviceListOptions{
		Search: "ISR", Make: "Cisco", SupportStatus: "supported", Software: "Backup-Restore",
	})
	if err != nil || len(rows) != 1 || rows[0].String("software_name") != "Backup-Restore" {
		t.Fatalf("%v rows=%v", err, rows)
	}
	if latest, _ := rows[0]["software_is_latest"].(bool); latest {
		t.Fatal("a claim on a superseded version should say so")
	}
}

func TestVendorDeviceSupportStatusIsCheckedBeforeTheRequest(t *testing.T) {
	// The list endpoints do not validate filter values: an unknown one returns
	// an empty page, which reads as "nothing supports it" rather than as an
	// error. So it is refused here, without a request.
	calls := int32(0)
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		fmt.Fprint(w, `{"items":[],"total":0}`)
	}, Options{})
	_, err := c.VendorDevices.List(context.Background(), VendorDeviceListOptions{SupportStatus: "maybe"})
	if err == nil || atomic.LoadInt32(&calls) != 0 {
		t.Fatalf("err=%v calls=%d", err, atomic.LoadInt32(&calls))
	}
}

func TestVendorDevicesForSoftwareAsksTheCatalogueByID(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/software/Backup-Restore":
			fmt.Fprint(w, `{"id":"sw-1","name":"Backup-Restore","version":"2.0","is_latest":true}`)
		case "/api/v1/vendor-devices":
			// By id, not by name: a name covers every version of it, and this
			// is one version's own list.
			if q := r.URL.Query(); q.Get("software_id") != "sw-1" || q.Get("search") != "Cisco" {
				t.Errorf("bad query %v", q)
			}
			fmt.Fprint(w, `{"items":[{"id":"vd1","software_version":"2.0"}],"total":1}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
		}
	}, Options{})
	rows, err := c.VendorDevices.ForSoftware(context.Background(), "Backup-Restore", "", VendorDeviceListOptions{Search: "Cisco"})
	if err != nil || len(rows) != 1 || rows[0].String("software_version") != "2.0" {
		t.Fatalf("%v rows=%v", err, rows)
	}
}

func TestVendorDevicesForDeviceSearchesByItsHardware(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/devices/dev-1":
			fmt.Fprint(w, `{"id":"d1","unique_id":"dev-1","make":"Cisco","model":"ISR 4331"}`)
		case "/api/v1/vendor-devices":
			if q := r.URL.Query(); q.Get("make") != "Cisco" || q.Get("model") != "ISR 4331" || q.Get("support_status") != "supported" {
				t.Errorf("bad query %v", q)
			}
			fmt.Fprint(w, `{"items":[{"id":"vd1"}],"total":1}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
		}
	}, Options{})
	rows, err := c.VendorDevices.ForDevice(context.Background(), "dev-1", VendorDeviceListOptions{SupportStatus: "supported"})
	if err != nil || len(rows) != 1 {
		t.Fatalf("%v rows=%v", err, rows)
	}
}

func TestVendorDevicesForDeviceWithoutHardwareMatchesNothing(t *testing.T) {
	calls := int32(0)
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		if r.URL.Path == "/api/v1/devices/dev-1" {
			fmt.Fprint(w, `{"id":"d1","unique_id":"dev-1"}`)
			return
		}
		t.Errorf("the catalogue should not be searched: %s", r.URL.Path)
	}, Options{})
	rows, err := c.VendorDevices.ForDevice(context.Background(), "dev-1", VendorDeviceListOptions{})
	if err != nil || len(rows) != 0 || atomic.LoadInt32(&calls) != 1 {
		t.Fatalf("%v rows=%v calls=%d", err, rows, atomic.LoadInt32(&calls))
	}
}

func TestVendorDeviceFilterSetTwiceIsRefused(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("no request should be made")
	}, Options{})
	_, err := c.VendorDevices.List(context.Background(), VendorDeviceListOptions{
		ListOptions: ListOptions{Query: url.Values{"make": {"Cisco"}}}, Make: "Juniper",
	})
	if err == nil {
		t.Fatal("a filter supplied twice should be refused, not silently resolved")
	}
	// Same rule for the ones these convenience methods set themselves.
	if _, err = c.VendorDevices.ForDevice(context.Background(), "dev-1", VendorDeviceListOptions{Make: "Cisco"}); err == nil {
		t.Fatal("ForDevice should refuse a caller-supplied make")
	}
	if _, err = c.VendorDevices.ForSoftware(context.Background(), "x", "", VendorDeviceListOptions{Software: "y"}); err == nil {
		t.Fatal("ForSoftware should refuse a caller-supplied software")
	}
}
