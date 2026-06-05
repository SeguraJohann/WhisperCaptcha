# WhisperCaptcha

A remote service that solves reCAPTCHA v2 challenges by transcribing their audio challenge using [OpenAI Whisper](https://github.com/openai/whisper). The client sends a request with the necessary parameters, the service automates the captcha flow using a headless browser, transcribes the audio, and returns the solved token.

---

## Current Status

> **Architecture defined. Ready to implement.** All blocking research has been resolved and documented below. The development plan is established. Next step: implement Phase 1.

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
- `v` — widget version hash. Changes with Google deployments. Retrieved by fetching `https://www.google.com/recaptcha/api.js` and extracting the release hash from the redirect or inline content.

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
3. The audio URL is exposed in the DOM as an anchor tag inside the bframe: `a#audio-source` or via the `src` attribute of the `<audio>` element.
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
│  │  isolated proxy / cookies / headers        │  │
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
| `browser.new_context(proxy, headers, cookies)` | One per request, closed when done | ~50ms |
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
- Makes `cookies` and `headers` parameters proxy-only concerns, not needed for page loading.

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

The `co` parameter in the iframe URLs is derived from the `url` field provided in the request (scheme + hostname + port), ensuring Google validates the token against the correct origin.

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

> Note: `cookies` and `headers` parameters were removed. The direct widget loading approach does not load the client's page, so injecting client session state is not applicable.

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

| Variable       | Default  | Description                                                              |
|----------------|----------|--------------------------------------------------------------------------|
| `WHISPER_MODEL`| `small`  | Whisper model to use. Options: `tiny`, `base`, `small`, `medium`, `large` |
| `API_KEY`      | *(unset)*| If set, all requests must include `X-API-Key: {value}` header            |
| `LOG_LEVEL`    | `info`   | Logging level. Options: `debug`, `info`, `warning`, `error`              |

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
3. A minimal local HTML page containing the reCAPTCHA widget is served and loaded.
4. The service waits for the anchor iframe to render and the checkbox to become clickable.
5. The audio challenge button is clicked inside the bframe iframe.
6. The `.mp3` audio URL is extracted from the bframe DOM and downloaded.
7. Whisper transcribes the audio to text.
8. The transcription is typed into the bframe's answer input and submitted.
9. If rejected, retry up to N times (re-triggering the audio challenge).
10. Once accepted, the `g-recaptcha-response` token is read from the host page's textarea.
11. The token is returned to the client. The BrowserContext is closed.

---

## Proposed Tech Stack

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
- [ ] Define directory structure.
- [ ] Set up `pyproject.toml` (or `requirements.txt`) with all dependencies.
- [ ] FastAPI app skeleton with `POST /solve` endpoint.
- [ ] Pydantic request/response schemas.
- [ ] Environment variable configuration loading.
- [ ] API key middleware (reads `API_KEY` env var, no-op if unset).

### Phase 2 — Playwright module
- [ ] Browser lifecycle: launch Chromium on startup, shut down on exit.
- [ ] `BrowserContext` creation per request with optional proxy.
- [ ] Widget version fetcher: GET `https://www.google.com/recaptcha/api.js` and extract version hash `v`.
- [ ] `co` parameter encoder: base64url-encode the origin from the `url` field.
- [ ] Minimal HTML page builder with the widget.
- [ ] Navigate and wait for anchor iframe to be ready.
- [ ] Switch to bframe and trigger audio challenge.
- [ ] Extract `.mp3` URL from bframe DOM and download it.

### Phase 3 — Whisper module
- [ ] Load Whisper model on startup (avoid per-request load time).
- [ ] Transcribe `.mp3` bytes to text string.
- [ ] Clean transcription output (lowercase, strip punctuation) before submission.

### Phase 4 — Integration and robustness
- [ ] Submit transcription in bframe input and confirm.
- [ ] Extract token from host page textarea after submission.
- [ ] Retry logic: if reCAPTCHA rejects the answer, re-trigger the audio challenge and retry (max 3 attempts).
- [ ] Timeout handling at each step (widget load, audio load, token appearance).
- [ ] Structured error responses for each failure mode.

### Phase 5 — Operability
- [ ] Request logging (method, status, duration).
- [ ] Dockerfile: base image, system deps (Chromium, ffmpeg), Python deps, Whisper model download at build time.
- [ ] `docker-compose.yml` for local development.

---

## Open Questions

- **Rate limiting**: not yet designed. To be addressed when the hosting strategy is defined.
- **Whisper on GPU**: if deployed on a GPU-enabled host, the `WHISPER_MODEL` env var should be set to `base` or `small` with CUDA enabled. No code changes required — Whisper auto-detects CUDA if available.
- **Retry budget**: the maximum number of retries before returning an error is not yet defined. 3 is a reasonable starting point.
