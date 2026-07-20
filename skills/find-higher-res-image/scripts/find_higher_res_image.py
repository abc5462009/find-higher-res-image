#!/usr/bin/env python3
"""Rank Google Lens candidates and safely replace a lower-resolution source."""

from __future__ import annotations

import argparse
from datetime import datetime
import io
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required. Install it locally with: python -m pip install Pillow"
    ) from exc


USER_AGENT = "Codex-Google-Lens-Higher-Res/2.0"
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


class SkillError(RuntimeError):
    """Expected, user-actionable failure."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download or read image candidates collected from Google Lens, rank them by "
            "decoded dimensions and perceptual similarity, and optionally replace a local source."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Local JPG, PNG, WebP, or GIF source")
    source.add_argument("--url", help="Direct public source-image URL")
    parser.add_argument(
        "--candidate-url",
        action="append",
        default=[],
        help="Direct candidate image URL from Lens; repeat for multiple candidates",
    )
    parser.add_argument(
        "--candidate-path",
        action="append",
        type=Path,
        default=[],
        help="Downloaded local candidate path; repeat for multiple candidates",
    )
    parser.add_argument(
        "--candidates-json",
        type=Path,
        help="JSON list/object containing candidate URLs, paths, and optional source pages",
    )
    parser.add_argument(
        "--matching-page",
        action="append",
        default=[],
        help="Lens result/source page URL to preserve in the report",
    )
    parser.add_argument("--lens-results-url", help="Google Lens results-page URL for provenance")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "work" / "higher-res-image",
        help="Directory for candidates and reports (default: ./work/higher-res-image)",
    )
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--similarity-threshold", type=float, default=0.88)
    parser.add_argument("--min-area-ratio", type=float, default=1.10)
    parser.add_argument("--max-aspect-delta", type=float, default=0.05)
    parser.add_argument("--max-download-mb", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Back up and replace the local source with the best verified candidate",
    )
    parser.add_argument(
        "--allow-reencode",
        action="store_true",
        help="Allow replacement when candidate and source formats differ",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.max_results <= 50:
        raise SkillError("--max-results must be between 1 and 50")
    if not 0.0 <= args.similarity_threshold <= 1.0:
        raise SkillError("--similarity-threshold must be between 0 and 1")
    if args.min_area_ratio <= 1.0:
        raise SkillError("--min-area-ratio must be greater than 1")
    if not 0.0 <= args.max_aspect_delta <= 1.0:
        raise SkillError("--max-aspect-delta must be between 0 and 1")
    if args.max_download_mb < 1:
        raise SkillError("--max-download-mb must be positive")
    if args.replace and args.file is None:
        raise SkillError("--replace requires --file; URL sources cannot be replaced")
    if args.allow_reencode and not args.replace:
        raise SkillError("--allow-reencode is only valid together with --replace")
    if args.candidates_json and not args.candidates_json.is_file():
        raise SkillError(f"Candidates JSON not found: {args.candidates_json}")


def google_lens_url(image_url: str) -> str:
    return "https://lens.google.com/uploadbyurl?url=" + urllib.parse.quote(image_url, safe="")


def normalized_format(value: str | None) -> str:
    result = (value or "").upper()
    return "JPEG" if result == "JPG" else result


def flattened_pixels(image: Image.Image) -> list[int]:
    getter = getattr(image, "get_flattened_data", None)
    return list(getter()) if getter is not None else list(image.getdata())


def average_hash(image: Image.Image) -> int:
    sample = ImageOps.grayscale(image).resize((8, 8), Image.Resampling.LANCZOS)
    values = flattened_pixels(sample)
    average = sum(values) / len(values)
    result = 0
    for value in values:
        result = (result << 1) | int(value >= average)
    return result


def difference_hash(image: Image.Image) -> int:
    sample = ImageOps.grayscale(image).resize((9, 8), Image.Resampling.LANCZOS)
    pixels = flattened_pixels(sample)
    result = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            result = (result << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return result


def hash_similarity(left: int, right: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / 64.0)


def image_info_from_bytes(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image_format = normalized_format(opened.format)
            frames = int(getattr(opened, "n_frames", 1))
            oriented = ImageOps.exif_transpose(opened)
            oriented.load()
            if image_format not in SUPPORTED_FORMATS:
                raise SkillError(f"Unsupported decoded image format: {image_format or 'unknown'}")
            width, height = oriented.size
            return {
                "format": image_format,
                "width": width,
                "height": height,
                "area": width * height,
                "aspect_ratio": width / height,
                "frames": frames,
                "average_hash": average_hash(oriented),
                "difference_hash": difference_hash(oriented),
            }
    except (UnidentifiedImageError, OSError) as exc:
        raise SkillError("Content is not a valid supported image") from exc


def read_local_image(path: Path, maximum: int) -> tuple[bytes, dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SkillError(f"Image not found: {resolved}")
    if resolved.stat().st_size > maximum:
        raise SkillError(f"Image exceeds the {maximum // (1024 * 1024)} MiB limit")
    data = resolved.read_bytes()
    return data, image_info_from_bytes(data)


def read_limited(response: Any, maximum: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > maximum:
                raise SkillError(f"Remote image exceeds the {maximum // (1024 * 1024)} MiB limit")
        except ValueError:
            pass
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise SkillError(f"Remote image exceeds the {maximum // (1024 * 1024)} MiB limit")
    return data


def download_image(url: str, timeout: int, maximum: int) -> tuple[bytes, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SkillError("Only http/https candidate URLs are supported")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.5"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return read_limited(response, maximum), response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        raise SkillError(f"HTTP {exc.code} while downloading candidate") from exc
    except urllib.error.URLError as exc:
        raise SkillError(f"Download failed: {exc.reason}") from exc


def candidate_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return {"kind": "url", "value": value}
        return {"kind": "path", "value": value}
    if not isinstance(value, dict):
        return None
    url = value.get("image_url") or value.get("url") or value.get("candidate_url")
    path = value.get("path") or value.get("candidate_path")
    if url:
        record = {"kind": "url", "value": str(url)}
    elif path:
        record = {"kind": "path", "value": str(path)}
    else:
        return None
    if value.get("source_page"):
        record["source_page"] = str(value["source_page"])
    if value.get("title"):
        record["title"] = str(value["title"])
    return record


def load_candidate_manifest(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid candidates JSON: {path}") from exc
    metadata: dict[str, Any] = {}
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("candidates") or payload.get("candidate_urls") or []
        metadata = {
            "lens_results_url": payload.get("lens_results_url"),
            "matching_pages": payload.get("matching_pages") or [],
        }
        for item in payload.get("candidate_paths") or []:
            values.append({"path": item})
    else:
        raise SkillError("Candidates JSON must be a list or object")
    records = [record for value in values if (record := candidate_record(value))]
    return records, metadata


def collect_candidates(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = [{"kind": "url", "value": value} for value in args.candidate_url]
    records.extend({"kind": "path", "value": str(path)} for path in args.candidate_path)
    metadata: dict[str, Any] = {"matching_pages": []}
    if args.candidates_json:
        manifest_records, manifest_metadata = load_candidate_manifest(args.candidates_json)
        records.extend(manifest_records)
        metadata.update({key: value for key, value in manifest_metadata.items() if value})
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = (record["kind"], str(record["value"]))
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique[: args.max_results], metadata


def similarity(source: dict[str, Any], candidate: dict[str, Any]) -> float:
    average = hash_similarity(source["average_hash"], candidate["average_hash"])
    difference = hash_similarity(source["difference_hash"], candidate["difference_hash"])
    return round((0.35 * average) + (0.65 * difference), 4)


def analyze_candidate(
    index: int,
    record: dict[str, Any],
    source: dict[str, Any],
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "index": index,
        "kind": record["kind"],
        "value": str(record["value"]),
        "verified": False,
    }
    for key in ("source_page", "title"):
        if record.get(key):
            result[key] = record[key]
    try:
        maximum = args.max_download_mb * 1024 * 1024
        if record["kind"] == "url":
            data, content_type = download_image(str(record["value"]), args.timeout, maximum)
            result["content_type"] = content_type
        else:
            data, _ = read_local_image(Path(str(record["value"])), maximum)
        info = image_info_from_bytes(data)
        candidate_path = output_dir / f"candidate-{index:02d}{FORMAT_EXTENSIONS[info['format']]}"
        candidate_path.write_bytes(data)
        score = similarity(source, info)
        area_ratio = round(info["area"] / source["area"], 4)
        aspect_delta = round(
            abs(info["aspect_ratio"] - source["aspect_ratio"]) / source["aspect_ratio"], 4
        )
        reasons: list[str] = []
        if score < args.similarity_threshold:
            reasons.append("perceptual similarity is below threshold")
        if area_ratio < args.min_area_ratio:
            reasons.append("pixel area is not sufficiently larger")
        if aspect_delta > args.max_aspect_delta:
            reasons.append("aspect ratio differs too much")
        result.update(
            {
                "saved_path": str(candidate_path.resolve()),
                "format": info["format"],
                "width": info["width"],
                "height": info["height"],
                "frames": info["frames"],
                "area_ratio": area_ratio,
                "aspect_delta": aspect_delta,
                "similarity": score,
                "verified": not reasons,
                "rejection_reasons": reasons,
            }
        )
    except SkillError as exc:
        result["error"] = str(exc)
    return result


def best_verified(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    verified = [item for item in candidates if item.get("verified")]
    if not verified:
        return None
    return max(
        verified,
        key=lambda item: (item["width"] * item["height"], item["similarity"]),
    )


def next_backup_path(source: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
    counter = 2
    while result.exists():
        result = source.with_name(f"{source.stem}.backup-{stamp}-{counter}{source.suffix}")
        counter += 1
    return result


def reencode_candidate(candidate: Path, target: Path, source_format: str) -> None:
    if source_format == "GIF":
        raise SkillError("Animated GIF re-encoding is disabled; use a GIF candidate")
    with Image.open(candidate) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        options: dict[str, Any] = {}
        if source_format == "JPEG":
            if image.mode not in {"RGB", "L"}:
                if "A" in image.getbands():
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            options = {"quality": 95, "optimize": True}
        elif source_format == "WEBP":
            options = {"quality": 95, "method": 6}
        image.save(target, format=source_format, **options)


def replace_source(
    source_path: Path,
    source_info: dict[str, Any],
    selected: dict[str, Any],
    allow_reencode: bool,
) -> dict[str, Any]:
    source = source_path.expanduser().resolve()
    candidate = Path(selected["saved_path"])
    candidate_format = normalized_format(selected["format"])
    source_format = normalized_format(source_info["format"])
    if candidate_format != source_format and not allow_reencode:
        raise SkillError(
            f"Best candidate is {candidate_format}, source is {source_format}; use "
            "--allow-reencode only after accepting conversion risks"
        )
    if source_info["frames"] > 1 and candidate_format != source_format:
        raise SkillError("Animated sources cannot be replaced through format conversion")
    backup = next_backup_path(source)
    shutil.copy2(source, backup)
    temporary = source.with_name(f".{source.name}.replacement-{uuid.uuid4().hex}.tmp")
    try:
        if candidate_format == source_format:
            shutil.copyfile(candidate, temporary)
        else:
            reencode_candidate(candidate, temporary, source_format)
        staged = image_info_from_bytes(temporary.read_bytes())
        if staged["format"] != source_format:
            raise SkillError("Staged replacement format does not match the source format")
        os.replace(temporary, source)
        final = image_info_from_bytes(source.read_bytes())
        return {
            "status": "replaced",
            "target": str(source),
            "backup": str(backup),
            "candidate": str(candidate),
            "reencoded": candidate_format != source_format,
            "new_width": final["width"],
            "new_height": final["height"],
            "new_format": final["format"],
        }
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if not source.exists() or source.read_bytes() != backup.read_bytes():
            shutil.copy2(backup, source)
        raise


def public_info(info: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in info.items() if not key.endswith("_hash")}


def markdown_report(report: dict[str, Any]) -> str:
    source = report["source"]
    lines = [
        "# Google Lens higher-resolution image check",
        "",
        f"- Source: `{source['value']}`",
        f"- Dimensions: {source['width']} x {source['height']}",
        f"- Format: {source['format']}",
        f"- Lens results: {report.get('lens_results_url') or 'not recorded'}",
        "",
        "## Ranked candidates",
        "",
    ]
    if not report["candidates"]:
        lines.append("No candidates were supplied.")
    else:
        lines.extend(
            [
                "| Rank | Verified | Dimensions | Area ratio | Similarity | Candidate |",
                "|---:|:---:|---:|---:|---:|---|",
            ]
        )
        ranked = sorted(
            report["candidates"],
            key=lambda item: (
                bool(item.get("verified")),
                int(item.get("width", 0)) * int(item.get("height", 0)),
                float(item.get("similarity", 0)),
            ),
            reverse=True,
        )
        for rank, item in enumerate(ranked, 1):
            dimensions = (
                f"{item.get('width')} x {item.get('height')}" if item.get("width") else "error"
            )
            value = str(item.get("value", "")).replace("|", "%7C")
            lines.append(
                f"| {rank} | {'yes' if item.get('verified') else 'no'} | {dimensions} | "
                f"{item.get('area_ratio', '-')} | {item.get('similarity', '-')} | {value} |"
            )
    lines.extend(["", "## Matching pages", ""])
    pages = report.get("matching_pages") or []
    lines.extend(f"- {page}" for page in pages) if pages else lines.append("None recorded.")
    lines.extend(["", "## Selection", ""])
    selected = report.get("selected")
    if selected:
        lines.append(
            f"Selected candidate {selected['index']}: {selected['width']} x "
            f"{selected['height']} at similarity {selected['similarity']}."
        )
    else:
        lines.append("No candidate passed every automatic check.")
    replacement = report.get("replacement")
    if replacement:
        lines.extend(
            [
                "",
                "## Replacement",
                "",
                f"- Status: {replacement['status']}",
                f"- Target: `{replacement['target']}`",
                f"- Backup: `{replacement['backup']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        maximum = args.max_download_mb * 1024 * 1024
        args.output_dir = args.output_dir.expanduser().resolve()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        if args.file is not None:
            args.file = args.file.expanduser().resolve()
            _, source_info = read_local_image(args.file, maximum)
            source_kind, source_value = "file", str(args.file)
        else:
            source_data, _ = download_image(args.url, args.timeout, maximum)
            source_info = image_info_from_bytes(source_data)
            source_kind, source_value = "url", args.url

        records, manifest_metadata = collect_candidates(args)
        if not records:
            lens_url = google_lens_url(args.url) if args.url else "https://lens.google.com/"
            raise SkillError(
                "No Lens candidates supplied. Open the Lens URL, collect direct candidate image "
                f"URLs or downloaded files, then rerun this script. Lens URL: {lens_url}"
            )
        candidates = [
            analyze_candidate(index, record, source_info, args.output_dir, args)
            for index, record in enumerate(records, 1)
        ]
        selected = best_verified(candidates)
        replacement = None
        if args.replace:
            if selected is None:
                raise SkillError("No candidate passed every check; source was not replaced")
            replacement = replace_source(args.file, source_info, selected, args.allow_reencode)

        lens_results_url = args.lens_results_url or manifest_metadata.get("lens_results_url")
        matching_pages = list(args.matching_page)
        for page in manifest_metadata.get("matching_pages") or []:
            page_value = page.get("url") if isinstance(page, dict) else page
            if page_value:
                matching_pages.append(str(page_value))
        for candidate in candidates:
            if candidate.get("verified") and candidate.get("source_page"):
                matching_pages.append(str(candidate["source_page"]))
        matching_pages = list(dict.fromkeys(matching_pages))

        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "engine": "google-lens",
            "lens_results_url": lens_results_url,
            "source": {"kind": source_kind, "value": source_value, **public_info(source_info)},
            "thresholds": {
                "similarity": args.similarity_threshold,
                "minimum_area_ratio": args.min_area_ratio,
                "maximum_aspect_delta": args.max_aspect_delta,
            },
            "matching_pages": matching_pages,
            "candidates": candidates,
            "selected": selected,
            "replacement": replacement,
        }
        report_json = args.output_dir / "report.json"
        report_md = args.output_dir / "report.md"
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_md.write_text(markdown_report(report), encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "success",
                    "engine": "google-lens",
                    "report_json": str(report_json),
                    "report_markdown": str(report_md),
                    "candidates_checked": len(candidates),
                    "verified_candidates": sum(bool(item.get("verified")) for item in candidates),
                    "selected": selected.get("value") if selected else None,
                    "replacement": replacement,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except SkillError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover
        print(
            json.dumps(
                {"status": "error", "message": f"Unexpected failure: {type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
