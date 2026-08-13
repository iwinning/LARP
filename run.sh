#!/usr/bin/env bash
# Startar Flask-webbservern med gunicorn.
# Single worker bevarar in-memory job-state; threads hanterar parallella anrop.
# Timeout 300 s ger långa scraping-jobb tid att köra klart.
exec gunicorn \
  --bind 0.0.0.0:5000 \
  --workers 1 \
  --threads 4 \
  --timeout 300 \
  --reuse-port \
  app:app
