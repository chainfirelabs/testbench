// Command testbench lists and searches TestBench inventory, software and results.
package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"text/tabwriter"
	"time"
	"unicode"

	tb "gitlab.joedoes.tech/chainfirelabs/testbench/src/go-client"
)

type filters []string

func (f *filters) String() string { return strings.Join(*f, ", ") }
func (f *filters) Set(v string) error {
	key, _, ok := strings.Cut(v, "=")
	if !ok || strings.TrimSpace(key) == "" {
		return fmt.Errorf("expected COLUMN=VALUE")
	}
	*f = append(*f, v)
	return nil
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	os.Exit(run(ctx, os.Args[1:], os.Stdout, os.Stderr))
}

func run(ctx context.Context, args []string, out, errOut io.Writer) int {
	fs := flag.NewFlagSet("testbench", flag.ContinueOnError)
	fs.SetOutput(errOut)
	devices := fs.Bool("devices", false, "List devices (all pages)")
	software := fs.Bool("software", false, "List software")
	tests := fs.Bool("tests", false, "List test results")
	vendor := fs.Bool("vendor-devices", false, "List vendor compatibility claims across every software")
	types := fs.Bool("types", false, "List device types (including disabled)")
	whoami := fs.Bool("whoami", false, "Show the token's account, role and permissions")
	fields := fs.Bool("fields", false, "Show searchable field definitions for the selected resource")
	typeKey := fs.String("type", "", "Device type key, e.g. router or uncategorized (devices only)")
	search := fs.String("search", "", "Search across the resource's searchable columns")
	get := fs.String("get", "", "Get one device ID, software name/UUID, or test UUID")
	version := fs.String("version", "", "Software version for --software --get or --tests --for-software")
	forDevice := fs.String("for-device", "", "Filter tests by device unique ID or UUID")
	forSoftware := fs.String("for-software", "", "Filter tests by software name or UUID")
	support := fs.String("support", "", "Vendor claim status: supported, partial, unsupported, planned")
	latest := fs.Bool("latest", false, "Only the latest version of each software name")
	status := fs.String("status", "", "Device workflow status")
	outcome := fs.String("outcome", "", "Test outcome: pass, fail, warn")
	tag := fs.String("tag", "", "Test tag: adhoc, acceptance, end-to-end, automated")
	sort := fs.String("sort", "", "Sort by a column key")
	order := fs.String("order", "", "Sort order: asc or desc")
	limit := fs.Int("limit", 0, "Maximum rows; 0 means all")
	pageSize := fs.Int("page-size", 200, "Rows per API request (1-1000)")
	columns := fs.String("columns", "", "Comma-separated output columns; supports data.key and misc_data.key")
	format := fs.String("format", "table", "Output: table, json, csv")
	asJSON := fs.Bool("json", false, "Output complete records as JSON (alias for --format json)")
	base := fs.String("url", os.Getenv("TB_API_URL"), "API URL (defaults to TB_API_URL or localhost:8001)")
	token := fs.String("token", "", "API token (defaults to TB_API_KEY)")
	timeout := fs.Duration("timeout", 2*time.Minute, "Timeout for the entire command, e.g. 30s or 5m")
	var filter filters
	fs.Var(&filter, "filter", "Column filter COLUMN=VALUE; repeat for multiple columns")
	fs.Usage = func() {
		fmt.Fprint(errOut, "Usage: testbench --devices|--software|--vendor-devices|--tests|--types|--whoami [options]\n\nExamples:\n  testbench --devices --type router\n  testbench --devices --filter 'make=MikroTik' --filter 'location=Lab A'\n  testbench --software --search curl --latest\n  testbench --vendor-devices --search 'ISR 4331'\n  testbench --vendor-devices --for-software curl-smoke --support supported\n  testbench --tests --for-device dev-0042 --outcome fail --json\n  testbench --devices --type router --fields\n\nVendor devices are what a vendor CLAIMS supports their software; tests are what\nhas actually been run. Absence of a test means untested, not unsupported.\n\nAuthentication: set TB_API_URL and TB_API_KEY.\n")
		fs.PrintDefaults()
	}
	fail := func(err error) int { fmt.Fprintln(errOut, "testbench:", err); return 2 }
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if len(args) == 0 {
		fs.Usage()
		return 0
	}
	if fs.NArg() != 0 {
		return fail(fmt.Errorf("unexpected arguments: %s", strings.Join(fs.Args(), " ")))
	}
	n := 0
	for _, v := range []bool{*devices, *software, *vendor, *tests, *types, *whoami} {
		if v {
			n++
		}
	}
	if n != 1 {
		return fail(fmt.Errorf("select exactly one of --devices, --software, --vendor-devices, --tests, --types, --whoami"))
	}
	if *limit < 0 || *pageSize < 1 || *pageSize > 1000 || *timeout <= 0 {
		return fail(fmt.Errorf("limit must be nonnegative, page-size 1-1000, and timeout positive"))
	}
	if *asJSON {
		*format = "json"
	}
	if *format != "table" && *format != "json" && *format != "csv" {
		return fail(fmt.Errorf("format must be table, json, or csv"))
	}
	if *order != "" && *order != "asc" && *order != "desc" {
		return fail(fmt.Errorf("order must be asc or desc"))
	}
	if !*devices && (*typeKey != "" || *status != "") {
		return fail(fmt.Errorf("--type and --status require --devices"))
	}
	if !*software && *latest {
		return fail(fmt.Errorf("--latest requires --software"))
	}
	// --for-device and --for-software are shared with --vendor-devices, where
	// they mean the same thing: narrow to the claims about that hardware, or to
	// that software's own list.
	if !*tests && !*vendor && (*forDevice != "" || *forSoftware != "") {
		return fail(fmt.Errorf("--for-device and --for-software require --tests or --vendor-devices"))
	}
	if !*tests && (*outcome != "" || *tag != "") {
		return fail(fmt.Errorf("--outcome and --tag require --tests"))
	}
	if !*vendor && *support != "" {
		return fail(fmt.Errorf("--support requires --vendor-devices"))
	}
	if *vendor && *get != "" {
		// A claim is identified by its hardware and the software that makes
		// it, not by an id anyone would have to hand; there is no single-claim
		// endpoint to put behind this.
		return fail(fmt.Errorf("--get does not apply to --vendor-devices; filter with --search or --for-software"))
	}
	if *vendor && *forDevice != "" && *forSoftware != "" {
		// They narrow to different things — one device's hardware, or one
		// software's list — and the API has no endpoint that does both.
		return fail(fmt.Errorf("--vendor-devices takes --for-device or --for-software, not both"))
	}
	if *version != "" && !(*software && *get != "" || *tests && *forSoftware != "" || *vendor && *forSoftware != "") {
		return fail(fmt.Errorf("--version requires --software --get, --tests --for-software, or --vendor-devices --for-software"))
	}
	listFilters := *search != "" || len(filter) > 0 || *sort != "" || *order != "" || *limit != 0 || *typeKey != "" || *status != "" || *latest || *forDevice != "" || *forSoftware != "" || *outcome != "" || *tag != "" || *support != ""
	if *get != "" && (listFilters || *fields || *types || *whoami) {
		return fail(fmt.Errorf("--get requires a resource and cannot be combined with list filters or --fields"))
	}
	if (*types || *whoami) && (listFilters || *fields) {
		return fail(fmt.Errorf("list filters and --fields require --devices, --software, --vendor-devices, or --tests"))
	}
	if *fields && *vendor {
		return fail(fmt.Errorf("--fields requires --devices, --software, or --tests"))
	}
	if *fields && (*search != "" || len(filter) > 0 || *sort != "" || *order != "" || *limit != 0 || *status != "" || *latest || *forDevice != "" || *forSoftware != "" || *outcome != "" || *tag != "" || *support != "") {
		return fail(fmt.Errorf("--fields only supports --type as a filter"))
	}
	q := url.Values{}
	for _, f := range filter {
		key, value, _ := strings.Cut(f, "=")
		key = strings.TrimSpace(key)
		switch key {
		case "page", "page_size", "offset":
			return fail(fmt.Errorf("use --limit and --page-size instead of filtering %s", key))
		}
		if _, ok := q[key]; ok {
			return fail(fmt.Errorf("duplicate filter %q", key))
		}
		q.Set(key, value)
	}
	for key, value := range map[string]string{"search": *search, "device_type": *typeKey, "status": *status, "outcome": *outcome, "tag": *tag, "support_status": *support, "sort": *sort, "order": *order} {
		if value != "" {
			if _, ok := q[key]; ok {
				return fail(fmt.Errorf("%s supplied both as a filter and a flag", key))
			}
			q.Set(key, value)
		}
	}
	if *forDevice != "" && q.Has("device_id") || *forSoftware != "" && q.Has("software_id") {
		return fail(fmt.Errorf("do not combine named test filters with device_id/software_id filters"))
	}
	if *latest && q.Has("latest_only") {
		return fail(fmt.Errorf("latest_only supplied both as a filter and a flag"))
	}
	if *latest {
		q.Set("latest_only", "true")
	}
	for key, allowed := range map[string]string{"status": "available,checked_out,inventory,missing,broken", "outcome": "pass,fail,warn", "tag": "adhoc,acceptance,end-to-end,automated", "support_status": "supported,partial,unsupported,planned"} {
		// Each vocabulary belongs to one resource; validating it against the
		// others would reject a filter the API is perfectly happy with.
		switch key {
		case "status":
			if !*devices {
				continue
			}
		case "support_status":
			if !*vendor {
				continue
			}
		default:
			if !*tests {
				continue
			}
		}
		if value := q.Get(key); value != "" {
			found := false
			for _, v := range strings.Split(allowed, ",") {
				found = found || value == v
			}
			if !found {
				return fail(fmt.Errorf("%s must be one of %s", key, allowed))
			}
		}
	}
	if *token == "" {
		*token = os.Getenv("TB_API_KEY")
	}
	c, err := tb.New(*base, *token, tb.Options{PageSize: *pageSize, Retries: 2})
	if err != nil {
		return fail(err)
	}
	defer c.Close()
	ctx, cancel := context.WithTimeout(ctx, *timeout)
	defer cancel()
	options := tb.ListOptions{Query: q, Limit: *limit}
	rows := []tb.Record{}
	var record tb.Record
	defaults := ""
	switch {
	case *fields:
		defaults = "key,label,type,required,plugin_role"
		if *devices {
			var schema tb.Record
			schemaKey := *typeKey
			if schemaKey == "uncategorized" {
				schemaKey = ""
			}
			schema, err = c.DeviceSchema(ctx, schemaKey)
			if err == nil {
				raw, _ := json.Marshal(schema["fields"])
				err = json.Unmarshal(raw, &rows)
			}
		} else {
			var defs map[string][]tb.Record
			defs, err = c.EntityFields(ctx)
			if *software {
				rows = defs["software"]
			} else {
				rows = defs["tests"]
			}
		}
	case *whoami:
		record, err = c.Whoami(ctx)
		// Permissions, not just the role name: an installation defines its own
		// roles, so "auditor" says nothing about what the token may do.
		defaults = "username,role,permissions,email"
	case *types:
		rows, err = c.DeviceTypes(ctx, true)
		defaults = "key,label,enabled,device_count"
	case *devices:
		defaults = "unique_id,device_type_key,status,make,model,lan_ip,online_status"
		if *get != "" {
			record, err = c.Devices.Get(ctx, *get)
		} else {
			rows, err = c.Devices.List(ctx, options)
		}
	case *software:
		defaults = "name,version,is_latest,vendor_device_count"
		if *get != "" {
			record, err = c.Software.Get(ctx, *get, *version)
		} else {
			rows, err = c.Software.List(ctx, options)
		}
	case *vendor:
		defaults = "software_name,software_version,make,model,firmware_version,architecture,support_status"
		// Every named flag already travelled into `q` above, so the filters
		// compose with --for-software and --for-device rather than being
		// quietly dropped by them.
		claims := tb.VendorDeviceListOptions{ListOptions: options}
		switch {
		case *forSoftware != "":
			rows, err = c.VendorDevices.ForSoftware(ctx, *forSoftware, *version, claims)
		case *forDevice != "":
			rows, err = c.VendorDevices.ForDevice(ctx, *forDevice, claims)
		default:
			rows, err = c.VendorDevices.List(ctx, claims)
		}
	case *tests:
		defaults = "id,device_unique_id,software_name,software_version,outcome,tag,run_at"
		if *get != "" {
			record, err = c.Tests.Get(ctx, *get)
		} else {
			rows, err = c.Tests.List(ctx, tb.TestListOptions{ListOptions: options, Device: *forDevice, Software: *forSoftware, SoftwareVersion: *version})
		}
	}
	if err != nil {
		fmt.Fprintln(errOut, "testbench:", err)
		return 1
	}
	if record != nil {
		rows = append(rows, record)
	}
	if rows == nil {
		rows = []tb.Record{}
	}
	if *columns == "" {
		*columns = defaults
	}
	cols := strings.Split(*columns, ",")
	for i, col := range cols {
		cols[i] = strings.TrimSpace(col)
		if cols[i] == "" {
			return fail(fmt.Errorf("output column must not be empty"))
		}
	}
	if err = render(out, rows, cols, *format); err != nil {
		fmt.Fprintln(errOut, "testbench:", err)
		return 1
	}
	return 0
}

func value(row tb.Record, key string) any {
	if v := row.Field(key); v != nil {
		return v
	}
	var v any = map[string]any(row)
	for _, part := range strings.Split(key, ".") {
		m, ok := v.(map[string]any)
		if !ok {
			return nil
		}
		v = m[part]
	}
	return v
}
func cell(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(b)
}
func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsControl(r) {
			return ' '
		}
		return r
	}, s)
}
func render(out io.Writer, rows []tb.Record, columns []string, format string) error {
	if format == "json" {
		enc := json.NewEncoder(out)
		enc.SetIndent("", "  ")
		return enc.Encode(rows)
	}
	if format == "csv" {
		w := csv.NewWriter(out)
		if err := w.Write(columns); err != nil {
			return err
		}
		for _, row := range rows {
			cells := make([]string, len(columns))
			for i, col := range columns {
				cells[i] = cell(value(row, col))
			}
			if err := w.Write(cells); err != nil {
				return err
			}
		}
		w.Flush()
		return w.Error()
	}
	w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
	if _, err := fmt.Fprintln(w, strings.Join(columns, "\t")); err != nil {
		return err
	}
	for _, row := range rows {
		cells := make([]string, len(columns))
		for i, col := range columns {
			cells[i] = clean(cell(value(row, col)))
		}
		if _, err := fmt.Fprintln(w, strings.Join(cells, "\t")); err != nil {
			return err
		}
	}
	return w.Flush()
}
