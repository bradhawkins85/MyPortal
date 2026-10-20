# shellcheck shell=bash
# Locate, or install, the Python interpreter MyPortal runs on.
#
# Sourced by scripts/upgrade.sh (bare-metal releases) and
# scripts/install_environment.sh. Hosts that only have an older system Python
# (Debian 12 ships 3.11, Ubuntu 22.04 ships 3.10) are moved to the runtime
# version automatically:
#
#   1. python${MYPORTAL_PYTHON_VERSION} already on PATH.
#   2. An interpreter this helper installed earlier under <managed root>.
#   3. The distribution package (apt), when the host offers one.
#   4. A standalone CPython build installed with uv under <managed root>.
#
# ensure_runtime_python prints the interpreter path on stdout; progress goes
# to stderr. It returns non-zero, leaving the running release untouched, when
# no interpreter can be found or installed.

MYPORTAL_PYTHON_VERSION="${MYPORTAL_PYTHON_VERSION:-3.12}"
# Pinned so every host bootstraps the interpreter the same way.
MYPORTAL_UV_VERSION="${MYPORTAL_UV_VERSION:-0.13.4}"

python_runtime_usable() {
  local candidate="$1"
  [[ -n "$candidate" && -x "$candidate" ]] || return 1
  # venv and ensurepip are needed to build the release virtual environment;
  # Debian splits ensurepip into a separate pythonX.Y-venv package.
  "$candidate" - "$MYPORTAL_PYTHON_VERSION" >/dev/null 2>&1 <<'PY'
import sys
import ensurepip  # noqa: F401
import venv  # noqa: F401

wanted = tuple(int(part) for part in sys.argv[1].split("."))
sys.exit(0 if sys.version_info[: len(wanted)] == wanted else 1)
PY
}

find_runtime_python() {
  local root="$1" candidate
  for candidate in \
    "$(command -v "python${MYPORTAL_PYTHON_VERSION}" 2>/dev/null || true)" \
    "${root}/bin/python${MYPORTAL_PYTHON_VERSION}"; do
    if python_runtime_usable "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_runtime_python_package() {
  local package="python${MYPORTAL_PYTHON_VERSION}"
  [[ "${EUID:-$(id -u)}" == "0" ]] && command -v apt-get >/dev/null 2>&1 || return 1
  DEBIAN_FRONTEND=noninteractive apt-get update -qq >&2 || return 1
  [[ -n "$(apt-cache policy "$package" 2>/dev/null | awk '/Candidate:/ {print $2}' | grep -v '(none)')" ]] || return 1
  echo "Installing ${package} from the distribution packages…" >&2
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$package" "${package}-venv" >&2
}

install_runtime_python_standalone() {
  local root="$1" bootstrap="${1}/uv-bootstrap" interpreter
  echo "Installing standalone Python ${MYPORTAL_PYTHON_VERSION} under ${root}…" >&2
  mkdir -p "$root"
  # uv itself comes from PyPI through the current system Python; only the
  # downloaded interpreter is used to run MyPortal.
  if [[ ! -x "${bootstrap}/bin/uv" ]]; then
    rm -rf "$bootstrap"
    python3 -m venv "$bootstrap" >&2 || return 1
    "${bootstrap}/bin/python" -m pip install --quiet --disable-pip-version-check \
      "uv==${MYPORTAL_UV_VERSION}" >&2 || return 1
  fi
  UV_PYTHON_INSTALL_DIR="${root}/interpreters" \
    "${bootstrap}/bin/uv" python install --no-bin "$MYPORTAL_PYTHON_VERSION" >&2 || return 1
  interpreter=$(UV_PYTHON_INSTALL_DIR="${root}/interpreters" \
    "${bootstrap}/bin/uv" python find --python-preference only-managed "$MYPORTAL_PYTHON_VERSION") || return 1
  mkdir -p "${root}/bin"
  ln -sfn "$interpreter" "${root}/bin/python${MYPORTAL_PYTHON_VERSION}"
  # The service account runs releases whose virtual environments point here.
  chmod -R u+rwX,go+rX "$root"
}

ensure_runtime_python() {
  local root="$1" found
  if found=$(find_runtime_python "$root"); then
    printf '%s\n' "$found"
    return 0
  fi
  echo "Python ${MYPORTAL_PYTHON_VERSION} is not installed; MyPortal requires it." >&2
  if install_runtime_python_package && found=$(find_runtime_python "$root"); then
    printf '%s\n' "$found"
    return 0
  fi
  if install_runtime_python_standalone "$root" && found=$(find_runtime_python "$root"); then
    printf '%s\n' "$found"
    return 0
  fi
  echo "Error: could not install Python ${MYPORTAL_PYTHON_VERSION}. Install python${MYPORTAL_PYTHON_VERSION} and python${MYPORTAL_PYTHON_VERSION}-venv, then rerun." >&2
  return 1
}
