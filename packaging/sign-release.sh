#!/usr/bin/env bash
# Produce a signed manifest of the shipped files.
#
#   ./packaging/sign-release.sh
#
# Writes SHA256SUMS and a detached OpenPGP signature SHA256SUMS.asc.
# Anyone can then verify authorship and integrity with:
#
#   gpg --verify SHA256SUMS.asc SHA256SUMS && sha256sum -c SHA256SUMS
#
# Author: lonestarparrot <lonestarparrot@gmail.com>
set -euo pipefail

KEY="${SIGNING_KEY:-lonestarparrot@gmail.com}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Hash every tracked file except the signature artefacts themselves.
git ls-files -z \
  | grep -zv '^SHA256SUMS' \
  | xargs -0 sha256sum > SHA256SUMS

rm -f SHA256SUMS.asc
gpg --batch --yes --armor --detach-sign --local-user "$KEY" -o SHA256SUMS.asc SHA256SUMS

echo "Signed $(wc -l < SHA256SUMS) files as:"
gpg --verify SHA256SUMS.asc SHA256SUMS 2>&1 | sed 's/^/  /'
