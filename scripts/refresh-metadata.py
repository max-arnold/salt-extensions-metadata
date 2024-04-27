#!/usr/bin/env python3
import argparse
import os
import pathlib
import re

import msgpack
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL_CACHE_PATH = pathlib.Path(os.environ.get("LOCAL_CACHE_PATH") or REPO_ROOT.joinpath(".cache"))
PACKAGE_INFO_CACHE = LOCAL_CACHE_PATH / "pypi-packages-info"
METADATA_DIR = REPO_ROOT / "metadata"


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def iterate_pypi_cache():
    for path in sorted(PACKAGE_INFO_CACHE.glob("*.msgpack")):
        extension_data = msgpack.unpackb(path.read_bytes())
        name = extension_data["info"]["name"]
        name_normalized = normalize(name)
        # description = extension_data["info"]["description"].rstrip()
        # if "markdown" in extension_data["info"]["description_content_type"]:
        #     description = m2r.convert(description)
        summary = extension_data["info"]["summary"].strip()
        releases = [
            {
                "version": ver,
                "dt": files[-1]["upload_time"],
            }
            for ver, files in map(
                lambda ver: (ver[0], [f for f in ver[1] if not f.get("yanked")]),
                extension_data.get("releases", {}).items(),
            )
            if len(files) > 0
        ]
        if len(releases) == 0:
            continue
        # "requires_dist": [ license
        extension_data["info"].pop("license", None)
        extension_data["info"].pop("description", None)
        yield {
            "name": name,
            "name_normalized": name_normalized,
            "summary": summary,
            # "description": description,
            "releases": len(releases),
            "release_first": releases[0],
            "release_latest": releases[-1],
        } | {
            key: extension_data["info"].get(key)
            for key in [
                "author" "author_email",
                "bugtrack_url",
                "docs_url",
                "download_url",
                "home_page",
                "maintainer",
                "maintainer_email",
                "package_url",
                "project_url",
                "project_urls",
                "release_url",
            ]
            if extension_data["info"].get(key)
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metadata")
    # parser.add_argument(
    #     "--no-progress", action="store_true", default="CI" in os.environ, help="Disable progress bar"
    # )
    options = parser.parse_args()
    for ext in iterate_pypi_cache():
        print("---", "\n" + yaml.dump(ext))
