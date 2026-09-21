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

// VendorDevices reads the hardware vendors CLAIM their software supports.
//
// Not the same question as Tests, and the two routinely disagree: a vendor
// device is a published claim — possibly about hardware nobody here owns, and
// carrying no evidence — while a test is a run that actually happened. Absence
// of a test means untested, not unsupported.
type VendorDevices struct{ c *Client }

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

// VendorDeviceListOptions adds the catalogue's own named filters to the usual
// list options. Software takes a software NAME and covers every version of it;
// a claim made by 1.0 and one made by 2.0 are separate rows and both come back.
type VendorDeviceListOptions struct {
	ListOptions
	Search, Software                              string
	Make, Model, FirmwareVersion, HardwareVersion string
	Architecture, SupportStatus                   string
}

var vendorSupportStatuses = []string{"supported", "partial", "unsupported", "planned"}

// with returns a copy carrying one more raw query parameter, refusing to
// overwrite one the caller set — a silently dropped filter is a wrong answer
// rather than an error, which is the failure mode worth spending a check on.
func (o VendorDeviceListOptions) with(key, value string) (VendorDeviceListOptions, error) {
	if o.Query.Has(key) {
		return o, fmt.Errorf("testbench: %s is set by this call; do not also pass it in Query", key)
	}
	q := url.Values{}
	for k, v := range o.Query {
		q[k] = append([]string(nil), v...)
	}
	q.Set(key, value)
	o.Query = q
	return o, nil
}

func (o VendorDeviceListOptions) query() (ListOptions, error) {
	q := url.Values{}
	for k, v := range o.Query {
		q[k] = append([]string(nil), v...)
	}
	named := map[string]string{
		"search": o.Search, "software": o.Software, "make": o.Make, "model": o.Model,
		"firmware_version": o.FirmwareVersion, "hardware_version": o.HardwareVersion,
		"architecture": o.Architecture, "support_status": o.SupportStatus,
	}
	for key, value := range named {
		if value == "" {
			continue
		}
		if _, ok := q[key]; ok {
			return ListOptions{}, fmt.Errorf("testbench: %s supplied twice", key)
		}
		q.Set(key, value)
	}
	if o.SupportStatus != "" {
		found := false
		for _, v := range vendorSupportStatuses {
			found = found || o.SupportStatus == v
		}
		if !found {
			// The list endpoints do not validate filter values: an unknown one
			// returns an empty page rather than an error, which reads as "no
			// vendor supports it" — a wrong answer rather than a failure.
			return ListOptions{}, fmt.Errorf("testbench: support_status must be one of %s", strings.Join(vendorSupportStatuses, ", "))
		}
	}
	return ListOptions{Query: q, Limit: o.Limit}, nil
}

// List searches every software version's compatibility list at once. Each row
// names the software and version making the claim, and marks a claim made by a
// superseded version with software_is_latest=false.
func (v *VendorDevices) List(ctx context.Context, o VendorDeviceListOptions) ([]Record, error) {
	q, err := o.query()
	if err != nil {
		return nil, err
	}
	return v.c.list(ctx, "/vendor-devices", q)
}

func (v *VendorDevices) Iterate(ctx context.Context, o VendorDeviceListOptions, yield func(Record) error) error {
	q, err := o.query()
	if err != nil {
		return err
	}
	return v.c.Iterate(ctx, "/vendor-devices", q, yield)
}

// ForSoftware reads one software version's own list. An empty version selects
// the current version, as everywhere else. Versions do not inherit from one
// another — a new version starts as a copy and the two diverge — so this is
// genuinely the list that version publishes.
//
// Resolved to an id and asked of the catalogue rather than of the software's
// own sub-resource: the catalogue takes every filter in the options, and its
// rows already name the software they belong to.
func (v *VendorDevices) ForSoftware(ctx context.Context, name, version string, o VendorDeviceListOptions) ([]Record, error) {
	if o.Software != "" {
		return nil, fmt.Errorf("testbench: ForSoftware sets the software; do not also pass Software")
	}
	id, err := v.c.Software.Resolve(ctx, name, version)
	if err != nil {
		return nil, err
	}
	o, err = o.with("software_id", id)
	if err != nil {
		return nil, err
	}
	return v.List(ctx, o)
}

// ForDevice finds the claims describing a device in the fleet, by its own make
// and model. A convenience over List, not a new question. A device carrying
// neither returns nothing: there is nothing to match on, and every claim in the
// catalogue is not an answer.
func (v *VendorDevices) ForDevice(ctx context.Context, id string, o VendorDeviceListOptions) ([]Record, error) {
	if o.Make != "" || o.Model != "" {
		return nil, fmt.Errorf("testbench: ForDevice sets make and model from the device; do not also pass them")
	}
	device, err := v.c.Devices.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	// Not named `make`: that is a builtin, and shadowing it here would be a
	// trap for the next person who needs a slice in this function.
	vendor, model := device.String("make"), device.String("model")
	if vendor == "" && model == "" {
		return []Record{}, nil
	}
	o.Make, o.Model = vendor, model
	return v.List(ctx, o)
}
