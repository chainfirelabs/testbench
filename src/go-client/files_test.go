package testbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// fakeFiles serves one test's artifacts and output the way the API does.
type fakeFiles struct {
	mu        sync.Mutex
	artifacts []map[string]any
	blobs     map[string][]byte
	output    map[string]any
	text      string
	failNext  int
}

func (f *fakeFiles) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.failNext > 0 {
			f.failNext--
			w.WriteHeader(500)
			fmt.Fprint(w, `{"detail":"boom"}`)
			return
		}
		// The test id "t/1" must arrive escaped, as one path segment.
		path, ok := strings.CutPrefix(r.URL.EscapedPath(), "/api/v1/tests/t%2F1")
		if !ok {
			t.Errorf("test id not escaped: %s", r.URL.EscapedPath())
		}
		write := func(status int, body any) {
			w.WriteHeader(status)
			json.NewEncoder(w).Encode(body)
		}
		switch {
		case path == "/artifacts" && r.Method == "GET":
			write(200, map[string]any{"items": f.artifacts, "storage_enabled": true})
		case path == "/artifacts" && r.Method == "POST":
			file, header, err := r.FormFile("file")
			if err != nil {
				t.Fatal(err)
			}
			data, _ := io.ReadAll(file)
			id := fmt.Sprintf("a%d", len(f.artifacts)+1)
			row := map[string]any{"id": id, "filename": header.Filename,
				"content_type": header.Header.Get("Content-Type"), "size_bytes": len(data)}
			f.artifacts = append(f.artifacts, row)
			f.blobs[id] = data
			write(201, row)
		case strings.HasSuffix(path, "/download") && r.Method == "GET":
			id := strings.TrimSuffix(strings.TrimPrefix(path, "/artifacts/"), "/download")
			data, ok := f.blobs[id]
			if !ok {
				write(404, map[string]any{"detail": "Artifact not found"})
				return
			}
			w.Header().Set("Content-Type", "application/octet-stream")
			w.Write(data)
		case strings.HasPrefix(path, "/artifacts/") && r.Method == "DELETE":
			delete(f.blobs, strings.TrimPrefix(path, "/artifacts/"))
			f.artifacts = f.artifacts[:0]
			w.WriteHeader(204)
		case path == "/output" && r.Method == "GET":
			if f.output == nil {
				write(200, map[string]any{"output": nil, "text": nil})
				return
			}
			write(200, map[string]any{"output": f.output, "text": f.text, "error": nil})
		case path == "/output" && r.Method == "PUT":
			file, header, err := r.FormFile("file")
			if err != nil {
				t.Fatal(err)
			}
			data, _ := io.ReadAll(file)
			if bytes.ContainsAny(data, "\xff") {
				write(422, map[string]any{"detail": "Output file must contain only ASCII text"})
				return
			}
			f.output = map[string]any{"filename": header.Filename, "size_bytes": len(data)}
			f.text = string(data)
			write(200, f.output)
		case path == "/output" && r.Method == "DELETE":
			if f.output == nil {
				write(404, map[string]any{"detail": "This test has no output"})
				return
			}
			f.output = nil
			w.WriteHeader(204)
		default:
			write(404, map[string]any{"detail": "no route " + r.Method + " " + path})
		}
	}
}

func TestArtifactsRoundTrip(t *testing.T) {
	fake := &fakeFiles{blobs: map[string][]byte{}}
	c := newTestClient(t, fake.handler(t), Options{Retries: 2})
	ctx := context.Background()
	dir := t.TempDir()
	report := filepath.Join(dir, "report.html")
	os.WriteFile(report, []byte("<h1>ok</h1>"), 0o600)

	row, err := c.Tests.UploadArtifactFile(ctx, "t/1", report)
	if err != nil || row.String("filename") != "report.html" || !strings.HasPrefix(row.String("content_type"), "text/html") {
		t.Fatalf("%v %v", err, row)
	}
	if _, err := c.Tests.UploadArtifact(ctx, "t/1", "../capture.pcap", bytes.NewReader([]byte{0, 0xff})); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Tests.UploadArtifact(ctx, "t/1", " ", nil); err == nil {
		t.Fatal("an empty filename was accepted")
	}
	rows, err := c.Tests.Artifacts(ctx, "t/1")
	if err != nil || len(rows) != 2 || rows[1].String("filename") != "capture.pcap" {
		t.Fatalf("%v %v", err, rows)
	}

	// A 500 before any bytes is retried; the copy is complete and single.
	fake.failNext = 1
	var buf bytes.Buffer
	n, err := c.Tests.DownloadArtifact(ctx, "t/1", "a2", &buf)
	if err != nil || n != 2 || !bytes.Equal(buf.Bytes(), []byte{0, 0xff}) {
		t.Fatalf("%v %d %v", err, n, buf.Bytes())
	}
	_, err = c.Tests.DownloadArtifact(ctx, "t/1", "missing", io.Discard)
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 404 {
		t.Fatalf("expected 404, got %v", err)
	}
	if err := c.Tests.DeleteArtifact(ctx, "t/1", "a1"); err != nil {
		t.Fatal(err)
	}
}

func TestOutputRoundTrip(t *testing.T) {
	fake := &fakeFiles{blobs: map[string][]byte{}}
	c := newTestClient(t, fake.handler(t), Options{})
	ctx := context.Background()

	if out, err := c.Tests.Output(ctx, "t/1"); err != nil || out != nil {
		t.Fatalf("expected no output, got %v %v", out, err)
	}
	if row, err := c.Tests.SetOutputText(ctx, "t/1", "line 1\n"); err != nil || row.String("filename") != "output.txt" {
		t.Fatalf("%v %v", err, row)
	}
	out, err := c.Tests.Output(ctx, "t/1")
	if err != nil || out.String("text") != "line 1\n" {
		t.Fatalf("%v %v", err, out)
	}
	log := filepath.Join(t.TempDir(), "run.log")
	os.WriteFile(log, []byte("from disk\n"), 0o600)
	if row, err := c.Tests.SetOutputFile(ctx, "t/1", log); err != nil || row.String("filename") != "run.log" {
		t.Fatalf("%v %v", err, row)
	}
	_, err = c.Tests.SetOutput(ctx, "t/1", "bin", bytes.NewReader([]byte{0xff}))
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 422 {
		t.Fatalf("expected 422, got %v", err)
	}
	if err := c.Tests.DeleteOutput(ctx, "t/1"); err != nil {
		t.Fatal(err)
	}
	if err := c.Tests.DeleteOutput(ctx, "t/1"); !errors.As(err, &apiErr) || apiErr.StatusCode != 404 {
		t.Fatalf("expected 404, got %v", err)
	}
}

func TestUploadsAreNeverRetried(t *testing.T) {
	calls := 0
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(503)
		fmt.Fprint(w, `{"detail":"down"}`)
	}, Options{Retries: 3})
	if _, err := c.Tests.SetOutputText(context.Background(), "t", "x"); err == nil || calls != 1 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}
