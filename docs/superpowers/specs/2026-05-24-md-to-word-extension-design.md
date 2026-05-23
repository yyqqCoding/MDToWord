# MD To Word Browser Extension Design

Date: 2026-05-24

## Goal

Build a browser extension that lets users paste AI-generated Markdown content and export it as a Word `.docx` document.

The content scope is intentionally narrow:

- Text
- Mathematical formulas
- Tables

The extension will not auto-detect content from AI websites. Users copy content themselves and paste it into the extension's editor.

The most important quality requirement is that formulas in the generated Word document are editable Word equations, not screenshots.

## Non-Goals

The first version will not support:

- Automatic extraction from ChatGPT, Claude, Kimi, Tongyi, or other AI pages
- Images
- Attachments
- PDF export
- Code block highlighting
- Nested tables
- Merged table cells
- Complex Word template editing
- Multi-document batch export

## Recommended Architecture

Use a browser extension for input, preview, configuration, and download. Use a conversion backend for high-quality Markdown to Word conversion.

The backend can be deployed in two modes:

- Local service, for privacy-sensitive users
- Cloud service, for easier product usage

Both modes expose the same HTTP API, so the extension only needs a configurable service URL.

## Main Components

### Browser Extension

The extension contains:

- A main editor page or side panel where users paste Markdown content
- A preview area for text, formulas, and tables
- An export button that sends the current Markdown to the conversion service
- A settings page for configuring the conversion service URL
- Local storage for saving the last service URL and optional draft content

The extension should use Manifest V3 for Chrome and Edge compatibility.

### Conversion Backend

The backend contains:

- A `/health` endpoint for service availability checks
- A `/convert` endpoint for generating `.docx`
- Markdown normalization before conversion
- Pandoc-based `.docx` generation
- Error handling that returns useful messages when formulas or tables cannot be converted

Python FastAPI is a good fit for the backend because it is simple to run locally and can also be deployed as a cloud API.

### Conversion Engine

Use Pandoc as the conversion engine for the first production-quality implementation.

Pandoc is responsible for:

- Markdown parsing
- LaTeX math parsing
- Word `.docx` generation
- Word-native table generation
- Converting formulas into Word-compatible math markup when exporting to `.docx`

The conversion command should be equivalent to:

```bash
pandoc input.md \
  --from markdown+tex_math_dollars+tex_math_single_backslash+pipe_tables+grid_tables \
  --to docx \
  --output result.docx
```

The implementation should call Pandoc through a controlled backend wrapper rather than exposing arbitrary command execution.

## Input Format

The supported input is Markdown with standard text, formulas, and tables.

Inline formula:

```md
质能方程是 $E = mc^2$。
```

Block formula:

```md
$$
\int_0^1 x^2 dx = \frac{1}{3}
$$
```

Table:

```md
| 名称 | 公式 | 说明 |
|---|---|---|
| 动能 | $E_k = \frac{1}{2}mv^2$ | 物体运动能量 |
| 质能方程 | $E = mc^2$ | 质量和能量关系 |
```

The extension should document this format inside developer docs, but the UI should avoid long instructional text.

## API Design

### `GET /health`

Checks whether the conversion service is available.

Response:

```json
{
  "status": "ok",
  "engine": "pandoc"
}
```

### `POST /convert`

Request:

```json
{
  "title": "example",
  "markdown": "Markdown content here",
  "options": {
    "filename": "example.docx"
  }
}
```

Response:

- Success: binary `.docx` file
- Failure: JSON error response

Error response:

```json
{
  "error": "conversion_failed",
  "message": "Pandoc failed to parse a formula near line 12.",
  "details": []
}
```

## Data Flow

1. User pastes Markdown into the extension editor.
2. Extension stores the draft locally.
3. Extension renders a browser preview of text, formulas, and tables.
4. User clicks export.
5. Extension validates that the conversion service URL is configured.
6. Extension sends Markdown to `/convert`.
7. Backend writes the Markdown to a temporary working directory.
8. Backend runs Pandoc with the supported Markdown extensions.
9. Backend returns the generated `.docx`.
10. Extension downloads the returned file.

## Formula Handling

Supported formula delimiters:

- `$...$`
- `$$...$$`
- `\(...\)`
- `\[...\]`

Before calling Pandoc, the backend should normalize formulas where needed:

- Convert `\(...\)` to `$...$`
- Convert `\[...\]` to `$$...$$`
- Ensure block formulas have blank lines around them
- Preserve LaTeX content without trying to rewrite complex math

The system should fail clearly when a formula cannot be converted. It should not silently replace editable formulas with formula images.

## Table Handling

The MVP supports standard Markdown pipe tables.

Grid tables can be supported by Pandoc if users provide them, but the main tested input should be pipe tables because AI tools commonly produce them.

The MVP does not support:

- Merged cells
- Nested tables
- Tables containing images
- Word-specific table styling controls

## UI Design

The extension should be a working tool rather than a landing page.

Primary screen:

- Header with app name and service status
- Editor area for Markdown input
- Preview area for parsed content
- Export button
- Settings button

Settings:

- Service URL field
- Health check button
- Save button

The UI should be compact and work-focused.

## Error Handling

Extension-side errors:

- Missing conversion service URL
- Service unavailable
- Conversion timeout
- Empty input
- Failed download

Backend-side errors:

- Pandoc not installed
- Pandoc execution timeout
- Invalid Markdown or LaTeX
- Temporary file write failure

Errors should be specific enough for users to fix input problems. Internal stack traces should be logged server-side, not shown directly in the extension UI.

## Testing Strategy

Backend tests:

- Health endpoint returns `ok`
- Plain Chinese text converts to `.docx`
- Inline formulas convert successfully
- Block formulas convert successfully
- Tables convert successfully
- Tables containing formulas convert successfully
- Invalid formulas produce structured errors

Extension tests:

- User can enter content
- Draft is retained locally
- Service URL can be saved
- Health check reports success/failure
- Export calls backend and downloads `.docx`
- Empty content prevents export

Manual acceptance tests:

- Open generated `.docx` in Word
- Confirm Chinese text is readable
- Confirm tables are Word-native tables
- Confirm formulas are editable Word equations

## Implementation Milestones

1. Create backend skeleton with FastAPI.
2. Add Pandoc-based conversion endpoint.
3. Add backend tests with representative Markdown fixtures.
4. Create extension skeleton with Manifest V3.
5. Build editor, preview, settings, and export flow.
6. Connect extension to local backend.
7. Add error handling and service health checks.
8. Verify generated Word files manually.
9. Add cloud deployment configuration.

## Open Decisions

The following decisions can be deferred until implementation:

- Whether the extension UI should be a popup, side panel, or full extension page
- Whether to use React/Vite or plain TypeScript for the extension
- Whether the cloud backend needs authentication in the first public version
- Whether to provide a custom Word reference document for default styling

## References

- Pandoc manual: https://pandoc.org/MANUAL.html
- Pandoc math documentation: https://pandoc.org/demo/example33/8.13-math.html
- Microsoft LaTeX and MathML support in Office math: https://learn.microsoft.com/en-us/office/math/latex
- Chrome Extensions documentation: https://developer.chrome.com/docs/extensions/
