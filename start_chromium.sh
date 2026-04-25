#!/bin/bash
# Launch two Chromium instances with separate profiles and remote debugging ports.
# Account 1 uses port 9222, Account 2 uses port 9223.

chromium-browser \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/pi/.config/chromium-uscis1 \
  --no-first-run \
  "$@" &
echo "Chromium (Account 1) started on port 9222, profile: chromium-uscis1"

sleep 2

chromium-browser \
  --remote-debugging-port=9223 \
  --user-data-dir=/home/pi/.config/chromium-uscis2 \
  --no-first-run \
  "$@" &
echo "Chromium (Account 2) started on port 9223, profile: chromium-uscis2"
