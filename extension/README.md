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
