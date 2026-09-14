# Third-party software

TestBench is licensed under the MIT License. Its dependencies and container
components remain under their own licenses; the TestBench license does not
replace or restrict those terms.

The source manifests identify the complete dependency inputs. Notable runtime
components include:

| Component | License |
|---|---|
| AG Grid Community and Vue adapter | MIT |
| Vue, Vue Router, and Pinia | MIT |
| FastAPI and most Python application dependencies | MIT, BSD, Apache-2.0, ISC, or PSF |
| psycopg2 | LGPL-3.0-or-later with exceptions described in its license |
| Paramiko | LGPL-2.1 |
| certifi | MPL-2.0 |
| Kubernetes Python client | Apache-2.0 |
| MCP Python SDK | MIT |
| Debian, nginx, and Distroless components | Component-specific licenses |

The TestBench Python images retain the license files installed with their
Python distributions. The frontend image includes the license texts for its
production npm packages and Debian copyright notices for nginx and its copied
runtime libraries.

The Helm chart can launch a separately distributed `oh-my-pi` browser-agent
image. That image is not part of TestBench and remains subject to its own MIT
license and third-party notices. The same applies to any replacement image,
database, Kubernetes distribution, AI service, or other external system used
with TestBench.

This file is an overview, not a replacement for the complete license texts
included with each dependency or container component.
