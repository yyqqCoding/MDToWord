# MD To Word Extension

Browser extension UI for pasting Markdown and exporting Word documents through the conversion backend.

## Development

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

## Load in Chrome or Edge

1. Run `npm run build`.
2. Open `chrome://extensions` or `edge://extensions`.
3. Enable developer mode.
4. Load the `extension/dist` directory as an unpacked extension.
5. Click the extension icon to open the side panel.
6. Set the service URL to `http://127.0.0.1:8000`.

## Edge Store Release

1. Update the store version in `public/manifest.json`. Do not edit
   `dist/manifest.json` directly because the next build replaces it.
2. Run `npm run build`.
3. Confirm that `public/manifest.json` and `dist/manifest.json` contain the same
   version.
4. Reload `dist` as an unpacked extension and complete the production smoke
   test.
5. Create the store archive from the contents of `dist`, with `manifest.json`
   at the archive root. Build output and archives are not committed.
