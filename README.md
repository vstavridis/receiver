# Receiver app

Deploy this folder as a separate Streamlit app.

## What it does

- reads a public `manifest.json`
- looks up the entry for the machine id (`M1`, `M2`, etc.)
- shows the assigned PDF
- auto-refreshes every few seconds

## Receiver URL examples

For Raspberry Pi monitor 1:

```text
https://receiver-pi.streamlit.app/?machine=M1&manifest_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json
```

For Raspberry Pi monitor 2:

```text
https://receiver-pi.streamlit.app/?machine=M2&manifest_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json
```

## Alternative: Streamlit secret

In the receiver app secrets:

```toml
MANIFEST_URL = "https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json"
```

Then use URLs like:

```text
https://receiver-pi.streamlit.app/?machine=M1
https://receiver-pi.streamlit.app/?machine=M2
```

## Raspberry Pi setup idea

Open Chromium in kiosk mode with the receiver URL for each machine.

Example:

```bash
chromium-browser --kiosk "https://receiver-pi.streamlit.app/?machine=M1&manifest_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json"
```
