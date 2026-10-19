package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func filesServer(t *testing.T, uploaded *[]string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "GET" && r.URL.Path == "/api/v1/tests/t1/artifacts":
			fmt.Fprint(w, `{"items":[{"id":"a1","filename":"report.zip","content_type":"application/zip","size_bytes":3}]}`)
		case r.Method == "POST" && r.URL.Path == "/api/v1/tests/t1/artifacts":
			_, header, err := r.FormFile("file")
			if err != nil {
				t.Fatal(err)
			}
			*uploaded = append(*uploaded, header.Filename)
			fmt.Fprintf(w, `{"id":"a%d","filename":%q,"size_bytes":1}`, len(*uploaded)+1, header.Filename)
		case r.Method == "GET" && r.URL.Path == "/api/v1/tests/t1/artifacts/a1/download":
			fmt.Fprint(w, "zip")
		case r.Method == "GET" && r.URL.Path == "/api/v1/tests/t1/output":
			fmt.Fprint(w, `{"output":{"filename":"run.log","size_bytes":6},"text":"hello\n","error":null}`)
		case r.Method == "GET" && r.URL.Path == "/api/v1/tests/t2/output":
			fmt.Fprint(w, `{"output":null,"text":null}`)
		case r.Method == "PUT" && r.URL.Path == "/api/v1/tests/t1/output":
			file, header, err := r.FormFile("file")
			if err != nil {
				t.Fatal(err)
			}
			data, _ := io.ReadAll(file)
			fmt.Fprintf(w, `{"filename":%q,"size_bytes":%d}`, header.Filename, len(data))
		case r.Method == "DELETE":
			w.WriteHeader(204)
		default:
			w.WriteHeader(404)
			fmt.Fprintf(w, `{"detail":"no route %s %s"}`, r.Method, r.URL.Path)
		}
	}
}

func TestArtifactCommands(t *testing.T) {
	var uploaded []string
	code, out, errOut := invoke(t, filesServer(t, &uploaded), "--test", "t1", "--artifacts")
	if code != 0 || !strings.Contains(out, "report.zip") || !strings.Contains(out, "a1") {
		t.Fatal(code, out, errOut)
	}

	dir := t.TempDir()
	for _, name := range []string{"one.txt", "two.pcap"} {
		os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o600)
	}
	code, out, errOut = invoke(t, filesServer(t, &uploaded), "--test", "t1",
		"--upload-artifact", filepath.Join(dir, "one.txt"), "--upload-artifact", filepath.Join(dir, "two.pcap"))
	if code != 0 || strings.Join(uploaded, ",") != "one.txt,two.pcap" || !strings.Contains(out, "two.pcap") {
		t.Fatal(code, out, errOut, uploaded)
	}

	// Into a directory, under the artifact's own name; never over an existing file.
	code, out, errOut = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--download-artifact", "a1", "--out", dir)
	saved, _ := os.ReadFile(filepath.Join(dir, "report.zip"))
	if code != 0 || string(saved) != "zip" || !strings.Contains(out, "saved") {
		t.Fatal(code, out, errOut)
	}
	if code, _, _ = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--download-artifact", "a1", "--out", dir); code != 1 {
		t.Fatal("overwrote an existing file")
	}
	code, out, _ = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--download-artifact", "a1", "--out", "-")
	if code != 0 || out != "zip" {
		t.Fatal(code, out)
	}
	if code, _, errOut = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--download-artifact", "nope", "--out", dir); code != 1 || !strings.Contains(errOut, "not found") {
		t.Fatal(code, errOut)
	}
	if code, out, _ = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--delete-artifact", "a1"); code != 0 || !strings.Contains(out, "deleted") {
		t.Fatal(code, out)
	}
}

func TestOutputCommands(t *testing.T) {
	var uploaded []string
	code, out, errOut := invoke(t, filesServer(t, &uploaded), "--test", "t1", "--output")
	if code != 0 || out != "hello\n" {
		t.Fatalf("%d %q %s", code, out, errOut)
	}
	code, out, _ = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--output", "--json")
	if code != 0 || !strings.Contains(out, `"run.log"`) {
		t.Fatal(code, out)
	}
	if code, _, errOut = invoke(t, filesServer(t, &uploaded), "--test", "t2", "--output"); code != 1 || !strings.Contains(errOut, "no output") {
		t.Fatal(code, errOut)
	}
	log := filepath.Join(t.TempDir(), "run.log")
	os.WriteFile(log, []byte("abc\n"), 0o600)
	code, out, errOut = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--set-output", log)
	if code != 0 || !strings.Contains(out, "run.log") || !strings.Contains(out, "4") {
		t.Fatal(code, out, errOut)
	}
	if code, out, _ = invoke(t, filesServer(t, &uploaded), "--test", "t1", "--delete-output"); code != 0 || !strings.Contains(out, "deleted output") {
		t.Fatal(code, out)
	}
}

func TestFileCommandValidation(t *testing.T) {
	for _, args := range [][]string{
		{"--artifacts"},
		{"--test", "t1"},
		{"--test", "t1", "--artifacts", "--output"},
		{"--test", "t1", "--artifacts", "--tests"},
		{"--test", "t1", "--output", "--search", "x"},
		{"--tests", "--out", "x"},
	} {
		code, _, _ := invoke(t, func(w http.ResponseWriter, r *http.Request) { t.Error("request sent for", args) }, args...)
		if code != 2 {
			t.Errorf("%v: exit %d, want 2", args, code)
		}
	}
}
