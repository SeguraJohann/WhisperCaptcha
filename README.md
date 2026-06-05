# WhisperCaptcha

A remote service that solves reCAPTCHA v2 challenges by transcribing their audio challenge using [OpenAI Whisper](https://github.com/openai/whisper). The client sends a request with the necessary parameters, the service automates the captcha flow using a headless browser, transcribes the audio, and returns the solved token.

---

## Current Status

> **Planning phase.** No code has been written yet. This document defines the architecture, intended behavior, open questions, and the work that needs to be done before and during implementation.

---

## How It Works

reCAPTCHA v2 offers an audio accessibility alternative where the user listens to a distorted audio clip and types what they hear. WhisperCaptcha exploits this by:

1. Navigating to the target page with a headless browser (Playwright).
2. Clicking the audio challenge button inside the reCAPTCHA widget.
3. Downloading the `.mp3` audio file served by Google.
4. Transcribing it with Whisper (running locally on the server).
5. Submitting the transcription as the captcha answer.
6. Extracting the resulting `g-recaptcha-response` token from the DOM.
7. Returning the token to the client so it can submit its form.

The token has a validity window of approximately **2 minutes**. The client must use it immediately after receiving it.

---

## Architecture

```
Client (spider/bot)
        │
        │  POST /solve
        │  { url, sitekey, cookies, headers, proxy, ... }
        ▼
┌──────────────────────────────────────────────────┐
│                WhisperCaptcha API                │
│                                                  │
│  Browser Pool (persistent Chromium process)      │
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

### Browser Lifecycle Management

Playwright separates three levels: the **Chromium process** (heavy, ~1-3s startup), the **BrowserContext** (lightweight, ~50ms, fully isolated), and the **Page**. This distinction defines the service strategy:

| Level | Lifecycle | Time |
|---|---|---|
| `chromium.launch()` | Once when the server starts | ~1-3s |
| `browser.new_context(proxy, headers, cookies)` | One per request, closed when done | ~50ms |
| `context.new_page()` | One per context | ~10ms |

**Why contexts are not reused between requests:**
- Playwright does not allow changing the proxy of an already-created context.
- Reusing a context leaks cookies and storage between different clients.
- Creating and closing contexts is cheap; creating and closing browser processes is not.

### Estimated Time per Request

| Step | Estimated time |
|---|---|
| Create new BrowserContext | ~50ms |
| Load page + locate widget | 2-5s |
| Trigger audio challenge + download mp3 | 1-3s |
| Whisper transcription (`small` model) | 2-5s |
| Submit answer + extract token | 0.5-1s |
| **Total per request** | **~6-15s** |

Given that the token lives ~2 minutes, this is well within the acceptable window.

---

## API

### Request

**`POST /solve`**

| Parameter        | Type     | Required | Description                                         |
|------------------|----------|----------|-----------------------------------------------------|
| `url`            | `string` | Yes      | URL of the page containing the reCAPTCHA widget     |
| `sitekey`        | `string` | Yes      | `data-sitekey` attribute of the reCAPTCHA widget    |
| `cookies`        | `dict`   | No       | Session cookies to inject into the browser          |
| `headers`        | `dict`   | No       | Custom HTTP headers                                 |
| `proxy`          | `string` | No       | Proxy URL in `http://host:port` format              |
| `proxy_user`     | `string` | No       | Proxy username (if authentication is required)      |
| `proxy_password` | `string` | No       | Proxy password                                      |

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

---

## Execution Flow

1. Client sends `POST /solve` with the required parameters.
2. The service picks a browser from the pool (Chromium process already running).
3. A new `BrowserContext` is created with the request's proxy, cookies, and headers.
4. The service navigates to the target URL and locates the reCAPTCHA widget.
5. The audio challenge is triggered.
6. The `.mp3` audio file is downloaded.
7. Whisper transcribes the audio to text.
8. The transcription is submitted as the captcha answer.
9. The `g-recaptcha-response` token is extracted from the DOM.
10. The token is returned to the client, the context is closed, and the process ends.

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

## TO DO

### Blocking Research (must be done before writing code)

- [ ] **Understand the full reCAPTCHA v2 flow**
  - How is the widget loaded? Is it embedded in the page HTML or inside a Google-served iframe?
  - How many requests does it make to Google's servers on initialization?
  - Which endpoints are called to load and serve the audio challenge?
  - What is the exact DOM structure that exposes the resolved `g-recaptcha-response` token?

- [ ] **Analyze iframe behavior**
  - reCAPTCHA v2 renders its UI inside Google iframes. Confirm how many iframes are involved in the full flow (widget + challenge popup).
  - Determine how Playwright must switch frame context to interact with the challenge elements.

- [ ] **Double initialization problem**
  - Assess whether the client's spider having already loaded the page (and initialized the widget) causes a conflict when the service loads the same page again — potentially triggering a second initialization request to Google's servers that could invalidate the session or trigger detection.
  - Determine if the client needs a script on their spider's side to **block or defer** widget initialization (e.g., intercept `api.js` loading, prevent `grecaptcha.render()` from firing) so the service handles it fresh.
  - Explore whether it is viable to resolve the captcha using only the `sitekey` and URL without fully loading the client's page.

- [ ] **Study reCAPTCHA v2's implicit API**
  - Understand the parameters passed in iframe URLs: `k` (sitekey), `v` (widget version), `co` (encoded domain).
  - Assess whether it is possible to construct the challenge URLs directly without loading the client's page.

- [ ] **Whisper model selection**
  - Determine which Whisper model provides the best balance of speed and accuracy for reCAPTCHA audio (which is intentionally distorted).
  - Benchmark `base`, `small`, and `medium` against real audio challenge samples.

### Implementation

- [ ] Define and document the full API contract (request/response schemas).
- [ ] Implement the FastAPI skeleton.
- [ ] Implement the Playwright module for the captcha flow.
- [ ] Implement the audio download and Whisper transcription module.
- [ ] Handle proxy authentication within Playwright.
- [ ] Error handling and timeouts (challenge not loaded, audio unavailable, transcription rejected, etc.).
- [ ] Automatic retries if the submitted answer is rejected by reCAPTCHA.
- [ ] Implement the persistent Chromium browser pool (starts with the server).
- [ ] Define pool size based on expected concurrency and server resources.
- [ ] Dockerize the service.

### Security & Operations

- [ ] Endpoint authentication (API key, JWT, etc.) to prevent public access.
- [ ] Rate limiting to prevent abuse.
- [ ] Request and result logging.
- [ ] Define hosting infrastructure (VPS, cloud container, etc.).

---

## Open Questions

- **Double widget initialization**: This is the most critical open question before implementation. If the client's spider already triggered the reCAPTCHA widget when loading the page, and the service loads that same page again, Google may invalidate the challenge or flag anomalous behavior. The solution may require the client to intercept `api.js` loading on their end (e.g., via `page.route()` in their own Playwright setup), or the service may need to operate fully independently of the client's session.
- **Token validity window**: The `g-recaptcha-response` token expires in approximately 2 minutes. Clients must submit it immediately upon receipt.
