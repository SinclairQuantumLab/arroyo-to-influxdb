#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv_python="${project_dir}/.venv/bin/python"

if [[ ! -x "${venv_python}" ]]; then
  echo "Prepared project interpreter not found: ${venv_python}. Run uv sync first." >&2
  exit 1
fi

cd "${project_dir}"
exec "${venv_python}" ./main.py --settings ./settings.toml
