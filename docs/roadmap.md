# Roadmap

This document records possible future work. Items here are not committed to a
specific release or delivery date.

## Plugin-step screenshots

Optionally retain screenshots captured during Device Info and AI Reboot runs and
display them beneath the corresponding saved steps on the device detail page.

OMP exposes emitted browser screenshots through ACP
`tool_call_update.content` as an image content block containing a MIME type and
base64 data. It also reports the path of the image saved inside the disposable
worker pod. TestBench currently records only tool status and text output, so it
does not retain either form.

A future implementation should:

- collect image content in the Device Info and Reboot ACP adapters;
- validate MIME type, decoded size, dimensions, and image count before upload;
- upload captures before the short-lived worker pod is removed;
- store image bytes in object storage or another dedicated binary store, with
  only identifiers and metadata in PostgreSQL;
- associate each capture with its device, plugin, run, recipe version, and
  optionally the step that produced it;
- provide authenticated endpoints for thumbnails, full-size viewing, and
  deletion;
- display captures below Plugin Steps without embedding base64 data in the
  recipe JSON;
- define retention limits and cleanup behavior for superseded recipe versions;
- treat captures as sensitive data because device interfaces may reveal network
  details, serial numbers, usernames, session information, or credentials; and
- remain compatible with OMP versions that expose an internal
  `blob:sha256:...` reference instead of resolved base64 data.

The existing `captureImages` settings control instructions given to the agent;
they are not browser-tool sandbox controls. Screenshot storage should therefore
remain disabled unless explicitly enabled, and the upload path must enforce its
own limits regardless of agent behavior.
