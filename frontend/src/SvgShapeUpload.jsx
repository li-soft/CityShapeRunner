import { useId, useRef, useState } from "react";
import { uploadSvgShape } from "./api.js";
import { previewPathData, validateSvgFile } from "./svgUpload.js";

// Picks an SVG by file input or drag and drop, validates it on the server and
// shows the normalised outline the planner will route.
export default function SvgShapeUpload({ value, onChange, disabled = false }) {
  const inputId = useId();
  const helpId = useId();
  const errorId = useId();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  async function acceptFile(file) {
    const problem = validateSvgFile(file);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const svgText = await file.text();
      const preview = await uploadSvgShape({ svgText, filename: file.name });
      onChange({ filename: file.name, svgText, preview });
    } catch (uploadError) {
      onChange(null);
      setError(uploadError?.message || "This SVG could not be read.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function clear() {
    onChange(null);
    setError("");
    inputRef.current?.focus();
  }

  const unavailable = disabled || busy;
  return (
    <div className="svg-upload">
      <div
        className={`svg-upload-drop${dragging ? " svg-upload-drop--active" : ""}`}
        onDragOver={(event) => {
          if (unavailable) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!unavailable) acceptFile(event.dataTransfer.files?.[0]);
        }}
      >
        <label htmlFor={inputId}>
          <span>Upload your own SVG shape</span>
          <small>Drop an .svg file here or choose one</small>
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          accept=".svg,image/svg+xml"
          onChange={(event) => acceptFile(event.target.files?.[0])}
          aria-describedby={error ? `${helpId} ${errorId}` : helpId}
          aria-invalid={Boolean(error)}
          disabled={unavailable}
        />
      </div>
      <p id={helpId} className="field-help">
        A single outline works best, such as a heart or a country border. Paths, polygons and
        polylines are supported, up to 2 MB.
      </p>
      <p className="svg-upload-status" aria-live="polite">
        {busy ? "Reading SVG…" : ""}
      </p>
      {error && (
        <p id={errorId} className="field-error" role="alert">
          <span aria-hidden="true">!</span>
          {error}
        </p>
      )}
      {value && (
        <figure className="svg-upload-preview">
          <svg viewBox="0 0 120 120" role="img" aria-label={`Outline of ${value.preview.label}`}>
            {previewPathData(value.preview.paths).map((d, index) => (
              <path key={index} d={d} />
            ))}
          </svg>
          <figcaption>
            <strong>{value.preview.label}</strong>
            <span>
              {value.preview.path_count} {value.preview.path_count === 1 ? "stroke" : "strokes"}
              {value.preview.closed ? " · closed outline" : ""}
            </span>
            <button type="button" className="button button--quiet" onClick={clear} disabled={disabled}>
              Remove SVG
            </button>
          </figcaption>
        </figure>
      )}
    </div>
  );
}
