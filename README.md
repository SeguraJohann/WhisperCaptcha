# WhisperCaptcha

A remote service that solves reCAPTCHA v2 challenges by transcribing their audio challenge using [OpenAI Whisper](https://github.com/openai/whisper). The client sends a request with the necessary parameters, the service automates the captcha flow using a headless browser, transcribes the audio, and returns the solved token.

---

## Current Status

> **Phases 1–4 and 6 complete.** Solver, HTTP API, and demo module are all implemented. Phase 5 (operability) is pending but not blocking.
>
> **Active bug:** the solver fails with `"Timeout waiting for reCAPTCHA elements"` after opening the audio challenge. Likely cause: the `audio#audio-source` selector in `_download_audio` does not match the current reCAPTCHA bframe DOM. Next step: inspect the bframe in a visible browser (`BROWSER_HEADLESS=false`) to find the correct selector.

---

## Directory Structure

```
solver/               — Standalone solver. No HTTP dependencies.
  browser.py          — Chromium lifecycle (start, stop, get_browser)
  captcha.py          — Full captcha solving flow
  transcriber.py      — Whisper model load and transcription
  __main__.py         — CLI entry point: python -m solver --url ... --sitekey ...

server/               — HTTP API layer. Wraps the solver with FastAPI.
  main.py             — FastAPI app, lifespan, POST /solve endpoint
  schemas.py          — Pydantic request/response models
  auth.py             — API key dependency
  config.py           — Environment variable loading

demo/                 — End-to-end demo. Requires the server to be running.
  __main__.py         — Opens a visible browser, reads sitekey, delegates to server, injects token
```

The `solver/` package has no dependency on `server/`. It can be used standalone, imported as a library, or called via the CLI without running the HTTP server.

---

## How to Run

### Prerequisites

```bash
pip install -r requirements.txt
playwright install chromium
# ffmpeg must also be installed and on PATH (required by Whisper)
```

### As a standalone script

```bash
python -m solver --url https://example.com --sitekey YOUR_SITEKEY
# prints the token to stdout
```

Optional flags: `--proxy http://host:port`, `--proxy-user`, `--proxy-password`, `--model small`

### As an HTTP server

```bash
uvicorn server.main:app
```

To run with a visible browser (useful for debugging the captcha flow):

```bash
$env:BROWSER_HEADLESS="false"; uvicorn server.main:app  # PowerShell
BROWSER_HEADLESS=false uvicorn server.main:app          # bash
```

Then send requests:

```bash
curl -X POST http://localhost:8000/solve \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "sitekey": "YOUR_SITEKEY"}'
```

### As a demo (end-to-end visible test)

With the server running in another terminal:

```bash
python -m demo --url https://www.google.com/recaptcha/api2/demo
```

Optional flags: `--server http://localhost:8000`, `--api-key YOUR_KEY`

---

## How reCAPTCHA v2 Works

Understanding the internals is necessary to implement the solver correctly.

### Widget Loading

The host page includes a script tag:
```html
<script src="https://www.google.com/recaptcha/api.js" async defer></script>
<div class="g-recaptcha" data-sitekey="YOUR_SITEKEY"></div>
```

When `api.js` loads, it:
1. Reads the current widget version (`v`) by embedding a versioned hash in the script URL (e.g., `api.js/releases/abc123/`).
2. Renders two iframes into the page DOM.

### The Two Iframes

reCAPTCHA v2 renders its entire UI inside two Google-served iframes. The host page's own DOM is only used for the final token.

| Iframe | URL pattern | Purpose |
|--------|-------------|---------|
| **Anchor** | `https://www.google.com/recaptcha/api2/anchor?ar=1&k={sitekey}&co={co}&v={v}&...` | The visible checkbox widget |
| **Bframe** | `https://www.google.com/recaptcha/api2/bframe?hl=en&v={v}&k={sitekey}` | The challenge popup (images or audio) |

**URL parameters:**
- `k` — the sitekey (`data-sitekey` attribute).
- `co` — base64url-encoded origin. Format: `base64url("{scheme}://{host}:{port}")`. Example: `https://example.com:443` → `aHR0cHM6Ly9leGFtcGxlLmNvbTo0NDM=`.
- `v` — widget version hash. Changes with Google deployments.

### The Token

When the challenge is solved, reCAPTCHA injects the `g-recaptcha-response` token into a hidden `<textarea>` in the **host page's DOM** (not inside any iframe). The selector is:

```css
textarea[name="g-recaptcha-response"]
```

The token is a long JWT-like string (e.g., `03AGdBq...`) and is valid for approximately **2 minutes**. The client must submit it to their target form immediately.

### Audio Challenge Flow

Inside the bframe:
1. Click the audio challenge button (headphone icon).
2. Google serves an `.mp3` file with a distorted spoken string (e.g., `"six two nine four"`).
3. The audio URL is exposed in the DOM via the `src` attribute of the `<audio>` element.
4. Download the `.mp3`, transcribe it with Whisper, type the result into the bframe's text input, and submit.
5. If accepted, Google writes the token to the host page's textarea.

---

## Architecture

```
Client (spider/bot)
        │
        │  POST /solve
        │  { url, sitekey, proxy, ... }
        ▼
┌──────────────────────────────────────────────────┐
│                WhisperCaptcha API                │
│                                                  │
│  Single Chromium process (started with server)   │
│  ┌────────────────────────────────────────────┐  │
│  │  BrowserContext (new per request)          │  │
│  │  isolated proxy                            │  │
│  │                                            │  │
│  │  ┌──────────┐        ┌──────────────────┐  │  │
│  │  │  Page    │ mp3 →  │    Whisper       │  │  │
│  │  │Playwright│        │  Transcriber     │  │  │
│  │  └──────────┘        └──────────────────┘  │  │
│  │       │                      │             │  │
│  │  captcha flow          transcription       │  │
│  │                              │             │  │
│  │               submit answer → token        │  │
│  └────────────────────────────────────────────┘  │
│  context.close()  ← cleanup on finish            │
└──────────────────────────────────────────────────┘
        │
        │  { token: "03AGdBq..." }
        ▼
Client (spider/bot)
```

### Browser Lifecycle

Playwright separates three levels: the **Chromium process** (heavy, ~1-3s startup), the **BrowserContext** (lightweight, ~50ms, fully isolated), and the **Page**.

| Level | Lifecycle | Time |
|---|---|---|
| `chromium.launch()` | Once when the server starts | ~1-3s |
| `browser.new_context(proxy)` | One per request, closed when done | ~50ms |
| `context.new_page()` | One per context | ~10ms |

**Why contexts are not reused between requests:**
- Playwright does not allow changing the proxy of an already-created context.
- Reusing a context leaks cookies and storage between different clients.
- Creating and closing contexts is cheap; creating and closing browser processes is not.

### Scaling Strategy

The service runs as a single-instance container with one Chromium process. Concurrency is achieved by running multiple container replicas behind a load balancer (e.g., Kubernetes pods, Docker Swarm services, or any container orchestration layer). No internal browser pool is needed — horizontal scaling handles concurrency.

### Widget Loading Strategy (Direct Approach)

The service does **not** navigate to the client's page. Instead, it constructs a minimal local HTML page that contains only the reCAPTCHA widget, using the `sitekey` and `url` parameters provided by the client. This approach:

- Eliminates the double initialization problem (the client's spider may have already loaded the widget on the same page, which would trigger duplicate initialization requests to Google and risk detection or session invalidation).
- Removes the dependency on the client's page structure, cookies, and session state.

The minimal HTML page:
```html
<!DOCTYPE html>
<html>
<head></head>
<body>
  <div class="g-recaptcha" data-sitekey="{sitekey}"></div>
  <script src="https://www.google.com/recaptcha/api.js" async defer></script>
</body>
</html>
```

The page is served via `page.route()` at the client's target URL, so the browser's origin matches the client's domain. The reCAPTCHA script reads `window.location.origin` to derive the `co` parameter automatically.

### Estimated Time per Request

| Step | Estimated time |
|---|---|
| Create new BrowserContext | ~50ms |
| Load minimal HTML + widget initialization | 2-4s |
| Trigger audio challenge + download mp3 | 1-3s |
| Whisper transcription (`small` model, CPU) | 2-5s |
| Submit answer + extract token | 0.5-1s |
| **Total per request** | **~6-14s** |

---

## API

### Request

**`POST /solve`**

| Parameter        | Type     | Required | Description                                                    |
|------------------|----------|----------|----------------------------------------------------------------|
| `url`            | `string` | Yes      | URL of the target page (used to derive the `co` parameter)     |
| `sitekey`        | `string` | Yes      | `data-sitekey` attribute of the reCAPTCHA widget               |
| `proxy`          | `string` | No       | Proxy URL in `http://host:port` format                         |
| `proxy_user`     | `string` | No       | Proxy username (if authentication is required)                 |
| `proxy_password` | `string` | No       | Proxy password                                                 |

### Response

**Success:**
```json
{
  "status": "ok",
  "token": "03AGdBq..."
}
```

**Error:**
```json
{
  "status": "error",
  "message": "description of the error"
}
```

### Authentication

Requests must include an API key in the `X-API-Key` header. Authentication is controlled by the `API_KEY` environment variable. If `API_KEY` is not set, authentication is disabled (useful for local development and deployments where the network perimeter handles access control).

---

## Configuration

All runtime configuration is done via environment variables.

| Variable           | Default  | Description                                                              |
|--------------------|----------|--------------------------------------------------------------------------|
| `WHISPER_MODEL`    | `small`  | Whisper model to use. Options: `tiny`, `base`, `small`, `medium`, `large` |
| `API_KEY`          | *(unset)*| If set, all requests must include `X-API-Key: {value}` header            |
| `LOG_LEVEL`        | `info`   | Logging level. Options: `debug`, `info`, `warning`, `error`              |
| `BROWSER_HEADLESS` | `true`   | Set to `false` to run the solver's Chromium in visible mode (for debugging) |

**Whisper model tradeoffs (for reCAPTCHA audio):**

| Model    | Speed (CPU) | Accuracy on distorted audio | Recommended use |
|----------|-------------|----------------------------|-----------------|
| `tiny`   | <1s         | Low                        | Not recommended |
| `base`   | ~1s         | Moderate                   | High-throughput, GPU available |
| `small`  | 2-5s        | Good                       | **Default. Best balance.** |
| `medium` | 5-10s       | Marginally better          | Not justified for this use case |

---

## Execution Flow

1. Client sends `POST /solve` with `url`, `sitekey`, and optional proxy parameters.
2. The service creates a new `BrowserContext` from the persistent Chromium process, with the request's proxy configuration if provided.
3. A minimal local HTML page containing the reCAPTCHA widget is served via `page.route()` at the client's URL, so the origin matches.
4. The service waits for the anchor iframe to render and clicks the checkbox.
5. The audio challenge button is clicked inside the bframe iframe.
6. The `.mp3` audio URL is extracted from the bframe DOM and downloaded.
7. Whisper transcribes the audio to text (runs in a thread executor to avoid blocking the event loop).
8. The transcription is typed into the bframe's answer input and submitted.
9. If rejected, the audio challenge is reloaded and retried (max 3 attempts).
10. Once accepted, the `g-recaptcha-response` token is read from the host page's textarea.
11. The token is returned to the client. The BrowserContext is closed.

---

## Tech Stack

| Component     | Technology             |
|---------------|------------------------|
| API           | FastAPI                |
| Automation    | Playwright (Python)    |
| Transcription | OpenAI Whisper (local) |
| Server        | Uvicorn                |
| Container     | Docker                 |

---

## Development Plan

### Phase 1 — Project skeleton
- [x] Define directory structure.
- [x] Set up `pyproject.toml` and `requirements.txt` with all dependencies.
- [x] FastAPI app skeleton with `POST /solve` endpoint.
- [x] Pydantic request/response schemas.
- [x] Environment variable configuration loading.
- [x] API key middleware (reads `API_KEY` env var, no-op if unset).

### Phase 2 — Playwright module
- [x] Browser lifecycle: launch Chromium on startup, shut down on exit.
- [x] `BrowserContext` creation per request with optional proxy.
- [x] Minimal HTML page builder with the widget.
- [x] Navigate and wait for anchor iframe to be ready.
- [x] Switch to bframe and trigger audio challenge.
- [x] Extract `.mp3` URL from bframe DOM and download it.

> Note: The `v` (widget version) and `co` (origin) parameters are derived automatically by the reCAPTCHA script when it loads in the browser. No manual extraction needed with the direct widget loading approach.

### Phase 3 — Whisper module
- [x] Load Whisper model on startup (avoid per-request load time).
- [x] Transcribe `.mp3` bytes to text string.
- [x] Clean transcription output (lowercase, strip punctuation) before submission.

### Phase 4 — Integration and robustness
- [x] Submit transcription in bframe input and confirm.
- [x] Extract token from host page textarea after submission.
- [x] Retry logic: if reCAPTCHA rejects the answer, re-trigger the audio challenge and retry (max 3 attempts).
- [x] Timeout handling at each step (widget load, audio load, token appearance).
- [x] Structured error responses for each failure mode.

### Phase 5 — Operability
- [ ] Request logging (method, status, duration).
- [ ] Dockerfile: base image, system deps (Chromium, ffmpeg), Python deps, Whisper model download at build time.
- [ ] `docker-compose.yml` for local development.

### Phase 6 — Test / Demo module

The goal is a visible end-to-end demo that proves the solver works in a real browser context, simulating exactly how a client spider would use it.

**Concept:**

A Playwright script opens a real target page in a **non-headless** (visible) browser. The script intercepts the page's own reCAPTCHA initialization, extracts the sitekey from the DOM, and delegates solving to the WhisperCaptcha server. Once the token is returned, it is injected back into the page.

**Flow:**

1. Open the target URL in a visible Chromium browser.
2. Intercept the reCAPTCHA script (`api.js`) via `page.route()` to block it from loading — this prevents the widget from initializing on the client side, avoiding the double initialization problem.
3. Detect the `data-sitekey` attribute on the `div.g-recaptcha` element in the page DOM.
4. Send a `POST /solve` request to the WhisperCaptcha server with the page URL and sitekey.
5. Wait for the server to return the token.
6. Inject the token into `textarea[name="g-recaptcha-response"]` in the page DOM.
7. *(To be developed)* Submit the form or trigger the next step in the scraping flow.

**Implementation tasks:**
- [x] Script that opens a configurable URL in a visible browser.
- [x] Route handler that intercepts `api.js` and blocks it.
- [x] DOM inspector that reads `data-sitekey` from the page after load.
- [x] HTTP client that calls `POST /solve` and waits for the token.
- [x] Token injector: writes the token into the page's hidden textarea.
- [ ] *(Future)* Form submission or post-token action logic.

---

## Open Questions

- **Rate limiting**: not yet designed. To be addressed when the hosting strategy is defined.
- **Whisper on GPU**: if deployed on a GPU-enabled host, the `WHISPER_MODEL` env var should be set to `base` or `small` with CUDA enabled. No code changes required — Whisper auto-detects CUDA if available.
