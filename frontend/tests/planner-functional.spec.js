import { expect, test } from "playwright/test";

import {
  galleryAsset,
  installCommonMocks,
  mockGeneration,
  reviewAndFindRoutes,
} from "./support/functional-fixtures.js";

async function openOtherWays(page) {
  const panel = page.locator(".alternate-starts-panel");
  if (!(await panel.evaluate((element) => element.open))) {
    await panel.getByText("Other ways to start", { exact: true }).click();
  }
  return panel;
}

async function expectPreservedPrompt(page, value) {
  await page.getByRole("button", { name: "Change request" }).click();
  await expect(page.getByLabel("Drawing and location")).toHaveValue(value);
}

test.beforeEach(async ({ page }) => {
  await installCommonMocks(page);
});

test("primary navigation links reach each planner section", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { level: 1, name: "Create GPS art on real streets." }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Create route" })).toBeVisible();
  await expect(page.getByText("Step 1 of 3")).toBeVisible();
  await expect(page.getByText(/surprise me|need inspiration/i)).toHaveCount(0);
  await expect(page.getByText("Map data © OpenStreetMap contributors")).toHaveCount(0);
  expect(await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundImage))
    .toBe("none");
  await expect(page.getByRole("link", { name: "Skip to route planner" })).toHaveAttribute(
    "href",
    "#route-designer",
  );
  const plannerLink = page.locator('.site-header nav a[href="#route-designer"]');
  const galleryLink = page.locator('.site-header nav a[href="#gallery"]');
  await expect(plannerLink).toHaveAttribute("href", "#route-designer");
  await expect(galleryLink).toHaveAttribute("href", "#gallery");
  await expect(page.locator("#route-designer")).toBeAttached();
  await expect(page.locator("#gallery")).toHaveCount(0);

  if (await plannerLink.isVisible()) {
    await plannerLink.click();
    await expect.poll(() => new URL(page.url()).hash).toBe("#route-designer");
    await galleryLink.click();
    await expect.poll(() => new URL(page.url()).hash).toBe("#gallery");
    await expect(page.locator("#gallery")).toBeAttached();
    await expect(page.locator("#route-designer")).toHaveCount(0);
  }
});

test("the planner uses a compact responsive layout with optional panels collapsed", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.locator(".image-reference-panel")).not.toHaveAttribute("open", "");
  await expect(page.locator(".suggest-panel")).not.toHaveAttribute("open", "");
  await expect(page.locator(".map-placement-panel")).not.toHaveAttribute("open", "");
  await expect(page.locator(".route-setup-panel")).not.toHaveAttribute("open", "");
  await expect(page.locator(".alternate-starts-panel")).not.toHaveAttribute("open", "");
  await expect(page.getByText("Other ways to start")).toBeVisible();
  const spacing = await page.locator(".generator-stage").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      columnGap: Number.parseFloat(style.columnGap),
      paddingTop: Number.parseFloat(style.paddingTop),
      paddingBottom: Number.parseFloat(style.paddingBottom),
    };
  });

  expect(spacing.columnGap).toBeLessThanOrEqual(48);
  expect(spacing.paddingTop).toBeLessThanOrEqual(44);
  expect(spacing.paddingBottom).toBeLessThanOrEqual(44);
  const horizontalOverflow = await page.locator("body").evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  );
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
});

test("the homepage features a different public gallery image on each load", async ({ page }) => {
  const assets = [galleryAsset("a", "first-map"), galleryAsset("b", "second-map")];
  await page.route("**/gallery?limit=50", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ configured: true, assets, next_cursor: null }),
  }));
  await page.goto("/");
  const example = page.getByRole("figure", { name: "See what the streets inspire." });
  await expect(example).toBeVisible();
  await expect(example).toContainText("From the public gallery");
  await expect(example.locator("svg")).toHaveCount(0);
  const map = example.getByRole("img");
  await expect(map).toBeVisible();
  const firstSource = await map.getAttribute("src");
  expect(assets.map((asset) => asset.image_url)).toContain(firstSource);
  await expect(example.getByRole("link", { name: "© OpenStreetMap contributors" })).toHaveAttribute("href", "https://www.openstreetmap.org/copyright");
  await expect(example.getByRole("link", { name: /Open this public gallery image/ })).toHaveAttribute("href", firstSource);
  await page.reload();
  await expect(map).toBeVisible();
  await expect(map).not.toHaveAttribute("src", firstSource);
  expect(assets.map((asset) => asset.image_url)).toContain(await map.getAttribute("src"));
  const skip = page.getByRole("link", { name: "Skip to route planner" });
  await skip.focus();
  await expect(skip).toBeFocused();
  await expect(skip).toHaveCSS("clip-path", "none");
});

test("the homepage never labels an empty gallery as a Budapest heart", async ({ page }) => {
  await page.goto("/");
  const example = page.getByRole("figure", { name: "See what the streets inspire." });
  await expect(example).toContainText("No community maps have been shared yet");
  await expect(example.getByRole("img")).toHaveCount(0);
  await expect(example).not.toContainText("Budapest");
});

test("the header mark and favicon share one scalable route identity", async ({ page }) => {
  await page.goto("/");

  const home = page.getByRole("link", { name: "Paceasso GPS Art Wizzard home" });
  const mark = home.locator(".brand-mark");
  const markPath = await mark.locator("path").getAttribute("d");
  await expect(home).toHaveAttribute("href", "/");
  await expect(mark).toHaveAttribute("aria-hidden", "true");
  await expect(mark.locator("svg")).toHaveAttribute("viewBox", "0 0 48 48");
  await expect(mark.locator("rect")).toHaveCount(1);
  await expect(mark.locator("circle")).toHaveCount(2);
  await expect(page.locator(".brand-mark i")).toHaveCount(0);

  const faviconResponse = await page.request.get(new URL("/favicon.svg", page.url()).href);
  expect(faviconResponse.ok()).toBe(true);
  const favicon = await faviconResponse.text();
  const faviconPath = favicon.match(/<path d="([^"]+)"/)?.[1];
  const compactPath = (value) => value?.replace(/\s+/g, "");
  expect(compactPath(faviconPath)).toBe(compactPath(markPath));
  expect(favicon.match(/<circle\b/g)).toHaveLength(2);
});

test("alternative starts keep visual, DOM, and keyboard order aligned", async ({ page }) => {
  await page.goto("/");
  await openOtherWays(page);

  const prompt = page.getByLabel("Drawing and location");
  const initialPrompt = await prompt.inputValue();
  const mapPanel = page.locator(".map-placement-panel");
  const simplePanel = page.locator(".suggest-panel");
  const imagePanel = page.locator(".image-reference-panel");
  const mapSummary = mapPanel.locator(":scope > summary");
  const simpleSummary = simplePanel.locator(":scope > summary");
  const imageSummary = imagePanel.locator(":scope > summary");
  const domOrder = await page.evaluate(() => {
    const map = document.querySelector(".map-placement-panel");
    const simple = document.querySelector(".suggest-panel");
    const image = document.querySelector(".image-reference-panel");
    return Boolean(
      map
        && simple
        && (map.compareDocumentPosition(simple) & Node.DOCUMENT_POSITION_FOLLOWING)
        &&
      simple
        && image
        && (simple.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING),
    );
  });
  expect(domOrder).toBe(true);

  const mapBox = await mapPanel.boundingBox();
  const simpleBox = await simplePanel.boundingBox();
  const imageBox = await imagePanel.boundingBox();
  expect(mapBox).not.toBeNull();
  expect(simpleBox).not.toBeNull();
  expect(imageBox).not.toBeNull();
  expect(mapBox.y).toBeLessThan(simpleBox.y);
  expect(simpleBox.y).toBeLessThan(imageBox.y);

  await mapSummary.focus();
  await expect(mapSummary).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(simpleSummary).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(mapSummary).toBeFocused();
  await simpleSummary.focus();
  await expect(simpleSummary).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(imageSummary).toBeFocused();

  await simpleSummary.click();
  await expect(page.getByLabel("City", { exact: true })).toBeVisible();
  await simpleSummary.click();
  await imageSummary.click();
  await expect(page.getByLabel("Direct image URL")).toBeVisible();
  await expect(prompt).toHaveValue(initialPrompt);
  await expect(page.locator(".result, .loading-card")).toHaveCount(0);
});

test("a selected shape can be positioned on the map before street fitting", async ({ page }) => {
  await page.route("**/shape-templates", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        count: 2,
        shapes: [
          { id: "heart", label: "Heart" },
          { id: "star", label: "Star" },
        ],
      }),
    }),
  );
  await page.route("**/shape-placement-preview*", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        shape: "star",
        label: "Star",
        closed: true,
        paths: [[
          [0, 0.5], [0.12, 0.12], [0.48, 0.12], [0.2, -0.08],
          [0.3, -0.45], [0, -0.22], [-0.3, -0.45], [-0.2, -0.08],
          [-0.48, 0.12], [-0.12, 0.12], [0, 0.5],
        ]],
        city: "Budapest",
        city_substituted: false,
        center: [47.4979, 19.0402],
        city_bbox: [47.45, 47.56, 18.95, 19.15],
        scale_m: 2600,
        rotation_deg: 12,
        distance_km: 12,
        sport: "run",
      }),
    }),
  );
  const capture = await mockGeneration(page);
  await page.goto("/");

  await openOtherWays(page);
  await page.getByText("Place a shape on the map").click();
  const mapPanel = page.locator(".map-placement-panel");
  await mapPanel.getByLabel("Shape").selectOption("star");
  await mapPanel.getByLabel("Target distance", { exact: true }).fill("12");
  await mapPanel.getByRole("button", { name: "Open placement map" }).click();

  const dialog = page.getByRole("dialog", { name: "Position the star" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".shape-placement-outline")).toHaveCount(1);
  await dialog.getByLabel("Footprint size").fill("3000");
  await dialog.getByLabel("Rotation").fill("45");
  await dialog.getByLabel("Nearby fine-tuning").fill("700");
  await dialog.getByRole("button", { name: "Fit to streets and create GPX" }).click();

  await expect(page.locator(".result")).toBeVisible();
  await expect.poll(() => capture.lastPayload()).toMatchObject({
    prompt: "star in Budapest, running, about 12 km",
    map_placement: {
      center_lat: 47.4979,
      center_lon: 19.0402,
      scale_m: 3000,
      rotation_deg: 45,
      search_radius_m: 700,
    },
  });
  expect(capture.lastPayload().start_point).toBeUndefined();
  expect(capture.lastPayload().start_address).toBeUndefined();
});

test("primary planner controls keep a 44 pixel activation floor", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Review request", exact: true })).toBeVisible();

  const activationFloor = 44;
  const subpixelRenderingTolerance = 0.01;

  const controls = page.locator([
    ".brand",
    ".site-header nav a",
    ".idea-chip",
    ".generate-button",
    ".suggest-panel > summary",
    ".image-reference-panel > summary",
  ].join(", "));
  const visibleBoxes = [];
  for (const control of await controls.all()) {
    if (await control.isVisible()) visibleBoxes.push(await control.boundingBox());
  }
  expect(visibleBoxes.length).toBeGreaterThan(8);
  for (const box of visibleBoxes) {
    expect(box).not.toBeNull();
    expect(box.width).toBeGreaterThanOrEqual(activationFloor - subpixelRenderingTolerance);
    expect(box.height).toBeGreaterThanOrEqual(activationFloor - subpixelRenderingTolerance);
  }
});

test("the route idea exposes its help and character count to assistive technology", async ({
  page,
}) => {
  await page.goto("/");

  const prompt = page.getByLabel("Drawing and location");
  await expect(prompt).toHaveAttribute("aria-describedby", "prompt-help prompt-count");
  await expect(page.locator("#prompt-help")).toContainText("a flying pig in Budapest");
  await expect(page.locator("#prompt-count")).toHaveText(`${(await prompt.inputValue()).length}/320`);
  await expect(prompt).toBeFocused();
});

test("choosing a popular idea updates the prompt and selected state", async ({ page }) => {
  await page.goto("/");

  const ideas = page.locator(".idea-picker .idea-chip");
  await ideas.nth(0).click();
  await expect(ideas.nth(0)).toHaveAttribute("aria-pressed", "true");
  const firstPrompt = await page.getByLabel("Drawing and location").inputValue();
  await ideas.nth(1).click();
  await expect(ideas.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(ideas.nth(0)).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("Drawing and location")).not.toHaveValue(firstPrompt);
  await expect(page.getByLabel("Drawing and location")).toBeFocused();
});

test("refreshing ideas keeps the edited request and shows six unseen choices", async ({ page }) => {
  await page.goto("/");
  const prompt = page.getByLabel("Drawing and location");
  await prompt.fill("a fish in Szeged, running, 10 km");
  const seen = new Set();
  const refresh = page.getByRole("button", { name: "Show different ideas" });
  for (let batch = 0; batch < 4; batch += 1) {
    const labels = await page.locator(".idea-picker .idea-chip").allTextContents();
    expect(labels).toHaveLength(6);
    for (const label of labels) { expect(seen.has(label)).toBe(false); seen.add(label); }
    if (batch % 2 === 0) {
      await refresh.focus();
      await refresh.press("Enter");
      await expect(refresh).toBeFocused();
    } else {
      await refresh.click();
    }
    await expect(page.locator(".idea-refresh [role=status]")).toContainText(`Idea set ${batch + 2}.`);
    await expect(prompt).toHaveValue("a fish in Szeged, running, 10 km");
  }
  await expect(page.locator(".idea-refresh [role=status]")).toContainText("Your request is unchanged");
  await page.locator(".idea-picker").screenshot({ path: test.info().outputPath("fresh-ideas.png") });
});

test("the full idea catalogue exposes every category and all 158 options", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".idea-catalog .idea-chip")).toHaveCount(0);
  expect(await page.locator("*").count()).toBeLessThan(1451);
  await page.getByText("More shapes, letters, and numbers").click();

  await expect(page.locator(".idea-group").getByRole("button")).toHaveCount(158);
  for (const category of [
    "Hungarian ideas",
    "Simple shapes",
    "Nature",
    "Animals",
    "Objects",
    "Symbols",
    "Letters, numbers & text",
  ]) {
    await expect(page.getByRole("region", { name: `${category} ideas` })).toBeVisible();
  }
});

test("the expanded catalogue filters by name and selects a detailed shape", async ({ page }) => {
  await page.goto("/");
  await page.getByText("More shapes, letters, and numbers").click();

  const filter = page.getByLabel("Filter options");
  await filter.fill("turtle");
  await expect(page.locator(".idea-filter")).toContainText("1 option");
  await expect(page.locator(".idea-group").getByRole("button")).toHaveCount(1);
  await page.locator(".idea-catalog").getByRole("button", { name: "Turtle", exact: true }).click();
  await expect(page.getByLabel("Drawing and location")).toHaveValue(
    "a turtle run in Keszthely, about 14 km",
  );

  await filter.fill("not-a-shape");
  await expect(page.getByText("No matching shapes.", { exact: false })).toBeVisible();
  await expect(page.locator(".idea-group").getByRole("button")).toHaveCount(0);
});

test("a custom free-text drawing is submitted without catalog selection", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");

  await page.getByLabel("Drawing and location").fill(
    "an octopus wearing a crown in Budapest, running, 12 km",
  );
  await reviewAndFindRoutes(page);

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "an octopus wearing a crown in Budapest, running, 12 km",
    intent_override: { shape: "star", city: "Debrecen", sport: "bike", distance_km: 20 },
  });
});

test("bug is submitted as a shape without exposing a letter B interpretation", async ({ page }) => {
  const capture = await mockGeneration(page);
  let interpretationRequests = 0;
  await page.route("**/interpret", async (route) => {
    interpretationRequests += 1;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({}),
    });
  });
  await page.goto("/");

  const prompt = page.getByLabel("Drawing and location");
  await prompt.fill("a bug run in Tatabánya, about 8 km");
  await expect(page.getByText("Live understanding")).toHaveCount(0);
  await expect(page.getByText("Letter B", { exact: true })).toHaveCount(0);
  expect(capture.requests).toHaveLength(0);

  await reviewAndFindRoutes(page);
  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "a bug run in Tatabánya, about 8 km",
  });
  expect(interpretationRequests).toBe(1);
});

test("route setup sends an address, first heading, and street priorities", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await page.getByLabel("Drawing and location").fill("a heart run in Tatabánya, about 8 km");

  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByText("Route setup", { exact: true }).click();
  await page.getByRole("radio", { name: "Address or place" }).check();
  await page.getByLabel("Start address or place").fill("Hősök tere, Budapest");
  await page.getByRole("radio", { name: "East", exact: true }).check();
  await page.getByRole("checkbox", { name: "Avoid steps" }).check();
  await page.getByRole("checkbox", { name: "Prefer green ways" }).check();
  await page.getByRole("button", { name: "Find routes" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "a heart run in Tatabánya, about 8 km",
    intent_override: { shape: "star", city: "Debrecen", sport: "bike", distance_km: 20 },
    start_address: "Hősök tere, Budapest",
    start_direction_deg: 90,
    route_preferences: {
      avoid_steps: true,
      avoid_ferries: false,
      avoid_fords: false,
      prefer_quiet: false,
      prefer_green: true,
    },
  });
});

test("route setup is organized into clear steps and can be reset", async ({ page }) => {
  await page.goto("/");
  await openOtherWays(page);

  const panel = page.locator(".route-setup-panel");
  await panel.getByText("Route setup", { exact: true }).click();
  await expect(panel.getByRole("heading", { name: "Choose the start" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Set the first heading" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Choose street priorities" })).toBeVisible();

  await panel.getByRole("radio", { name: "Address or place" }).check();
  await panel.getByLabel("Start address or place").fill("Hősök tere, Budapest");
  await panel.getByRole("radio", { name: "North", exact: true }).check();
  await panel.getByRole("checkbox", { name: "Avoid ferries" }).check();
  await expect(panel.locator(":scope > summary")).toContainText("3 active");
  await expect(panel.locator(":scope > summary")).toContainText(
    "Address start · North · 1 street preference",
  );

  await panel.getByRole("button", { name: "Reset setup" }).click();

  await expect(panel.getByRole("radio", { name: "Flexible start" })).toBeChecked();
  await expect(panel.getByRole("radio", { name: "Any direction" })).toBeChecked();
  await expect(panel.getByRole("checkbox", { name: "Avoid ferries" })).not.toBeChecked();
  await expect(panel.getByLabel("Start address or place")).toHaveCount(0);
  await expect(panel.locator(":scope > summary")).toContainText("Flexible start");
  await expect(panel.getByRole("button", { name: "Reset setup" })).toHaveCount(0);
});

test("route setup also reaches structured route suggestions", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);

  const setup = page.locator(".route-setup-panel");
  await setup.getByText("Route setup", { exact: true }).click();
  await setup.getByRole("radio", { name: "North", exact: true }).check();
  await setup.getByRole("checkbox", { name: "Avoid fords" }).check();

  const suggestion = page.locator(".suggest-panel");
  await suggestion.getByText("Choose city, activity, and distance").click();
  await suggestion.getByLabel("City", { exact: true }).selectOption("Győr");
  await suggestion.getByRole("radio", { name: "Running" }).check();
  await suggestion.getByLabel("Distance", { exact: true }).fill("12");
  await suggestion.getByRole("button", { name: "Find a route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "suggest a run route in Győr, about 12 km",
    start_direction_deg: 0,
    route_preferences: {
      avoid_steps: false,
      avoid_ferries: false,
      avoid_fords: true,
      prefer_quiet: false,
      prefer_green: false,
    },
  });
});

test("request review confirms interpretation without starting route generation", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.route("**/interpret", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        prompt: "a heart run in Budapest, about 8 km",
        intent: { shape: "heart", city: "Budapest", sport: "run", distance_km: 8 },
        drawing_label: "heart",
        drawing_kind: "template",
        defaults_applied: [],
        confidence: { drawing: 1, city: 1, sport: 1, distance: 1 },
        needs_clarification: false,
        clarifications: [],
      }),
    }),
  );
  await page.goto("/");

  await page.getByLabel("Drawing and location").fill("a heart run in Budapest, about 8 km");
  await page.getByRole("button", { name: "Review request" }).click();

  const check = page.locator(".request-review-card");
  await expect(check).toContainText("We’ll plan heart");
  await expect(check).toContainText("Budapest");
  await expect(check).toContainText("8 km");
  expect(capture.requests).toHaveLength(0);
});

test("current location replaces an entered address and reaches generation", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition(success) {
          success({ coords: { latitude: 47.497913, longitude: 19.040236 } });
        },
      },
    });
  });
  const capture = await mockGeneration(page);
  await page.goto("/");
  const initialPrompt = await page.getByLabel("Drawing and location").inputValue();
  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByText("Route setup", { exact: true }).click();
  await page.getByRole("radio", { name: "Address or place" }).check();
  await page.getByLabel("Start address or place").fill("Hősök tere, Budapest");

  await page.getByRole("radio", { name: "Current location" }).check();
  await page.getByRole("button", { name: "Use my location" }).click();

  await expect(page.getByLabel("Start address or place")).toHaveCount(0);
  await expect(page.getByText("Current location selected for this request only.")).toBeVisible();
  await page.getByRole("button", { name: "Find routes" }).click();
  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: initialPrompt,
    intent_override: { shape: "star", city: "Debrecen", sport: "bike", distance_km: 20 },
    start_point: {
      latitude: 47.497913,
      longitude: 19.040236,
      label: "Current location",
    },
  });
});

test("a supported image link can generate an AI route in a selected city", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");

  await openOtherWays(page);
  const imagePanel = page.locator(".image-reference-panel");
  await imagePanel.getByText("Use your own SVG or an image link").click();
  await imagePanel.getByLabel("Direct image URL").fill(
    "https://www.premiumsvg.com/wimg1/mug-icon.webp",
  );
  await imagePanel.getByLabel("Destination").selectOption("Pécs");
  await imagePanel.getByLabel("Travel mode").selectOption("bike");
  await imagePanel.getByLabel("Length").fill("24");
  await imagePanel.getByRole("button", { name: "Generate AI route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "a custom image in Pécs, cycling, about 24 km",
    reference_image_url: "https://www.premiumsvg.com/wimg1/mug-icon.webp",
  });
  await expectPreservedPrompt(
    page,
    "a custom image in Pécs, cycling, about 24 km",
  );
});

test("an uploaded SVG is previewed and generates a walking route from its outline", async ({ page }) => {
  const capture = await mockGeneration(page);
  const uploads = [];
  await page.route("**/shape-upload?**", async (route) => {
    uploads.push({ url: route.request().url(), body: route.request().postData() });
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        shape: "heart",
        label: "Heart",
        closed: true,
        paths: [[[0, -0.5], [-0.5, 0.2], [0, 0.5], [0.5, 0.2], [0, -0.5]]],
        path_count: 1,
        point_count: 5,
      }),
    });
  });
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M5 9 L1 3 L5 1 L9 3 Z"/></svg>';
  await page.goto("/");

  await openOtherWays(page);
  const imagePanel = page.locator(".image-reference-panel");
  await imagePanel.getByText("Use your own SVG or an image link").click();
  await imagePanel.getByLabel("Upload your own SVG shape").setInputFiles({
    name: "heart.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from(svg),
  });

  await expect(imagePanel.getByRole("img", { name: "Outline of Heart" })).toBeVisible();
  expect(uploads).toHaveLength(1);
  expect(new URL(uploads[0].url).searchParams.get("filename")).toBe("heart.svg");
  expect(uploads[0].body).toBe(svg);
  await expect(imagePanel.getByLabel(/Direct image URL/)).toBeDisabled();

  await imagePanel.getByLabel("Destination").selectOption("Pécs");
  await imagePanel.getByLabel("Travel mode").selectOption("walk");
  await imagePanel.getByLabel("Length").fill("6");
  await imagePanel.getByRole("button", { name: "Generate route from SVG" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "a custom image in Pécs, walking, about 6 km",
    reference_svg: svg,
    reference_svg_name: "heart.svg",
  });
});

test("a non-SVG upload is rejected before contacting the planner", async ({ page }) => {
  let uploads = 0;
  await page.route("**/shape-upload?**", async (route) => {
    uploads += 1;
    await route.abort();
  });
  await page.goto("/");

  await openOtherWays(page);
  const imagePanel = page.locator(".image-reference-panel");
  await imagePanel.getByText("Use your own SVG or an image link").click();
  await imagePanel.getByLabel("Upload your own SVG shape").setInputFiles({
    name: "photo.png",
    mimeType: "image/png",
    buffer: Buffer.from("not really a png"),
  });

  await expect(imagePanel.getByRole("alert")).toContainText("Choose an .svg file");
  expect(uploads).toBe(0);
});

test("mouse submission of an empty idea shows a persistent focused error", async ({ page }) => {
  await page.goto("/");
  const prompt = page.getByLabel("Drawing and location");
  await prompt.fill("   ");
  await page.getByRole("button", { name: "Review request" }).click();

  await expect(prompt).toBeFocused();
  await expect(prompt).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#prompt-error")).toContainText("Enter a route idea");
});

test("punctuation-only route ideas are identified beside the prompt", async ({ page }) => {
  await page.goto("/");
  const prompt = page.getByLabel("Drawing and location");
  await prompt.fill("!? ♥");
  await prompt.blur();

  await expect(prompt).toHaveAttribute("aria-errormessage", "prompt-error");
  await expect(page.locator("#prompt-error")).toHaveText(
    /Include a shape, word, letter, or number to draw/,
  );
  await expect(prompt).toHaveValue("!? ♥");
});

test("correcting a malformed route idea clears its error before submission", async ({ page }) => {
  await page.goto("/");
  const prompt = page.getByLabel("Drawing and location");
  await prompt.fill("!!!");
  await prompt.blur();
  await expect(page.locator("#prompt-error")).toBeVisible();

  await prompt.fill("letter A in Eger");
  await expect(page.locator("#prompt-error")).toHaveCount(0);
  await expect(prompt).toHaveAttribute("aria-invalid", "false");
  await expect(prompt).not.toHaveAttribute("aria-errormessage", /.+/);
});

test("running suggestions enforce both ends of the supported distance range", async ({ page }) => {
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();
  const distance = page.getByLabel("Distance", { exact: true });

  await expect(page.locator("#suggest-distance-help")).toHaveText("3 to 60 km for running.");
  await distance.fill("2");
  await distance.blur();
  await expect(page.locator("#suggest-distance-error")).toContainText("from 3 to 60 km");
  await distance.fill("61");
  await expect(page.locator("#suggest-distance-error")).toContainText("from 3 to 60 km");
  await distance.fill("60");
  await expect(page.locator("#suggest-distance-error")).toHaveCount(0);
});

test("suggestion distance distinguishes missing and fractional values", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();
  const distance = page.getByLabel("Distance", { exact: true });

  await distance.fill("");
  await page.getByRole("button", { name: "Find a route" }).click();
  await expect(page.locator("#suggest-distance-error")).toContainText(
    "Enter a distance in kilometres.",
  );
  expect(capture.requests).toHaveLength(0);

  await distance.fill("8.5");
  await expect(page.locator("#suggest-distance-error")).toContainText(
    "Enter the distance in whole kilometres.",
  );
  expect(capture.requests).toHaveLength(0);
});

test("a valid structured suggestion submits the selected city, activity, and distance", async ({
  page,
}) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();
  await page.getByLabel("City", { exact: true }).selectOption("Győr");
  await page.getByRole("radio", { name: "Running" }).check();
  await page.getByLabel("Distance", { exact: true }).fill("12");
  await page.getByRole("button", { name: "Find a route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "suggest a run route in Győr, about 12 km",
  });
  await expectPreservedPrompt(
    page,
    "suggest a run route in Győr, about 12 km",
  );
});

test("the major-city list submits a new Hungarian city without manual prompt editing", async ({
  page,
}) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();

  const city = page.getByLabel("City", { exact: true });
  await expect(city.locator("option")).toHaveCount(230);
  await city.selectOption("Szolnok");
  await page.getByRole("radio", { name: "Cycling" }).check();
  await page.getByLabel("Distance", { exact: true }).fill("24");
  await page.getByRole("button", { name: "Find a route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "suggest a bike route in Szolnok, about 24 km",
  });
  await expectPreservedPrompt(
    page,
    "suggest a bike route in Szolnok, about 24 km",
  );
});

test("the expanded Europe group submits a newly catalogued accented destination", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();

  const city = page.getByLabel("City", { exact: true });
  await expect(city.locator('optgroup[label="Europe"] option')).toHaveCount(136);
  await expect(page.locator("#suggest-city-help")).toHaveCount(0);
  await city.selectOption("Timișoara");
  await page.getByRole("radio", { name: "Running" }).check();
  await page.getByLabel("Distance", { exact: true }).fill("14");
  await page.getByRole("button", { name: "Find a route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "suggest a run route in Timișoara, about 14 km",
  });
  await expectPreservedPrompt(
    page,
    "suggest a run route in Timișoara, about 14 km",
  );
});

test("the Balaton shore group submits a local accented settlement", async ({ page }) => {
  const capture = await mockGeneration(page);
  await page.goto("/");
  await openOtherWays(page);
  await page.getByText("Choose city, activity, and distance").click();

  const city = page.getByLabel("City", { exact: true });
  await expect(city.locator('optgroup[label="Lake Balaton shore"] option')).toHaveCount(44);
  await expect(city.locator('option[value="Siófok"]')).toHaveCount(1);
  await city.selectOption("Kővágóörs");
  await page.getByRole("radio", { name: "Cycling" }).check();
  await page.getByLabel("Distance", { exact: true }).fill("22");
  await page.getByRole("button", { name: "Find a route" }).click();

  await expect.poll(() => capture.lastPayload()).toEqual({
    prompt: "suggest a bike route in Kővágóörs, about 22 km",
  });
  await expectPreservedPrompt(
    page,
    "suggest a bike route in Kővágóörs, about 22 km",
  );
});
