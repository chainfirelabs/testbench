package testbench

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"
)

type Devices struct{ c *Client }
type Software struct{ c *Client }
type Tests struct{ c *Client }

func unusual(s string) bool { return strings.Contains(s, "/") || s == "." || s == ".." }
func (d *Devices) List(ctx context.Context, o ListOptions) ([]Record, error) {
	return d.c.list(ctx, "/devices", o)
}
func (d *Devices) Iterate(ctx context.Context, o ListOptions, yield func(Record) error) error {
	return d.c.Iterate(ctx, "/devices", o, yield)
}
func (d *Devices) Get(ctx context.Context, id string) (Record, error) {
	if unusual(id) {
		return d.c.get(ctx, "/devices/lookup/by-unique-id", url.Values{"unique_id": {id}})
	}
	return d.c.get(ctx, "/devices/"+url.PathEscape(id), nil)
}
func (d *Devices) Exists(ctx context.Context, id string) (bool, error) {
	_, err := d.Get(ctx, id)
	var api *APIError
	if errors.As(err, &api) && api.StatusCode == 404 {
		return false, nil
	}
	return err == nil, err
}
func (d *Devices) Resolve(ctx context.Context, id string) (string, error) {
	r, err := d.Get(ctx, id)
	if err != nil {
		return "", err
	}
	return r.String("id"), nil
}
func (d *Devices) Actions(ctx context.Context, id string) ([]Record, error) {
	if unusual(id) {
		resolved, err := d.Resolve(ctx, id)
		if err != nil {
			return nil, err
		}
		id = resolved
	}
	var r struct {
		Actions []Record `json:"actions"`
	}
	err := d.c.Request(ctx, "GET", "/devices/"+url.PathEscape(id)+"/actions", nil, nil, &r)
	return r.Actions, err
}
func (d *Devices) Schema(ctx context.Context, id string) (Record, error) {
	r, err := d.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	return d.c.DeviceSchema(ctx, r.String("device_type_key"))
}
func (d *Devices) Overdue(ctx context.Context, minDays, limit int) ([]Record, error) {
	return d.c.list(ctx, "/devices/overdue", ListOptions{Query: url.Values{"min_days": {fmt.Sprint(minDays)}}, Limit: limit})
}
func (s *Software) List(ctx context.Context, o ListOptions) ([]Record, error) {
	return s.c.list(ctx, "/software", o)
}
func (s *Software) Iterate(ctx context.Context, o ListOptions, yield func(Record) error) error {
	return s.c.Iterate(ctx, "/software", o, yield)
}

// Get resolves a name or UUID. An empty version selects the current version.
func (s *Software) Get(ctx context.Context, name, version string) (Record, error) {
	path := "/software/" + url.PathEscape(name)
	var q url.Values
	if unusual(name) {
		path = "/software/lookup/by-name"
		q = url.Values{"name": {name}}
	}
	r, err := s.c.get(ctx, path, q)
	if err != nil {
		return nil, err
	}
	if version == "" || r.String("version") == version {
		return r, nil
	}
	rows, err := s.Versions(ctx, r.String("id"))
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if row.String("version") == version {
			return row, nil
		}
	}
	return nil, &APIError{StatusCode: 404, Method: "GET", Path: path, Detail: fmt.Sprintf("software %q has no version %q", name, version)}
}
func (s *Software) Versions(ctx context.Context, name string) ([]Record, error) {
	if unusual(name) {
		r, err := s.Get(ctx, name, "")
		if err != nil {
			return nil, err
		}
		name = r.String("id")
	}
	var r []Record
	err := s.c.Request(ctx, "GET", "/software/"+url.PathEscape(name)+"/versions", nil, nil, &r)
	return r, err
}
func (s *Software) Resolve(ctx context.Context, name, version string) (string, error) {
	r, err := s.Get(ctx, name, version)
	if err != nil {
		return "", err
	}
	return r.String("id"), nil
}

// TestListOptions adds name resolution to arbitrary test filters. Device and
// Software accept the same identifiers as their respective Get methods.
type TestListOptions struct {
	ListOptions
	Device, Software, SoftwareVersion string
}

func (t *Tests) query(ctx context.Context, o TestListOptions) (ListOptions, error) {
	q := url.Values{}
	for k, v := range o.Query {
		q[k] = append([]string(nil), v...)
	}
	if o.Device != "" {
		id, err := t.c.Devices.Resolve(ctx, o.Device)
		if err != nil {
			return ListOptions{}, err
		}
		q.Set("device_id", id)
	}
	if o.Software != "" {
		id, err := t.c.Software.Resolve(ctx, o.Software, o.SoftwareVersion)
		if err != nil {
			return ListOptions{}, err
		}
		q.Set("software_id", id)
	}
	return ListOptions{Query: q, Limit: o.Limit}, nil
}
func (t *Tests) List(ctx context.Context, o TestListOptions) ([]Record, error) {
	q, err := t.query(ctx, o)
	if err != nil {
		return nil, err
	}
	return t.c.list(ctx, "/tests", q)
}
func (t *Tests) Iterate(ctx context.Context, o TestListOptions, yield func(Record) error) error {
	q, err := t.query(ctx, o)
	if err != nil {
		return err
	}
	return t.c.Iterate(ctx, "/tests", q, yield)
}
func (t *Tests) Get(ctx context.Context, id string) (Record, error) {
	return t.c.get(ctx, "/tests/"+url.PathEscape(id), nil)
}
func (t *Tests) ForDevice(ctx context.Context, id string, limit int) ([]Record, error) {
	return t.List(ctx, TestListOptions{Device: id, ListOptions: ListOptions{Limit: limit}})
}
