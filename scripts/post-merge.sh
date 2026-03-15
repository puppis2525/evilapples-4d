#!/bin/bash
set -e

echo "[post-merge] Verifying environment..."
echo "[post-merge] Python: $(python --version 2>&1)"
echo "[post-merge] Done."
