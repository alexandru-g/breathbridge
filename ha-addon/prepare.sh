#!/bin/bash
# Copies the source code into the add-on directory for building.
# Run this before adding the add-on repo to HA.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

rm -rf "$SCRIPT_DIR/breathbridge"
cp -r "$REPO_DIR/src/breathbridge" "$SCRIPT_DIR/breathbridge"

echo "Add-on prepared. Source copied to ha-addon/breathbridge/"
