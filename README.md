# Slitting Receiver App

A standalone Streamlit receiver app for Raspberry Pi screens.

## What it does

This app stays open on a Raspberry Pi monitor and keeps checking a shared JSON manifest.
When the manifest points the screen to a new PDF, the app reloads and shows the new PDF automatically.

## How it works

The app reads a JSON file from `MANIFEST_URL`.

Expected manifest format:

```json
{
  "version": 12,
  "displays": {
    "M1": {
      "job_code": "JOB-2026-001",
      "pdf_url": "https://your-domain.example.com/pdfs/JOB-2026-001.pdf",
      "version": 12,
      "sent_at": "2026-04-08T10:15:00Z",
      "message": "Ready for machine M1"
    },
    "M2": {
      "job_code": "JOB-2026-002",
      "pdf_url": "https://your-domain.example.com/pdfs/JOB-2026-002.pdf",
      "version": 5,
      "sent_at": "2026-04-08T10:20:00Z"
    }
  }
}
```

## Streamlit secret required

In the deployed app, add this secret:

```toml
MANIFEST_URL = "https://your-domain.example.com/receiver-manifest.json"
```

## URL parameters

You can use the same deployed app for both Raspberry Pis.

Examples:

- `...?machine=M1`
- `...?machine=M2`
- `...?machine=M1&refresh=3`

## Raspberry Pi setup idea

Open Chromium in kiosk mode with the deployed Streamlit URL.

Example idea:

- Pi screen 1 opens the app with `machine=M1`
- Pi screen 2 opens the app with `machine=M2`

## Important integration note

This receiver app only **reads** the current PDF assignment.
Your main slitting app must still do the sender side:

1. generate/save the PDF
2. make the PDF reachable by URL
3. update the shared JSON manifest for M1 and/or M2

## Suggested sender flow

When the user clicks **Send to Machine** in the main app:

- save the PDF with a unique filename
- upload or copy it to a public/internal URL the Pis can reach
- update `receiver-manifest.json`
- increase the `version`

Using a new version or timestamp is important to avoid browser caching.


## New in v2

You can now use either a Streamlit secret or a `manifest_url` URL parameter.
