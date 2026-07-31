#!/usr/bin/env bash
set -euo pipefail
set -x

coverage run -m pytest tests/
coverage report
coverage html --title "${@-coverage}"
