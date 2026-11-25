#!/usr/bin/env bash
# Analyze one binary and write its CycloneDX SBOM to /out.
#
#   docker-entrypoint.sh [options]                 analyze the single file found in /input
#   docker-entrypoint.sh [options] <binary path>   analyze that binary
#   docker-entrypoint.sh <command ...>             run the command in /app instead
#
# Options:
#   --display-path <path>   path written to the SBOM instead of the analyzed file's path
#   --no-ldd                do not add the shared libraries ldd reports in the container
#
# If a binary DB (binaryDB/db_index.pkl) is mounted, the optional binary DB
# detection step (src/binaryDB_detect.py) runs before the SBOM is generated.
set -euo pipefail

IN_DIR="${IN_DIR:-/input}"
OUT_DIR="${OUT_DIR:-/out}"
cd /app

SBOM_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --display-path)
      if [[ $# -lt 2 ]]; then
        echo "[!] --display-path needs a value." >&2
        exit 2
      fi
      SBOM_ARGS+=(--display-path "$2"); shift 2 ;;
    --display-path=*) SBOM_ARGS+=(--display-path "${1#*=}"); shift ;;
    --no-ldd) SBOM_ARGS+=(--no-ldd); shift ;;
    --) shift; break ;;
    -*) echo "[!] Unknown option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

TARGET="${1:-}"
if [[ -z "${TARGET}" ]]; then
  # No argument: pick the single file in the input directory.
  mapfile -t found < <(find "${IN_DIR}" -maxdepth 1 -type f 2>/dev/null)
  if [[ ${#found[@]} -eq 0 ]]; then
    echo "[!] No input binary. Mount one under ${IN_DIR} or pass its path as an argument." >&2
    exit 2
  fi
  if [[ ${#found[@]} -gt 1 ]]; then
    echo "[!] ${IN_DIR} contains several files. Pass the path of the binary to analyze." >&2
    printf '    - %s\n' "${found[@]}" >&2
    exit 2
  fi
  TARGET="${found[0]}"
elif [[ ! -f "${TARGET}" ]]; then
  if command -v "${TARGET}" >/dev/null 2>&1; then
    exec "$@"
  fi
  echo "[!] Not a file or a command: ${TARGET}" >&2
  exit 2
fi

if [[ -z "$(find database -mindepth 1 ! -name .gitkeep -print -quit 2>/dev/null)" ]]; then
  echo "[!] The component DB is empty. Mount a built database at /app/database." >&2
  exit 2
fi

NAME="$(basename "${TARGET}")"

# The pipeline scripts work on every file in testset/bin, so keep only this one.
STAGED="$(mktemp)"
cp "${TARGET}" "${STAGED}"
find testset/bin -mindepth 1 ! -name .gitkeep -delete
mv "${STAGED}" "testset/bin/${NAME}"

python src/bin_preprocessor.py
python src/detector.py "${NAME}"

if [[ -f binaryDB/db_index.pkl ]]; then
  echo "[*] Binary DB found; running binary DB detection."
  python src/binaryDB_detect.py "${NAME}"
fi

python src/Binary_SBOM_Generation.py "${SBOM_ARGS[@]}" >/dev/null

SBOM="output/${NAME}-sbom.json"
if [[ ! -f "${SBOM}" ]]; then
  echo "[!] SBOM was not generated for ${NAME}." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
cp "${SBOM}" "${OUT_DIR}/"
echo "[*] SBOM written to ${OUT_DIR}/${NAME}-sbom.json"
