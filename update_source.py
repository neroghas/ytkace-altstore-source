#!/usr/bin/env python3
"""Generate a minimal AltStore/SideStore source from official YTKACE releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


UPSTREAM_REPOSITORY = "itzzace/ytkace"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPOSITORY}"
DOWNLOAD_PREFIX = f"{UPSTREAM_URL}/releases/download/"
API_URL = f"https://api.github.com/repos/{UPSTREAM_REPOSITORY}/releases"
SOURCE_PATH = Path(__file__).with_name("source.json")
USER_AGENT = "ytkace-altstore-source-updater"
SOURCE_IDENTIFIER = "io.github.neroghas.ytkace-altstore-source"
SOURCE_URL = (
    "https://raw.githubusercontent.com/neroghas/"
    "ytkace-altstore-source/main/source.json"
)


def request(url: str, *, authenticated: bool = False, method: str = "GET"):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if authenticated and token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers, method=method), timeout=60
    )


def fetch_releases() -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        with request(f"{API_URL}?per_page=100&page={page}", authenticated=True) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("GitHub Releases API returned an unexpected response")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def asset_ios_hint(name: str, body: str) -> int:
    for line in body.splitlines():
        if name in line:
            match = re.search(r"iOS\s*(\d+)", line, re.IGNORECASE)
            if match:
                return int(match.group(1))
    match = re.search(r"iOS[_ .-]?(\d+)", name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def youtube_version(name: str) -> tuple[int, ...]:
    match = re.search(r"YouTube[_ .-]?(\d+(?:\.\d+)+)", name, re.IGNORECASE)
    return version_tuple(match.group(1)) if match else ()


def choose_ipa(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = [
        asset
        for asset in release.get("assets", [])
        if str(asset.get("name", "")).lower().endswith(".ipa")
    ]
    if not assets:
        return None

    body = str(release.get("body") or "")
    # Prefer the newest/iOS 17+ build when one release provides multiple IPAs.
    return max(
        assets,
        key=lambda asset: (
            asset_ios_hint(str(asset["name"]), body),
            youtube_version(str(asset["name"])),
            str(asset["name"]),
        ),
    )


def download_and_inspect(url: str) -> dict[str, str]:
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="ytkace-source-") as temp_dir:
        ipa_path = Path(temp_dir) / "release.ipa"
        with request(url) as response, ipa_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)

        with zipfile.ZipFile(ipa_path) as archive:
            candidates = [
                name
                for name in archive.namelist()
                if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)
            ]
            if len(candidates) != 1:
                raise ValueError(f"Expected one top-level app Info.plist, found {len(candidates)}")
            info = plistlib.loads(archive.read(candidates[0]))

    required = {
        "bundleIdentifier": info.get("CFBundleIdentifier"),
        "version": info.get("CFBundleShortVersionString"),
        "buildVersion": info.get("CFBundleVersion"),
        "minOSVersion": info.get("MinimumOSVersion"),
        "sha256": digest.hexdigest(),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"IPA is missing required metadata: {', '.join(missing)}")
    return {key: str(value) for key, value in required.items()}


def clean_release_notes(body: str) -> str:
    lines = body.strip().splitlines()
    if lines and re.fullmatch(r"by\s+@\S+", lines[0].strip(), re.IGNORECASE):
        lines = lines[1:]
    return "\n".join(lines).strip() or "Official YTKACE release."


def current_versions() -> dict[str, dict[str, Any]]:
    if not SOURCE_PATH.exists():
        return {}
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    apps = source.get("apps", [])
    if not apps:
        return {}
    return {
        version["downloadURL"]: version
        for version in apps[0].get("versions", [])
        if version.get("downloadURL")
    }


def build_source(releases: list[dict[str, Any]]) -> dict[str, Any]:
    cached = current_versions()
    versions: list[dict[str, Any]] = []
    bundle_identifier: str | None = None

    for release in releases:
        if release.get("draft"):
            continue
        asset = choose_ipa(release)
        if not asset:
            continue

        url = str(asset["browser_download_url"])
        cached_version = cached.get(url)
        if cached_version:
            metadata = {
                key: cached_version[key] for key in ("version", "minOSVersion")
            }
            cached_bundle = cached_version.get("_bundleIdentifier")
            if cached_bundle:
                bundle_identifier = str(cached_bundle)
        else:
            metadata = download_and_inspect(url)
            inspected_bundle = metadata.pop("bundleIdentifier")
            published_digest = str(asset.get("digest") or "")
            if published_digest.startswith("sha256:"):
                if metadata["sha256"] != published_digest.removeprefix("sha256:"):
                    raise ValueError("Downloaded IPA does not match its published SHA-256")
            if bundle_identifier and bundle_identifier != inspected_bundle:
                raise ValueError("Official releases contain different bundle identifiers")
            bundle_identifier = inspected_bundle

        tag = str(release.get("tag_name") or "").removeprefix("v")
        versions.append(
            {
                "version": str(metadata["version"]),
                "date": str(release["published_at"]),
                "localizedDescription": (
                    f"YTKACE {tag}\n\n"
                    f"{clean_release_notes(str(release.get('body') or ''))}"
                ),
                "downloadURL": url,
                "size": int(asset["size"]),
                "minOSVersion": str(metadata["minOSVersion"]),
            }
        )

    if not versions:
        raise ValueError("No official IPA assets were found")

    # Existing sources omit the private cache field, so infer the known upstream bundle ID.
    bundle_identifier = bundle_identifier or "com.google.ios.youtube"
    return {
        "name": "YTKACE Official Releases",
        "identifier": SOURCE_IDENTIFIER,
        "sourceURL": SOURCE_URL,
        "apps": [
            {
                "name": "YTKACE",
                "bundleIdentifier": bundle_identifier,
                "developerName": "itzzace",
                "subtitle": "Open-source YouTube enhancements for iOS.",
                "localizedDescription": (
                    "Official YTKACE release IPAs for standalone installation with "
                    "SideStore and older-version access from LiveContainer."
                ),
                "iconURL": "https://github.com/itzzace.png",
                "tintColor": "#FF0000",
                "versions": versions,
            }
        ],
        "news": [],
    }


def validate_source(source: dict[str, Any], *, check_links: bool = False) -> None:
    unexpected_source_keys = set(source) - {"name", "identifier", "sourceURL", "apps", "news"}
    if unexpected_source_keys:
        raise ValueError(f"Unsupported SideStore source fields: {sorted(unexpected_source_keys)}")
    if not isinstance(source.get("name"), str) or not source["name"]:
        raise ValueError("Source name is required")
    for key in ("identifier", "sourceURL"):
        if not isinstance(source.get(key), str) or not source[key]:
            raise ValueError(f"Source field {key} is required")
    if check_links:
        with request(source["sourceURL"], method="HEAD") as response:
            if response.status >= 400:
                raise ValueError(f"Unavailable source URL: {source['sourceURL']}")
    apps = source.get("apps")
    if not isinstance(apps, list) or len(apps) != 1:
        raise ValueError("Source must contain exactly one app")

    app = apps[0]
    allowed_app_keys = {
        "name",
        "bundleIdentifier",
        "developerName",
        "subtitle",
        "localizedDescription",
        "iconURL",
        "tintColor",
        "versions",
    }
    unexpected_app_keys = set(app) - allowed_app_keys
    if unexpected_app_keys:
        raise ValueError(f"Unsupported SideStore app fields: {sorted(unexpected_app_keys)}")
    for key in ("name", "bundleIdentifier", "developerName", "localizedDescription", "iconURL"):
        if not isinstance(app.get(key), str) or not app[key]:
            raise ValueError(f"App field {key} is required")

    versions = app.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("At least one app version is required")

    urls: set[str] = set()
    for index, version in enumerate(versions):
        allowed_version_keys = {
            "version",
            "date",
            "localizedDescription",
            "downloadURL",
            "size",
            "minOSVersion",
        }
        unexpected_version_keys = set(version) - allowed_version_keys
        if unexpected_version_keys:
            raise ValueError(
                f"Unsupported SideStore version fields: {sorted(unexpected_version_keys)}"
            )
        for key in (
            "version",
            "date",
            "localizedDescription",
            "downloadURL",
            "minOSVersion",
        ):
            if not isinstance(version.get(key), str) or not version[key]:
                raise ValueError(f"Version {index} field {key} is required")
        if version["downloadURL"] in urls:
            raise ValueError(f"Duplicate IPA URL: {version['downloadURL']}")
        urls.add(version["downloadURL"])
        datetime.fromisoformat(version["date"].replace("Z", "+00:00"))
        if not version["downloadURL"].startswith(DOWNLOAD_PREFIX):
            raise ValueError("Every IPA URL must point to an official itzzace/ytkace release")
        if not version["downloadURL"].lower().endswith(".ipa"):
            raise ValueError("Every download URL must point to an IPA")
        if not isinstance(version.get("size"), int) or version["size"] <= 0:
            raise ValueError("Every version must have a positive byte size")
        if check_links:
            with request(version["downloadURL"], method="HEAD") as response:
                if response.status >= 400:
                    raise ValueError(f"Unavailable IPA URL: {version['downloadURL']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate source.json only")
    parser.add_argument("--check-links", action="store_true", help="also verify IPA URLs")
    args = parser.parse_args()

    if args.check:
        source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    else:
        source = build_source(fetch_releases())
        SOURCE_PATH.write_text(
            json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    validate_source(source, check_links=args.check_links)
    print(f"Validated {len(source['apps'][0]['versions'])} official YTKACE release(s).")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise SystemExit(f"error: {error}") from error
