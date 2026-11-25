import argparse
import os

import cyclonedx_sbom_gen

currentPath = os.getcwd()
OUTPUT_DIR = currentPath + "/output"
TARGET_BIN_DIR = currentPath + "/testset/bin"


def generate_sbom(bin_name: str, display_path=None, ldd=True):
    binary_path = os.path.join(TARGET_BIN_DIR, bin_name)
    components_path = os.path.join(OUTPUT_DIR, bin_name, f"{bin_name}.components.json")
    binarydb_path = os.path.join(OUTPUT_DIR, bin_name, f"{bin_name}.binaryDB.components.json")
    if os.path.exists(binarydb_path):
        print(f"    [*] Using binaryDB components: {binarydb_path}")
        components_path = binarydb_path

    if not os.path.exists(components_path):
        print(f"    [!] Components file not found: {components_path}")
        return

    print(f"    [*] Generating CycloneDX SBOM for {bin_name}...")
    args = [binary_path, components_path]
    if ldd:
        args.append("--ldd")
    if display_path:
        args += ["--display-path", display_path]
    cyclonedx_sbom_gen.main(args)

    sbom_path = os.path.join(OUTPUT_DIR, f"{bin_name}-sbom.json")
    if os.path.exists(sbom_path):
        print(f"\n{'='*60}")
        print(f"   {bin_name} SBOM")
        print(f"{'='*60}")
        with open(sbom_path, "r", encoding="utf-8") as f:
            sbom_content = f.read()
        print(sbom_content)
        print(f"{'='*60}\n")


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate CycloneDX SBOMs for the binaries in testset/bin.")
    p.add_argument("--display-path", help="Path written to binary:file:path; only with a single binary.")
    p.add_argument("--no-ldd", action="store_true", help="Do not add the shared libraries ldd reports.")
    args = p.parse_args(argv)

    binaries = sorted(f for f in os.listdir(TARGET_BIN_DIR)
                      if not f.startswith(".") and os.path.isfile(os.path.join(TARGET_BIN_DIR, f)))
    if args.display_path and len(binaries) != 1:
        p.error(f"--display-path needs exactly one binary in {TARGET_BIN_DIR}, found {len(binaries)}")
    for bin_name in binaries:
        print(f"[*] Generating SBOM for binary: {bin_name}")
        generate_sbom(bin_name, args.display_path, not args.no_ldd)


if __name__ == "__main__":
    main()
