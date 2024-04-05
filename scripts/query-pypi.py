#!/usr/bin/env python3
import argparse
import functools
import os
import pathlib
import pprint
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
import shutil

import httpx
import msgpack
import trio
import yaml
from lxml import html
from tqdm import tqdm

HEADERS = {"user-agent": "https://github.com/salt-extensions/salt-extensions-metadata"}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL_CACHE_PATH = pathlib.Path(
    os.environ.get("LOCAL_CACHE_PATH") or REPO_ROOT.joinpath(".cache")
)
if not LOCAL_CACHE_PATH.is_dir():
    LOCAL_CACHE_PATH.mkdir(0o755)
PACKAGE_INFO_CACHE = LOCAL_CACHE_PATH / "packages-info"
if not PACKAGE_INFO_CACHE.is_dir():
    PACKAGE_INFO_CACHE.mkdir(0o755)
STATE_DIR = REPO_ROOT / ".state"
if not STATE_DIR.is_dir():
    STATE_DIR.mkdir(0o755)
DATA_DIR = REPO_ROOT / "data"
METADATA_DIR = REPO_ROOT / "metadata"

PACKAGE_NAME_PREFIXES = ("salt-ext-", "saltext-", "saltext.")

with open(DATA_DIR / "include-pypi-packages.yaml", "r") as fp:
    KNOWN_SALT_EXTENSIONS = set(yaml.safe_load(fp))

with open(DATA_DIR / "exclude-pypi-packages.yaml", "r") as fp:
    KNOWN_NOT_SALT_EXTENSIONS = set(yaml.safe_load(fp))

print(f"Local Cache Path: {LOCAL_CACHE_PATH}", file=sys.stderr, flush=True)

if sys.version_info < (3, 12):
    print("This script is meant to only run on Py3.12+", file=sys.stderr, flush=True)


def set_progress_description(progress, message):
    progress.set_description(f"{message: <60}")


def get_sha256_command():
    sha256 = shutil.which('sha256sum')
    if sha256:
        return [sha256]
    sha256 = shutil.which('shasum')
    if sha256:
        return [sha256, "-a", "256"]
    raise Exception("SHA256 command not found")


@contextmanager
def get_index_info(progress, options):
    local_pypi_index_info = LOCAL_CACHE_PATH / "pypi-index.msgpack"
    set_progress_description(progress, "Loading cache")
    if local_pypi_index_info.exists():
        index_info = msgpack.unpackb(local_pypi_index_info.read_bytes())
        ret = subprocess.run(
            get_sha256_command() + [__file__],
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        if ret.returncode != 0:
            progress.write(
                f"Failed to get the sha256sum of {__file__}. Invalidating the packages ETAG cache."
            )
            for data in index_info["packages"].values():
                data.pop("etag", None)
        else:
            sha256sum = ret.stdout.split()[0].strip()
            stored_sha256sum = index_info.get("sha256sum")
            if sha256sum != stored_sha256sum:
                progress.write(
                    f"This script's sha256sum({sha256sum}) does not match {stored_sha256sum}. "
                    "Invalidating the packages ETAG cache."
                )
                for data in index_info["packages"].values():
                    data.pop("etag", None)
    else:
        index_info = {"packages": {}}

    progress.update()
    try:
        yield index_info
    finally:
        set_progress_description(progress, "Saving cache")
        ret = subprocess.run(
            get_sha256_command() + [__file__],
            check=False,
            shell=False,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        if ret.returncode != 0:
            progress.write(
                f"Failed to get the sha256sum of {__file__}. Invalidating the pypi cache."
            )
        else:
            sha256sum = ret.stdout.split()[0].strip()
            index_info["sha256sum"] = sha256sum
        local_pypi_index_info.write_bytes(msgpack.packb(index_info))
        progress.update()


async def download_pypi_simple_index(session, index_info, limiter, progress, options):
    try:
        async with limiter:
            headers = {}
            etag = index_info.get("etag")
            if etag:
                headers["If-None-Match"] = etag

            set_progress_description(progress, "Querying packages from PyPi")
            with tempfile.NamedTemporaryFile() as download_file:
                async with session.stream(
                    "GET", "https://pypi.org/simple/", headers=headers
                ) as response:

                    if response.status_code == 304:
                        # There are no new packages
                        progress.write("There are no new packages")
                        return

                    if response.status_code != 200:
                        progress.write(
                            "Failed to download the PyPi index. Status Code: {request.status_code}"
                        )
                        return
                    total = int(response.headers["Content-Length"])

                    with tqdm(
                        total=total,
                        unit_scale=True,
                        unit_divisor=1024,
                        unit="B",
                        disable=options.no_progress,
                    ) as dprogress:
                        dprogress.set_description("Downloading PyPi simple index")
                        num_bytes_downloaded = response.num_bytes_downloaded
                        async for chunk in response.aiter_bytes():
                            download_file.write(chunk)
                            dprogress.update(
                                response.num_bytes_downloaded - num_bytes_downloaded
                            )
                            num_bytes_downloaded = response.num_bytes_downloaded
                        dprogress.set_description(
                            "Downloading PyPi simple index complete."
                        )

                index_info["etag"] = response.headers.get("etag")
                STATE_DIR.joinpath("pypi-index-etag").write_text(index_info["etag"])

                set_progress_description(
                    progress, "Querying packages from PyPi completed"
                )

                set_progress_description(progress, "Parsing HTML for packages")
                tree = html.fromstring(pathlib.Path(download_file.name).read_text())
                old_packages = set(index_info["packages"])
                new_packages = set()
                package_list = index_info["packages"]
                for package in tree.xpath("//a/text()"):
                    if package in old_packages:
                        old_packages.remove(package)
                    if package not in package_list:
                        new_packages.add(package)
                        package_list[package] = {}
                if old_packages:
                    progress.write(
                        f"Removing the following old packages from "
                        f"cache: {', '.join(old_packages)}"
                    )
                    for package in old_packages:
                        package_list.pop(package)
                        package_info_cache = PACKAGE_INFO_CACHE / f"{package}.msgpack"
                        if package_info_cache.exists():
                            package_info_cache.unlink()
                progress.write(
                    f"The PyPi index server had {len(package_list)} packages. "
                    f"{len(new_packages)} were new. {len(old_packages)} were old and were deleted"
                )
                if len(new_packages) <= 100:
                    progress.write("New packages:")
                    for package in new_packages:
                        progress.write(f" * {package}")
                set_progress_description(progress, "Parsing HTML for packages complete")
    finally:
        progress.update()


async def collect_packages_information(session, index_info, limiter, progress, options):
    try:
        async with trio.open_nursery() as nursery:
            for package in index_info["packages"]:
                if (options.fast and (package in KNOWN_NOT_SALT_EXTENSIONS or not (package in KNOWN_SALT_EXTENSIONS or package.startswith(PACKAGE_NAME_PREFIXES)))):
                    continue
                async with limiter:
                    nursery.start_soon(
                        download_package_info,
                        session,
                        package,
                        index_info["packages"][package],
                        limiter,
                        progress,
                        options,
                    )
    finally:
        # Store the known extensions hash into state to trigger a cache hit/miss/update
        # on the Github Actions CI pipeline
        extensions = {}
        for path in PACKAGE_INFO_CACHE.glob("*.msgpack"):
            extension_data = msgpack.unpackb(
                PACKAGE_INFO_CACHE.joinpath(f"{path.stem}.msgpack").read_bytes()
            )
            extensions[path.stem] = extension_data
        try:
            extensions_hash = functools.reduce(
                lambda x, y: x ^ y,
                [
                    hash((key, repr(value)))
                    for (key, value) in sorted(extensions.items())
                ],
            )
            STATE_DIR.joinpath("known-extensions-hash").write_text(f"{extensions_hash}")
        except TypeError as exc:
            progress.write(f"Failed to generate the known extensions hash: {exc}")


async def download_package_info(session, package, package_info, limiter, progress, options):
    try:
        package_info_cache = PACKAGE_INFO_CACHE / f"{package}.msgpack"
        if package_info.get("not-found"):
            message = f"Skipping {package} know to throw 404"
            if not options.no_progress:
                set_progress_description(progress, message)
            if package_info_cache.exists():
                package_info_cache.unlink()
            return
        url = f"https://pypi.org/pypi/{package}/json"
        headers = {}
        etag = package_info.get("etag")
        if etag:
            headers["If-None-Match"] = etag

        set_progress_description(progress, f"Querying info for {package}")
        try:
            req = await session.get(url, headers=headers, timeout=15)
        except (httpx.TimeoutException, trio.ClosedResourceError) as exc:
            progress.write(f"Failed to query info for {package}: {exc}")
            return
        package_info["etag"] = req.headers.get("etag")
        if req.status_code == 304:
            set_progress_description(progress, f"No changes for {package}")
            # The package information has not changed:
            return
        if req.status_code != 200:
            if req.status_code == 404:
                package_info["not-found"] = True
            progress.write(
                f"Failed to query info for {package}. Status code: {req.status_code}"
            )
            if package_info_cache.exists():
                package_info_cache.unlink()
            return

        data = req.json()
        if not data:
            progress.write(
                "Failed to get JSON data back. Got:\n>>>>>>\n{req.text}\n<<<<<<"
            )
            if package_info_cache.exists():
                package_info_cache.unlink()
            return
        try:
            salt_extension = False
            if package in KNOWN_SALT_EXTENSIONS:
                salt_extension = True
                progress.write(f"{package} is a known salt-extension")
            elif package not in KNOWN_NOT_SALT_EXTENSIONS:
                if package.startswith(PACKAGE_NAME_PREFIXES):
                    salt_extension = True
                    progress.write(
                        f"{package} was detected as a salt-extension from it's name"
                    )
                elif (
                    data["info"]["keywords"]
                    and "salt-extension" in data["info"]["keywords"]
                ):
                    salt_extension = True
                    progress.write(
                        f"{package} was detected as a salt-extension because of it's keywords"
                    )
            if salt_extension:
                package_info_cache.write_bytes(msgpack.packb(data))
            else:
                if package_info_cache.exists():
                    package_info_cache.unlink()
        except Exception:
            progress.write(traceback.format_exc())
            progress.write(f"Data:\n{pprint.pformat(data)}")
    finally:
        progress.update()


async def main(options):
    timeout = 240 * 60  # move on after 4 hours
    progress = tqdm(
        total=sys.maxsize,
        unit="pkg",
        unit_scale=True,
        desc=f"{' ' * 60} :",
        disable=options.no_progress,
    )
    with progress:
        with get_index_info(progress, options) as index_info:
            concurrency = 1500
            limiter = trio.CapacityLimiter(concurrency)
            with trio.move_on_after(timeout) as cancel_scope:
                limits = httpx.Limits(
                    max_keepalive_connections=5, max_connections=concurrency
                )
                async with httpx.AsyncClient(
                    limits=limits, http2=True, headers=HEADERS
                ) as session:
                    await download_pypi_simple_index(
                        session, index_info, limiter, progress, options
                    )
                    if not options.no_progress:
                        # We can't reset tqdm if it's disabled
                        progress.reset(total=len(index_info["packages"]))
                    await collect_packages_information(
                        session, index_info, limiter, progress, options
                    )
        if cancel_scope.cancelled_caught:
            progress.write(f"The script timed out after {timeout} minutes")
            return 1
        progress.write("Detected Salt Extensions:")
        for path in sorted(PACKAGE_INFO_CACHE.glob("*.msgpack")):
            progress.write(f" * {path.stem}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query PyPI for Salt Extensions")
    parser.add_argument(
        "--fast", action="store_true", default=False, help="Fast mode (only match package names)"
    )
    parser.add_argument(
        "--no-progress", action="store_true", default="CI" in os.environ, help="Disable progress bar"
    )
    options = parser.parse_args()
    sys.exit(trio.run(main, options))
