---
name: find-higher-res-image
description: Use Google Lens to reverse-search a JPG, PNG, WebP, or GIF, collect likely original-image sources, compare decoded dimensions and perceptual similarity, and safely replace a local low-resolution image after backing it up. Use when the user asks to search by image with Google or Google Lens, find the original or a clearer/higher-resolution copy, locate where an image appears online, replace a blurry image with a web copy, or says phrases such as "以图搜图", "Google Lens 搜图", "搜原图", "找高清图", "找更清晰版本", or "高清图替换".
---

# Find Higher-Resolution Image

Use Google Lens for discovery and the bundled Python script for deterministic validation and safe replacement. Keep discovery, verification, and replacement as separate stages.

## Prerequisites

- Require Python 3 and Pillow for candidate validation.
- Use the Browser or Chrome skill for Google Lens. Read the selected browser skill before browser actions.
- Prefer Chrome for local-file searches because Google hides the file input and automated chooser activation may fail in the in-app browser.
- Do not require an API key. Do not use unofficial Lens multipart upload endpoints; they are brittle and may return 403.

## Google Lens Discovery

### Public image URL

Open this URL through the browser, with the image URL percent-encoded:

```text
https://lens.google.com/uploadbyurl?url=<encoded-image-url>
```

This route can proceed without a native file chooser.

### Local image file

1. Open `https://lens.google.com/` in Chrome and click the image-search button.
2. Try the documented file-chooser flow once.
3. If Google keeps the input hidden or the chooser does not fire, keep the Lens tab as a handoff and ask the user to click **上传文件** and select the exact local source path. Resume when the user says it is uploaded.
4. Do not repeatedly retry hidden inputs or direct upload endpoints.

A Lens upload sends the image to Google. Proceed when the user explicitly asked to search that image with Google Lens. Require confirmation before uploading private, sensitive, medical, financial, or identifying imagery.

## Collect Original Candidates

1. Record the Lens results-page URL.
2. Inspect the strongest visually matching results. Open result pages and obtain the original/content image URL, such as a page's main image or `og:image`; do not treat a small Google thumbnail as the final candidate.
3. Collect up to 12 direct image URLs or download the candidate images locally. Record each source page for provenance.
4. Save a compact manifest when several candidates exist:

```json
{
  "lens_results_url": "https://www.google.com/search?...",
  "candidates": [
    {
      "image_url": "https://example.com/original.jpg",
      "source_page": "https://example.com/article",
      "title": "Example result"
    }
  ]
}
```

Reject search-result thumbnails, data URLs, hotlink-protected resources, unrelated alternate compositions, and pages that require bypassing access controls.

## Validate and Rank

Run a dry check before changing anything:

```powershell
python "<skill-dir>\scripts\find_higher_res_image.py" --file "<source-image>" --candidates-json "<workspace>\work\higher-res-image\candidates.json" --output-dir "<workspace>\work\higher-res-image"
```

For one or two candidates, pass repeated `--candidate-url` or `--candidate-path` values instead. For a public source image, use `--url` instead of `--file`.

Read `report.json` or `report.md`. A candidate is usable only when it is larger than the source, passes the similarity and aspect-ratio checks, and is visually the same intended image. Rank verified candidates by pixel area, then similarity.

Visually compare the source and selected candidate. Reject alternate crops, borders, watermarks, AI recreations, colorized versions, or materially edited images even when the numeric score passes.

## Replace Safely

Only replace when the user's request explicitly authorizes it. Rerun the same validation command with `--replace`:

```powershell
python "<skill-dir>\scripts\find_higher_res_image.py" --file "<source-image>" --candidates-json "<workspace>\work\higher-res-image\candidates.json" --output-dir "<workspace>\work\higher-res-image" --replace
```

The script creates a timestamped backup beside the source before replacement. Do not delete the backup unless the user explicitly asks.

By default, replace only with the same decoded format. Use `--allow-reencode` only when the user accepts possible metadata, transparency, or animation changes. Never re-encode an animated GIF automatically.

Report the old and new dimensions, similarity score, original candidate URL, source page, replacement path, and backup path. If nothing trustworthy is found, keep the original unchanged.

## Useful Options

- `--candidate-url <url>`: add a direct Lens candidate image URL; repeat as needed.
- `--candidate-path <file>`: add a locally downloaded candidate; repeat as needed.
- `--similarity-threshold 0.90`: tighten perceptual matching.
- `--min-area-ratio 1.25`: require at least 25% more pixels.
- `--max-aspect-delta 0.03`: tighten crop/aspect matching.
- `--lens-results-url <url>` and `--matching-page <url>`: preserve provenance.

Use `python "<skill-dir>\scripts\find_higher_res_image.py" --help` for the full interface.

## Failure Handling

- If Lens requests sign-in, CAPTCHA, or manual file selection, hand off the Chrome tab to the user and resume afterward.
- If automated upload fails once, do not retry the same hidden selector; switch to the manual chooser handoff.
- If a candidate host blocks downloads, retain its page URL and continue with other candidates.
- If Google changes the results layout, use visible result cards and their source pages rather than guessed selectors or undocumented endpoints.
