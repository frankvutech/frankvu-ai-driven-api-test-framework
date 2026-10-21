#!/usr/bin/env bash
#
# Install the pinned Allure 2 command-line tool into .tools/, verifying its checksum.
#
# Allure 2's CLI is a Java application (Java 8+). It is downloaded from Maven Central rather than a
# package manager so local and CI runs use the identical, checksummed build:
#
#   io.qameta.allure:allure-commandline:2.39.0
#
# Prints the path of the `allure` executable on success.

set -euo pipefail

ALLURE_VERSION="2.39.0"
ALLURE_SHA256="74383f66069cc0f6ec2d0fdad2f1d761f13184f6cd289334b1dcfc4237735665"
ALLURE_URL="https://repo.maven.apache.org/maven2/io/qameta/allure/allure-commandline/${ALLURE_VERSION}/allure-commandline-${ALLURE_VERSION}.tgz"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${ROOT}/.tools"
ARCHIVE="${TOOLS}/allure-${ALLURE_VERSION}.tgz"
HOME_DIR="${TOOLS}/allure-${ALLURE_VERSION}"
BIN="${HOME_DIR}/bin/allure"

if [[ -x "${BIN}" ]]; then
  echo "${BIN}"
  exit 0
fi

command -v java >/dev/null 2>&1 || { echo "error: Allure ${ALLURE_VERSION} needs Java 8+ on PATH" >&2; exit 1; }

mkdir -p "${TOOLS}"
if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Downloading Allure ${ALLURE_VERSION}..." >&2
  curl --fail --silent --show-error --location "${ALLURE_URL}" --output "${ARCHIVE}.partial"
  mv "${ARCHIVE}.partial" "${ARCHIVE}"
fi

if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "${ARCHIVE}" | cut -d' ' -f1)"
else
  actual="$(shasum -a 256 "${ARCHIVE}" | cut -d' ' -f1)"
fi
if [[ "${actual}" != "${ALLURE_SHA256}" ]]; then
  rm -f "${ARCHIVE}"
  echo "error: checksum mismatch for ${ARCHIVE} (got ${actual})" >&2
  exit 1
fi

tar -xzf "${ARCHIVE}" -C "${TOOLS}"
chmod +x "${BIN}"
echo "${BIN}"
