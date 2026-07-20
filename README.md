# Find Higher-Resolution Image for Codex

A Codex skill that uses Google Lens to discover clearer copies of an image, then verifies candidates locally before replacing anything.

## What it does

- Opens Google Lens for reverse-image discovery.
- Collects likely original-image candidates and their source pages.
- Reads the candidates' real decoded dimensions.
- Compares perceptual similarity and aspect ratio.
- Rejects smaller, unrelated, differently cropped, or suspicious candidates.
- Creates a timestamped backup before replacing a local image.
- Keeps the original unchanged when no trustworthy upgrade is found.

## Requirements

- Codex with Chrome or the in-app Browser available.
- Python 3.10 or newer.
- [Pillow](https://pypi.org/project/pillow/).

Install the Python dependency:

```powershell
python -m pip install Pillow
```

## Install in Codex

Ask Codex to install this directory:

```text
$skill-installer install https://github.com/abc5462009/find-higher-res-image/tree/main/skills/find-higher-res-image
```

Or run the bundled installer directly on Windows:

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" `
  --repo abc5462009/find-higher-res-image `
  --path skills/find-higher-res-image
```

Restart Codex after installation.

## Usage

Example prompt:

```text
Use $find-higher-res-image with Google Lens to find a higher-resolution version of this image and replace it safely.
```

Chinese example:

```text
用 $find-higher-res-image 通过 Google Lens 找这张图的高清版，验证后备份替换。
```

The skill separates discovery, verification, and replacement. It performs a dry validation first and replaces a file only when the user explicitly authorizes replacement.

## Candidate validator

The included Python script can also validate candidate files or direct image URLs without replacing the source:

```powershell
python skills/find-higher-res-image/scripts/find_higher_res_image.py `
  --file "path/to/source.png" `
  --candidate-url "https://example.com/candidate.png" `
  --output-dir "work/higher-res-image"
```

Use `--replace` only after reviewing the generated `report.json` or `report.md`:

```powershell
python skills/find-higher-res-image/scripts/find_higher_res_image.py `
  --file "path/to/source.png" `
  --candidate-url "https://example.com/candidate.png" `
  --output-dir "work/higher-res-image" `
  --replace
```

Run `python skills/find-higher-res-image/scripts/find_higher_res_image.py --help` for all options.

## Privacy and limitations

- Searching a local image with Google Lens uploads that image to Google. Do not upload sensitive or private images without the owner's permission.
- Google may require sign-in, CAPTCHA, or one manual click on **Upload a file** because the native file chooser can block automation.
- Google can change the Lens interface at any time. The skill intentionally avoids undocumented upload APIs and does not bypass access controls.
- Candidate validation is an aid, not proof of copyright ownership or permission to reuse an image. Check the source page and applicable license before redistribution.

This project is not affiliated with or endorsed by Google.

## Repository layout

```text
skills/find-higher-res-image/
├── SKILL.md
├── LICENSE.txt
├── requirements.txt
├── agents/openai.yaml
└── scripts/find_higher_res_image.py
```

## License

MIT License. See [LICENSE](LICENSE).
