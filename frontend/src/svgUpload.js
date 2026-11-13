// Client-side checks and preview geometry for uploaded SVG shapes.
// The server re-validates everything; these checks only give fast feedback.

export const MAX_SVG_UPLOAD_BYTES = 2 * 1024 * 1024;

export function validateSvgFile(file) {
  if (!file) return "Choose an SVG file.";
  const name = String(file.name || "");
  const looksLikeSvg = file.type === "image/svg+xml" || /\.svg$/i.test(name);
  if (!looksLikeSvg) return "Choose an .svg file. Other image types can use an image link.";
  if (file.size === 0) return "This SVG file is empty.";
  if (file.size > MAX_SVG_UPLOAD_BYTES) return "SVG files must be 2 MB or smaller.";
  return "";
}

// Normalised server paths use y pointing up; SVG previews need y pointing down.
export function previewPathData(paths, { size = 120, padding = 10 } = {}) {
  const strokes = paths.filter(path => path.length >= 2);
  const points = strokes.flat();
  if (points.length === 0) return [];
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const minX = Math.min(...xs);
  const maxY = Math.max(...ys);
  const span = Math.max(Math.max(...xs) - minX, maxY - Math.min(...ys)) || 1;
  const scale = (size - padding * 2) / span;
  const format = value => Number(value.toFixed(2));
  return strokes.map(path => path
      .map(([x, y], index) => `${index === 0 ? "M" : "L"}${format(padding + (x - minX) * scale)} ${format(padding + (maxY - y) * scale)}`)
      .join(" "));
}
