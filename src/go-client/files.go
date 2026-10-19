package testbench

import (
	"context"
	"fmt"
	"io"
	"mime"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// Files attached to a recorded test: any number of artifacts (reports,
// captures, screenshots) and one ASCII text output. The server stores both in
// its object store beside the test, under tests/<test-id>/artifacts/ and
// tests/<test-id>/output/, so object storage must be enabled there. Uploads and
// deletions need the tests.edit permission.

func testPath(testID string) string { return "/tests/" + url.PathEscape(testID) }

// Artifacts lists the files attached to a test, oldest first.
func (t *Tests) Artifacts(ctx context.Context, testID string) ([]Record, error) {
	var page struct {
		Items []Record `json:"items"`
	}
	if err := t.c.Request(ctx, "GET", testPath(testID)+"/artifacts", nil, nil, &page); err != nil {
		return nil, err
	}
	if page.Items == nil {
		page.Items = []Record{}
	}
	return page.Items, nil
}

// UploadArtifact attaches one file read from r. Duplicate filenames are kept as
// separate artifacts. The content type is guessed from the filename.
func (t *Tests) UploadArtifact(ctx context.Context, testID, filename string, r io.Reader) (Record, error) {
	if strings.TrimSpace(filename) == "" {
		return nil, fmt.Errorf("testbench: artifact filename is required")
	}
	kind := mime.TypeByExtension(filepath.Ext(filename))
	if kind == "" {
		kind = "application/octet-stream"
	}
	var out Record
	err := t.c.upload(ctx, "POST", testPath(testID)+"/artifacts", filepath.Base(filename), kind, r, &out)
	return out, err
}

// UploadArtifactFile attaches a file from disk under its own name.
func (t *Tests) UploadArtifactFile(ctx context.Context, testID, path string) (Record, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return t.UploadArtifact(ctx, testID, filepath.Base(path), f)
}

// DownloadArtifact copies an artifact's bytes into w.
func (t *Tests) DownloadArtifact(ctx context.Context, testID, artifactID string, w io.Writer) (int64, error) {
	return t.c.Download(ctx, testPath(testID)+"/artifacts/"+url.PathEscape(artifactID)+"/download", w)
}

// DeleteArtifact removes an artifact and its stored file.
func (t *Tests) DeleteArtifact(ctx context.Context, testID, artifactID string) error {
	return t.c.Request(ctx, "DELETE", testPath(testID)+"/artifacts/"+url.PathEscape(artifactID), nil, nil, nil)
}

// Output reads a test's output. The record carries "output" (filename and
// size), "text", and "error" when the stored file could not be read. It is nil
// when the test has no output.
func (t *Tests) Output(ctx context.Context, testID string) (Record, error) {
	var out Record
	if err := t.c.Request(ctx, "GET", testPath(testID)+"/output", nil, nil, &out); err != nil {
		return nil, err
	}
	if out["output"] == nil {
		return nil, nil
	}
	return out, nil
}

// SetOutput stores r as the test's output, replacing any it had. The server
// accepts only ASCII text within its size limit (1 MiB by default) and answers
// anything else with an APIError.
func (t *Tests) SetOutput(ctx context.Context, testID, filename string, r io.Reader) (Record, error) {
	if strings.TrimSpace(filename) == "" {
		filename = "output.txt"
	}
	var out Record
	err := t.c.upload(ctx, "PUT", testPath(testID)+"/output", filepath.Base(filename), "text/plain", r, &out)
	return out, err
}

// SetOutputText stores text as the test's output, named output.txt.
func (t *Tests) SetOutputText(ctx context.Context, testID, text string) (Record, error) {
	return t.SetOutput(ctx, testID, "output.txt", strings.NewReader(text))
}

// SetOutputFile stores a file from disk as the test's output, under its own name.
func (t *Tests) SetOutputFile(ctx context.Context, testID, path string) (Record, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return t.SetOutput(ctx, testID, filepath.Base(path), f)
}

// DeleteOutput removes the test's output; an APIError with status 404 means it
// had none.
func (t *Tests) DeleteOutput(ctx context.Context, testID string) error {
	return t.c.Request(ctx, "DELETE", testPath(testID)+"/output", nil, nil, nil)
}
