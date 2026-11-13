import assert from "node:assert/strict";
import test from "node:test";
import { MAX_SVG_UPLOAD_BYTES, previewPathData, validateSvgFile } from "../src/svgUpload.js";

test("SVG files are accepted by type or extension and bounded in size", () => {
  assert.equal(validateSvgFile({ name: "heart.svg", type: "", size: 120 }), "");
  assert.equal(validateSvgFile({ name: "shape", type: "image/svg+xml", size: 120 }), "");
  assert.match(validateSvgFile({ name: "photo.png", type: "image/png", size: 120 }), /\.svg file/);
  assert.match(validateSvgFile({ name: "empty.svg", type: "image/svg+xml", size: 0 }), /empty/);
  assert.match(validateSvgFile({ name: "big.svg", type: "image/svg+xml", size: MAX_SVG_UPLOAD_BYTES + 1 }), /2 MB/);
  assert.match(validateSvgFile(null), /Choose/);
});

test("preview paths fit the box and flip y back to screen coordinates", () => {
  const [square, stroke] = previewPathData(
    [[[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]], [[0, 0], [0.5, 0.5]], [[1, 1]]],
    { size: 100, padding: 0 },
  );
  assert.equal(square, "M0 100 L100 100 L100 0 L0 0 L0 100");
  assert.equal(stroke, "M50 50 L100 0");
  assert.deepEqual(previewPathData([]), []);
});
