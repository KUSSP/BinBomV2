# BinBomV2

BinBomV2: **Binary Software Composition Analysis (SCA)** tool that detects the open-source software (OSS) components contained in binaries.

BinBomV2 builds a component database from OSS source code, decompiles the target binaries with [Ghidra](https://github.com/NationalSecurityAgency/ghidra), and matches them against the database to identify which open-source components each binary contains. The results are exported as a [CycloneDX](https://cyclonedx.org/) SBOM.

The source-to-binary function matching uses the control block-based technique from [SBridge](https://doi.org/10.1145/3797090) (Yang et al., *SBridge: Identifying Source-to-Binary Function Similarity via Cross-Domain Control Block Matching*, FSE 2026).

```
OSS source collection  →  Component DB construction  →  Binary preprocessing  →  Detection  →  (Optional) Binary DB detection  →  SBOM generation
```

## Installation

### Prerequisites

- **JDK 21 or later** (required for Ghidra headless decompilation)
- **universal-ctags**
- **Python 3.10 or later** and [uv](https://github.com/astral-sh/uv)
- A Debian/Ubuntu-based Linux distribution (required by the installation script)

To run BinBomV2 in a container instead, see [Docker (Optional)](#docker-optional).

### Clone the Repository

```bash
git clone --recurse-submodules git@github.com:KUSSP/BinBomV2.git
cd BinBomV2
```

If you have already cloned the repository, run `git submodule update --init` to fetch the `py-bsim` submodule. The submodule is only used by the optional binary DB detection step.

### Install External Tools (Ghidra, ctags)

The following script installs Ghidra 11.2.1 and universal-ctags. Ghidra must be installed under `tools/ghidra/`, so run the script from the `tools/` directory.

```bash
cd tools
bash external_tools_install.sh
cd ..
```

### Install Python Dependencies

```bash
uv sync
```

## Usage

### 1. Collect OSS Sources

List the repositories and versions to collect in `osscollector/repolist.txt`, one per line, in the format `<repository URL>, <library|application>, ["version tag", ...]`.

```
https://gitlab.com/bzip2/bzip2.git, library, ["bzip2-1.0.8"]
https://github.com/madler/zlib.git, library, ["v1.3.2"]
https://github.com/inikep/lzbench.git, application
```

The component type defaults to `library`, and the version tag list is optional. When no version tag is given, the latest version tag of the repository is collected.

Then run the collector. The collected data is stored under `osscollector/`.

```bash
uv run python src/OSScollector.py osscollector
```

### 2. Build the Component DB

Build the component database used for detection from the collected OSS sources. The database is written to `database/`.

```bash
uv run python src/preprocessor.py
```

### 3. Preprocess Binaries

Place the binaries to analyze in `testset/bin/`, then decompile and preprocess **all of them**. This step can take a long time. The preprocessed data is stored in `testset/<binary>/`.

```bash
uv run python src/bin_preprocessor.py
```

### 4. Detect Components

Match the preprocessed binaries against the component DB to detect their dependencies.

```bash
# Analyze all binaries
uv run python src/detector.py

# Analyze a specific binary
uv run python src/detector.py redis-server

# Print detailed phase-by-phase progress
uv run python src/detector.py -v
```

The detection results are saved to `output/<binary>/<binary>.components.json`.

### (Optional) Binary DB Detection

Matches the target binaries against reference binaries to reduce false negatives of step 4.

Place each reference binary in `binaryDB/db_input/` together with a `<binary>.meta.json` that names its component (`owner@@repo`, as in the component DB).

```
binaryDB/db_input/night-shift__fpconv__libfpconv.so
binaryDB/db_input/night-shift__fpconv__libfpconv.so.meta.json   {"component": "night-shift@@fpconv", "version": "11ed624b20a3bda6284a428b35d4e8815e47e098"}
```

```bash
uv run python src/binaryDB_build.py     # build the binary DB
uv run python src/binaryDB_detect.py    # run after step 4
```

### 5. Generate the SBOM

Generate CycloneDX SBOMs from the binaries in `testset/bin` and the detection results in `output/`.

```bash
uv run python src/Binary_SBOM_Generation.py
```

For binaries that have binary DB detection results (`<binary>.binaryDB.components.json`), the SBOM is generated from those results; otherwise, it is generated from the step 4 results. Each SBOM is saved to `output/<binary>-sbom.json`.

| Option | Description |
|---|---|
| `--display-path <path>` | Write `<path>` to the SBOM's `binary:file:path` instead of the analyzed file's path. Requires exactly one binary in `testset/bin`. |
| `--no-ldd` | Do not add the shared libraries `ldd` reports to `dependencies`. `ldd` lists the libraries of the machine running the analysis, not of the machine the binary came from. |

## Docker (Optional)

The provided `Dockerfile` builds an image with JDK 21, universal-ctags, Ghidra 11.2.1, the Python dependencies and `py-bsim`. Data is not included in the image; mount it as volumes.

### Build the Image

```bash
git submodule update --init   # py-bsim is required to build the image
docker build -t binbomv2 .
```

### Build the Databases

Any command passed to the container is run in `/app`, so steps 1 and 2, and the optional binary DB build, can be run inside it.

```bash
docker run --rm \
  -v "$PWD/osscollector:/app/osscollector" \
  -v "$PWD/database:/app/database" \
  binbomv2 sh -c "python src/OSScollector.py osscollector && python src/preprocessor.py"

# (Optional) Build the binary DB from binaryDB/db_input/
docker run --rm -v "$PWD/binaryDB:/app/binaryDB" binbomv2 python src/binaryDB_build.py
```

### Analyze a Binary

Put a single binary in a directory and mount it at `/input`. The container runs steps 3 to 5 on it and writes `<binary>-sbom.json` to `/out`.

```bash
mkdir -p input out
cp testset/bin/redis-server input/

docker run --rm \
  -v "$PWD/database:/app/database:ro" \
  -v "$PWD/osscollector:/app/osscollector:ro" \
  -v "$PWD/input:/input:ro" \
  -v "$PWD/out:/out" \
  binbomv2
```

- `/app/database` and `/app/osscollector` are required. The detector reads release metadata from `osscollector/repo_meta/`.
- If `/input` contains several files, pass the binary path as an argument, such as `binbomv2 /input/redis-server`.
- The SBOM options `--display-path <path>` and `--no-ldd` (see step 5) go before the binary path: `binbomv2 --display-path uploads/redis-server --no-ldd`.
- To include the optional binary DB detection, also mount `-v "$PWD/binaryDB:/app/binaryDB"`. When `binaryDB/db_index.pkl` exists, the container runs `binaryDB_detect.py` before generating the SBOM. Mount it writable, because BSim signatures of the target binaries are cached in `binaryDB/target_sig/`.
- The container runs as root, so files it creates in mounted directories are owned by root.

## Project Structure

```
.
├── src/                          # Source code
│   ├── OSScollector.py           # 1. OSS source collection
│   ├── preprocessor.py           # 2. Component DB construction
│   ├── bin_preprocessor.py       # 3. Binary decompilation and preprocessing
│   ├── detector.py               # 4. Dependency detection
│   ├── binaryDB_build.py         #    (Optional) Binary DB construction
│   ├── binaryDB_detect.py        #    (Optional) Binary DB detection
│   ├── Binary_SBOM_Generation.py # 5. SBOM generation
│   ├── cyclonedx_sbom_gen.py     #    CycloneDX SBOM generation logic
│   └── utils/                    #    Preprocessing and similarity utilities
├── osscollector/
│   └── repolist.txt              # List of repositories to collect
├── database/                     # Component DB
├── testset/
│   └── bin/                      # Target binaries
├── output/                       # Detection results and SBOMs
├── binaryDB/                     # (Optional) Binary DB
│   └── db_input/                 # Reference binaries (owner__repo__binary)
├── py-bsim/                      # (Optional) BSim vector comparison library (submodule)
├── tools/
│   ├── external_tools_install.sh # Installs Ghidra and ctags
│   ├── build_oss_binaries.sh     # (Optional) Helper for building reference binaries
│   └── ghidra/                   # Ghidra installation directory
├── Dockerfile                    # (Optional) Container image
├── docker-entrypoint.sh          #    Container entry point
├── pyproject.toml
└── uv.lock
```

## License

This project is licensed under the [Apache License 2.0](LICENSE).


## Contact

Heedong Yang (heedongy@korea.ac.kr), SSP Lab., Korea University
