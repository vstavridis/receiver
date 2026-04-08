# Receiver Pi v3

This version adds:

- fullscreen display mode
- auto refresh
- sound alert when a new version appears

## Deploy
Deploy this as a Streamlit app.

## URL examples

Monitor 1:
```text
https://receiver-pi.streamlit.app/?machine=M1&manifest_url=https://raw.githubusercontent.com/vstavridis/assets/main/manifest.json
```

Monitor 2:
```text
https://receiver-pi.streamlit.app/?machine=M2&manifest_url=https://raw.githubusercontent.com/vstavridis/assets/main/manifest.json
```

Disable sound:
```text
&sound=0
```

## Raspberry Pi kiosk

Use Chromium kiosk mode:
```bash
chromium-browser --kiosk "https://receiver-pi.streamlit.app/?machine=M1&manifest_url=https://raw.githubusercontent.com/vstavridis/assets/main/manifest.json"
```
