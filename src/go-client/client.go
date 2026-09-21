// Package testbench queries the TestBench API using an API key or bearer token.
package testbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

const DefaultBaseURL = "http://localhost:8001/api/v1"

// Record retains every API field, including installation-defined fields and
// nested data, test history, compatibility information, and future additions.
// JSON numbers are json.Number, preserving large integer values.
type Record map[string]any

func (r Record) String(key string) string { s, _ := r[key].(string); return s }
func (r Record) Field(key string) any {
	if data, ok := r["data"].(map[string]any); ok {
		if v, found := data[key]; found {
			return v
		}
	}
	return r[key]
}

// APIError preserves the HTTP status and the API's original detail value.
type APIError struct {
	StatusCode   int
	Method, Path string
	Detail       any
}

func (e *APIError) Error() string {
	return fmt.Sprintf("testbench: %s %s: HTTP %d: %v", e.Method, e.Path, e.StatusCode, e.Detail)
}

// Options configure connection reuse, pagination, and GET/HEAD retries.
// Retries defaults to zero; writes are never retried. The default HTTP timeout
// is 30 seconds. A supplied HTTPClient controls its own timeout and TLS policy.
type Options struct {
	HTTPClient *http.Client
	PageSize   int
	Retries    int
}
type Client struct {
	baseURL           string
	token             string
	http              *http.Client
	pageSize, retries int
	Devices           *Devices
	Software          *Software
	Tests             *Tests
	VendorDevices     *VendorDevices
}

var versioned = regexp.MustCompile(`/api/v[0-9]+$`)

func New(baseURL, token string, options Options) (*Client, error) {
	if strings.TrimSpace(token) == "" {
		return nil, fmt.Errorf("testbench: API token is required")
	}
	if strings.ContainsAny(token, "\r\n") {
		return nil, fmt.Errorf("testbench: invalid API token")
	}
	if baseURL == "" {
		baseURL = DefaultBaseURL
	}
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	u, err := url.Parse(baseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("testbench: invalid base URL")
	}
	if !versioned.MatchString(u.Path) {
		baseURL += "/api/v1"
	}
	h := options.HTTPClient
	if h == nil {
		h = &http.Client{Timeout: 30 * time.Second}
	}
	// Do not forward credentials via redirects (including redirects to login).
	clone := *h
	clone.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	size := options.PageSize
	if size <= 0 {
		size = 200
	}
	if size > 1000 {
		size = 1000
	}
	retries := options.Retries
	if retries < 0 {
		retries = 0
	}
	c := &Client{baseURL: baseURL, token: token, http: &clone, pageSize: size, retries: retries}
	c.Devices = &Devices{c}
	c.Software = &Software{c}
	c.Tests = &Tests{c}
	c.VendorDevices = &VendorDevices{c}
	return c, nil
}
func FromEnv(options Options) (*Client, error) {
	return New(os.Getenv("TB_API_URL"), os.Getenv("TB_API_KEY"), options)
}
func (c *Client) Close() { c.http.CloseIdleConnections() }

// Request is the escape hatch for other API endpoints. Path must be a relative
// API path beginning with /. Pass nil for an absent body or discarded response.
func (c *Client) Request(ctx context.Context, method, path string, query url.Values, body, out any) error {
	u, err := url.Parse(path)
	if err != nil || !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") || u.IsAbs() || u.Host != "" || u.Fragment != "" {
		return fmt.Errorf("testbench: invalid API path")
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return fmt.Errorf("testbench: dot path segments are not supported")
		}
	}
	q := u.Query()
	for k, values := range query {
		q[k] = append([]string(nil), values...)
	}
	u.RawQuery = q.Encode()
	var payload []byte
	if body != nil {
		payload, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}
	method = strings.ToUpper(method)
	attempts := 1
	if method == "GET" || method == "HEAD" {
		attempts += c.retries
	}
	for attempt := 0; attempt < attempts; attempt++ {
		if attempt > 0 {
			delay := time.Duration(attempt) * 250 * time.Millisecond
			if delay > 5*time.Second {
				delay = 5 * time.Second
			}
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return ctx.Err()
			case <-timer.C:
			}
		}
		req, err := http.NewRequestWithContext(ctx, method, c.baseURL+u.String(), bytes.NewReader(payload))
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+c.token)
		req.Header.Set("Accept", "application/json")
		req.Header.Set("User-Agent", "testbench-go-client/0.1.0")
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := c.http.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if attempt+1 < attempts {
				continue
			}
			return err
		}
		raw, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			if attempt+1 < attempts {
				continue
			}
			return readErr
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			if resp.StatusCode >= 500 && attempt+1 < attempts {
				continue
			}
			var detail any = string(raw)
			var parsed map[string]any
			if json.Unmarshal(raw, &parsed) == nil {
				if d, ok := parsed["detail"]; ok {
					detail = d
				} else {
					detail = parsed
				}
			}
			return &APIError{resp.StatusCode, method, path, detail}
		}
		if out == nil || len(raw) == 0 || resp.StatusCode == 204 {
			return nil
		}
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.UseNumber()
		return dec.Decode(out)
	}
	return fmt.Errorf("testbench: request failed")
}

// ListOptions accepts all API filters (including dynamic field keys). Limit=0
// means unlimited; a negative limit is rejected. Query is never modified.
type ListOptions struct {
	Query url.Values
	Limit int
}

func (c *Client) Iterate(ctx context.Context, path string, options ListOptions, yield func(Record) error) error {
	if options.Limit < 0 {
		return fmt.Errorf("testbench: limit must not be negative")
	}
	if yield == nil {
		return fmt.Errorf("testbench: yield callback is required")
	}
	q := url.Values{}
	for k, v := range options.Query {
		q[k] = append([]string(nil), v...)
	}
	size := c.pageSize
	if options.Limit > 0 && options.Limit < size {
		size = options.Limit
	}
	seen := map[string]bool{}
	count := 0
	for page := 1; ; page++ {
		q.Set("page", fmt.Sprint(page))
		q.Set("page_size", fmt.Sprint(size))
		var result struct {
			Items []Record `json:"items"`
			Total *int     `json:"total"`
		}
		if err := c.Request(ctx, "GET", path, q, nil, &result); err != nil {
			return err
		}
		for _, row := range result.Items {
			if err := ctx.Err(); err != nil {
				return err
			}
			id := row.String("id")
			if id != "" && seen[id] {
				continue
			}
			if id != "" {
				seen[id] = true
			}
			if err := yield(row); err != nil {
				return err
			}
			count++
			if options.Limit > 0 && count >= options.Limit {
				return nil
			}
		}
		if len(result.Items) < size || (result.Total != nil && page*size >= *result.Total) {
			return nil
		}
	}
}
func (c *Client) list(ctx context.Context, path string, o ListOptions) ([]Record, error) {
	rows := []Record{}
	err := c.Iterate(ctx, path, o, func(r Record) error { rows = append(rows, r); return nil })
	return rows, err
}
func (c *Client) get(ctx context.Context, path string, q url.Values) (Record, error) {
	var r Record
	err := c.Request(ctx, "GET", path, q, nil, &r)
	return r, err
}
func (c *Client) Whoami(ctx context.Context) (Record, error) { return c.get(ctx, "/auth/me", nil) }
func (c *Client) EntityFields(ctx context.Context) (map[string][]Record, error) {
	var r map[string][]Record
	err := c.Request(ctx, "GET", "/entity-fields", nil, nil, &r)
	return r, err
}
func (c *Client) DeviceTypes(ctx context.Context, includeDisabled bool) ([]Record, error) {
	var r []Record
	err := c.Request(ctx, "GET", "/device-types", url.Values{"include_disabled": {fmt.Sprint(includeDisabled)}}, nil, &r)
	return r, err
}
func (c *Client) DeviceSchema(ctx context.Context, typeKey string) (Record, error) {
	path := "/device-schema/global"
	if typeKey != "" {
		path = "/device-schema/types/" + url.PathEscape(typeKey)
	}
	return c.get(ctx, path, nil)
}
func (c *Client) DeviceSchemaRevision(ctx context.Context) (int64, error) {
	r, err := c.DeviceSchema(ctx, "")
	if err != nil {
		return 0, err
	}
	n, ok := r["revision"].(json.Number)
	if !ok {
		return 0, fmt.Errorf("testbench: missing schema revision")
	}
	return n.Int64()
}
