const BASE = (import.meta.env?.VITE_API_BASE ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message,
    status = null,
    { requestId = null, retryAfter = null, category = "request" } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
    this.category = category;
    this.retryable = status == null || status === 408 || status === 429 || status >= 500;
  }
}

async function readGenerationStream(response, onUpdate) {
  const responseError = () => new ApiError(
    "The route planner sent an invalid response. Please try again.", response.status,
    { category: "response", requestId: response.headers.get("X-Request-ID") },
  );
  if (!response.body) {
    throw new ApiError("The route planner did not send progress or a result.", response.status, {
      category: "response",
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let result = null;
  const handleLine = (line) => {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw responseError();
    }
    if (!event || typeof event !== "object" || Array.isArray(event) || typeof event.type !== "string") {
      throw responseError();
    }
    if (event.type === "progress" || event.type === "preview") onUpdate?.(event);
    if (event.type === "result") {
      if (!event.data || typeof event.data !== "object" || Array.isArray(event.data)) throw responseError();
      result = event.data;
    }
    if (event.type === "error") {
      throw new ApiError(event.detail || "Route generation failed.", event.status ?? 500, {
        category: "service", requestId: response.headers.get("X-Request-ID"),
        retryAfter: response.headers.get("Retry-After"),
      });
    }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      let newline = pending.indexOf("\n");
      while (newline !== -1) {
        handleLine(pending.slice(0, newline));
        pending = pending.slice(newline + 1);
        newline = pending.indexOf("\n");
      }
      if (done) break;
    }
    if (pending.trim()) handleLine(pending);
  } catch (error) {
    // Releasing the lock alone leaves a failed response downloading in the background.
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  if (result == null) {
    throw new ApiError("The route planner stopped before returning a route.", response.status, {
      category: "response",
    });
  }
  return result;
}

async function request(
  path,
  { signal, timeoutMs = 15_000, timeoutMessage, onUpdate, ...options } = {},
) {
  const controller = new AbortController();
  let timedOut = false;
  let timeoutId = null;

  const forwardAbort = () => controller.abort(signal?.reason);
  if (signal?.aborted) {
    forwardAbort();
  } else {
    signal?.addEventListener("abort", forwardAbort, { once: true });
  }

  if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
    timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  try {
    const response = await fetch(`${BASE}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: onUpdate ? "application/x-ndjson, application/json" : "application/json",
        ...options.headers,
      },
    });
    const streaming = response.ok && response.headers.get("Content-Type")?.includes("application/x-ndjson");
    const data = streaming
      ? await readGenerationStream(response, onUpdate)
      : await response.json().catch(() => null);
    const requestId = response.headers.get("X-Request-ID");
    const retryAfter = response.headers.get("Retry-After");

    if (!response.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : null;
      throw new ApiError(
        detail || `Something went wrong while planning your route (${response.status}).`,
        response.status,
        {
          requestId,
          retryAfter,
          category: response.status === 429 ? "busy" : response.status >= 500 ? "service" : "request",
        },
      );
    }
    if (data == null) {
      throw new ApiError("We didn’t receive a route. Please try again.", response.status, {
        requestId,
        category: "response",
      });
    }

    return data;
  } catch (error) {
    if (timedOut) {
      throw new ApiError(
        timeoutMessage || "The service didn’t respond in time. Please try again.",
        null,
        { category: "timeout" },
      );
    }
    if (signal?.aborted) {
      throw new DOMException("The request was cancelled.", "AbortError");
    }
    if (error instanceof ApiError) throw error;
    if (error?.name === "AbortError") throw error;
    throw new ApiError(
      "We can’t reach the route planner. Check your connection and try again.",
      null,
      { category: "network" },
    );
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

export function health(options = {}) {
  return request("/health", { ...options, timeoutMs: 7_000 });
}

export function interpretRoute(prompt, options = {}) {
  const { payload = {}, ...requestOptions } = options;
  return request("/interpret", {
    ...requestOptions,
    timeoutMs: 20_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, ...payload }),
  });
}

export function generate(prompt, options = {}) {
  const { payload = {}, ...requestOptions } = options;
  return request("/generate", {
    ...requestOptions,
    // The 175-second workflow budget is advisory: the last in-flight ORS
    // request and response serialisation can finish slightly later.
    timeoutMs: 240_000,
    timeoutMessage: "Route planning took too long. Try a simpler drawing or a shorter distance.",
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, ...payload }),
  });
}

export function editRoute(payload, options = {}) {
  return request("/edit-route", {
    ...options,
    timeoutMs: 180_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function recordRouteAcceptance(payload, options = {}) {
  return request("/route-acceptance", {
    ...options,
    timeoutMs: 7_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function removeGalleryImage(payload, options = {}) {
  return request("/gallery/delete", {
    ...options,
    timeoutMs: 15_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function requestTimedReadiness(payload, options = {}) {
  return request("/timed-readiness", {
    ...options,
    timeoutMs: 8_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function createMuralPlan(payload, options = {}) {
  return request("/mural-plan", {
    ...options,
    timeoutMs: 180_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function repairRecognition(payload, options = {}) {
  return request("/recognition-repair", {
    ...options,
    timeoutMs: 180_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function analyseInkproof(payload, options = {}) {
  return request("/inkproof-analysis", {
    ...options,
    timeoutMs: 30_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function requestNightReadiness(payload, options = {}) {
  return request("/night-readiness", {
    ...options,
    timeoutMs: 35_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function requestRouteLandmarks(payload, options = {}) {
  return request("/route-landmarks", {
    ...options,
    timeoutMs: 35_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function fetchOccasions(options = {}) {
  const { daysAhead = 60, country, timezone, locale, ...requestOptions } = options;
  const params = new URLSearchParams({ days_ahead: String(daysAhead) });
  if (country) params.set("country", country);
  if (timezone) params.set("timezone", timezone);
  if (locale) params.set("locale", locale);
  return request(`/occasions?${params}`, {
    ...requestOptions,
    timeoutMs: 8_000,
  });
}

export function rescueArtwork(payload, options = {}) {
  return request("/art-rescue", {
    ...options,
    timeoutMs: 180_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function requestAccessibilityReadiness(payload, options = {}) {
  return request("/accessibility-readiness", {
    ...options,
    timeoutMs: 35_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function buildLessonPack(payload, options = {}) {
  return request("/lesson-pack", {
    ...options,
    timeoutMs: 15_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function fetchDestinations(options = {}) {
  return request("/destinations", {
    ...options,
    timeoutMs: 8_000,
  });
}

export function fetchShapeTemplates(options = {}) {
  return request("/shape-templates", {
    ...options,
    timeoutMs: 8_000,
  });
}

export function fetchShapePlacementPreview(
  { shape, city, sport = "run", distanceKm = 10 },
  options = {},
) {
  const params = new URLSearchParams({
    shape,
    city,
    sport,
    distance_km: String(distanceKm),
  });
  return request(`/shape-placement-preview?${params}`, {
    ...options,
    timeoutMs: 12_000,
  });
}

export function uploadSvgShape({ svgText, filename }, options = {}) {
  const params = new URLSearchParams({ filename });
  return request(`/shape-upload?${params}`, {
    ...options,
    timeoutMs: 15_000,
    method: "POST",
    headers: { "Content-Type": "image/svg+xml" },
    body: svgText,
  });
}

export function listGallery({ cursor = null, limit = 24, campaign = null, ...options } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  if (campaign) params.set("campaign", campaign);
  return request(`/gallery?${params}`, { ...options, timeoutMs: 15_000 });
}

export function publishGalleryImage(payload, options = {}) {
  return request("/gallery", {
    ...options,
    timeoutMs: 45_000,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
