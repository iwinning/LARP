#!/bin/bash
set -e

# Install Python dependencies
pip install -r requirements.txt --quiet

# Install Playwright Chromium browser if not already present
python -m playwright install chromium
