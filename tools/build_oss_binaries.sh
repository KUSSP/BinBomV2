#!/usr/bin/env bash

# Build every locally collected OSS repository that can produce a native ELF
# artifact, then copy ELF executables/shared objects into binaryDB/db_input.
# The source tree is intentionally left in place; temporary CMake trees/logs
# live outside the repository.

set -u

ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
SRC="$ROOT/osscollector/repo_src"
OUT=${OUTPUT_DIR:-"$ROOT/binaryDB/db_input"}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/binary-sca-oss-build.XXXXXX")
LOGDIR="$WORK/logs"
MANIFEST="$OUT/.build_manifest.tsv"
mkdir -p "$OUT" "$LOGDIR"
trap 'rm -rf "$WORK"' EXIT

printf 'repo\tstatus\tcommand\tlog\n' > "$MANIFEST"

run_logged() {
    local repo=$1
    local label=$2
    shift 2
    local log="$LOGDIR/${repo//\//_}__${label}.log"
    printf '[%s] %s: %s\n' "$repo" "$label" "$*"
    timeout 20m "$@" >"$log" 2>&1
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        printf '%s\t%s\t%s\t%s\n' "$repo" "ok:$label" "$*" "$log" >> "$MANIFEST"
        return 0
    fi
    printf '%s\t%s\t%s\t%s\n' "$repo" "fail:$label:$rc" "$*" "$log" >> "$MANIFEST"
    printf '  build failed (rc=%s), see %s\n' "$rc" "$log"
    return 1
}

cmake_build() {
    local repo=$1
    local source=$2
    local name=$3
    shift 3
    local bdir="$WORK/cmake/$name"
    mkdir -p "$bdir"
    run_logged "$repo" "cmake-configure" cmake -S "$source" -B "$bdir" \
        -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_C_FLAGS_DEBUG='-O0 -g' -DCMAKE_CXX_FLAGS_DEBUG='-O0 -g' "$@" || return 1
    run_logged "$repo" "cmake-build" cmake --build "$bdir" --parallel 4 || return 1
}

make_build() {
    local repo=$1
    local dir=$2
    shift 2
    run_logged "$repo" "make-${dir##*/}" make -C "$dir" --jobs=4 "$@"
}

repo_dir() {
    printf '%s/%s' "$SRC" "$1"
}

# Repository-specific build recipes. Each recipe is allowed to fail so that
# independent repositories are still attempted.
build_repo() {
    local repo=$1
    local d
    d=$(repo_dir "$repo")
    mkdir -p "$WORK/manual/${repo//@@/__}"
    case "$repo" in
        'IlyaGrebnov@@libbsc'|'MarcinZukowski@@pivco-huffman'|'ebiggers@@libdeflate'|'evpobr@@sndfile2k'|'flanglet@@kanzi-cpp'|'google@@brunsli'|'google@@zopfli'|'hellobertrand@@zxc'|'libsndfile@@libsndfile'|'lzfse@@lzfse'|'madler@@zlib'|'nemequ@@lzo'|'richgel999@@lzham_codec_devel'|'richgel999@@miniz'|'robottwo@@quicklz'|'tukaani-project@@xz'|'welcome-to-the-sunny-side@@misa77'|'zlib-ng@@zlib-ng')
            cmake_build "$repo" "$d" "${repo//@@/__}" ;;
        'deus-libri@@preflate')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DCMAKE_CXX_FLAGS='-O0 -g -include stdint.h -D_ftelli64=ftello64 -D_fseeki64=fseeko64' ;;
        'google@@snappy')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DSNAPPY_BUILD_TESTS=OFF -DSNAPPY_BUILD_BENCHMARKS=OFF -DBUILD_SHARED_LIBS=ON ;;
        'josinli@@cknit')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DCMAKE_C_FLAGS_DEBUG='-O0 -g -fcommon' ;;
        'kspalaiologos@@bzip3')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DBZIP3_BUILD_APPS=OFF || true
            run_logged "$repo" manual-gcc gcc -O0 -g \
                "$d/src/main.c" "$d/src/libbz3.c" -I"$d/include" \
                -DVERSION='"1.5.3"' -lpthread -o "$WORK/manual/kspalaiologos__bzip3/bzip3" || true ;;
        'schnaader@@precomp-cpp')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DCMAKE_C_FLAGS_DEBUG='-O0 -g -include assert.h' \
                -DCMAKE_CXX_FLAGS_DEBUG='-O0 -g -include assert.h' ;;
        'skyformat99@@Exjson')
            cmake_build "$repo" "$d" "${repo//@@/__}" \
                -DCMAKE_C_FLAGS_DEBUG='-O0 -g -Wno-error=int-conversion' ;;
        'facebook@@zstd')
            cmake_build "$repo" "$d/build/cmake" "${repo//@@/__}" ;;
        'richox@@libzling')
            cmake_build "$repo" "$d/build" "${repo//@@/__}" || true
            mkdir -p "$WORK/manual/richox__libzling/libzling"
            for h in "$d"/src/*.h; do
                ln -sf "$h" "$WORK/manual/richox__libzling/libzling/$(basename "$h")"
            done
            run_logged "$repo" manual-g++ g++ -std=gnu++17 -O0 -g \
                "$d/demo/zling.cpp" "$d/src/libzling.cpp" \
                "$d/src/libzling_huffman.cpp" "$d/src/libzling_lz.cpp" \
                "$d/src/libzling_utils.cpp" -I"$WORK/manual/richox__libzling" \
                -DHAS_CXX11_SUPPORT=1 \
                -o "$WORK/manual/richox__libzling/zling" || true ;;
        'Distrotech@@ucl'|'nemequ@@liblzf')
            if [ -x "$d/configure" ]; then
                run_logged "$repo" configure bash -c "cd '$d' && CC=gcc CFLAGS='-std=gnu89 -O0 -g' ./configure --disable-shared --enable-static" || true
                make_build "$repo" "$d" || true
            else
                make_build "$repo" "$d" || true
            fi
            if [ "$repo" = 'nemequ@@liblzf' ]; then
                run_logged "$repo" manual-gcc gcc -O0 -g \
                    "$d/bench.c" "$d/lzf.c" "$d/lzf_c.c" "$d/lzf_c_best.c" "$d/lzf_d.c" \
                    -I"$d" -o "$WORK/manual/nemequ__liblzf/lzf-bench" || true
                run_logged "$repo" manual-shared gcc -O0 -g -fPIC -shared \
                    "$d/lzf_c.c" "$d/lzf_c_best.c" "$d/lzf_d.c" -I"$d" \
                    -o "$WORK/manual/nemequ__liblzf/liblzf.so" || true
            elif [ "$repo" = 'Distrotech@@ucl' ]; then
                run_logged "$repo" manual-shared gcc -O0 -g -fPIC -shared \
                    "$d/src"/*.c -I"$d" -I"$d/include" -I"$d/src" \
                    -std=gnu89 -DUCL_HAVE_CONFIG_H \
                    -o "$WORK/manual/Distrotech__ucl/libucl.so" || true
            fi ;;
        'powturbo@@Turbo-Range-Coder')
            make_build "$repo" "$d" -f makefile CC=gcc \
                'CFLAGS=-O0 -g -D_BWT -D_LIBSAIS -D_LIBSAIS16 -D_ANS -D_TRANSPOSE -D_NCPUISA -Ilibsais/include -Iinclude -Iinclude_' \
                'LDFLAGS=-lm' || true ;;
        'bzip2@@bzip2'|'conor42@@fast-lzma2'|'emmanuel-marty@@lzsa'|'facebook@@zstd'|'google@@gipfeli'|'inikep@@lizard'|'inikep@@lzbench'|'lzfse@@lzfse'|'madler@@zlib'|'p@@giflib@@code'|'robottwo@@quicklz'|'timothytylee@@libgsm'|'wtarreau@@libslz'|'zpaq@@zpaq'|'powturbo@@Turbo-Range-Coder'|'powturbo@@Turbo-Run-Length-Encoding'|'powturbo@@TurboBench'|'deus-libri@@preflate')
            if [ -f "$d/Makefile" ]; then make_build "$repo" "$d" || true
            elif [ -f "$d/makefile" ]; then make_build "$repo" "$d" -f makefile || true
            fi ;;
        'BrianPugh@@tamp')
            make_build "$repo" "$d" c-test || true ;;
        'coderforlife@@ms-compress')
            run_logged "$repo" build-sh bash -c "cd '$d' && bash build.sh" || true ;;
        'ShaneWF@@wflz')
            make_build "$repo" "$d/example" || true ;;
        'ariya@@FastLZ')
            make_build "$repo" "$d/examples" || true
            make_build "$repo" "$d/tests" || true ;;
        'mbitsnbites@@liblzg')
            make_build "$repo" "$d/src" || true ;;
        'packjpg@@packJPG'|'packjpg@@packMP3')
            make_build "$repo" "$d/source" || true ;;
        'google@@gipfeli')
            make_build "$repo" "$d" || true ;;
        'fusiyuan2010@@CSC')
            make_build "$repo" "$d/src" || true ;;
        'ip7z@@7zip'|'sisong@@lzma')
            if [ -f "$d/C/Util/7z/makefile.gcc" ]; then
                make_build "$repo" "$d/C/Util/7z" -f makefile.gcc || true
            fi ;;
        'ConorStokes@@LZSSE')
            run_logged "$repo" manual-g++ g++ -std=c++11 -O0 -g \
                "$d/example/main.cpp" "$d/lzsse2/lzsse2.cpp" \
                "$d/lzsse4/lzsse4.cpp" "$d/lzsse8/lzsse8.cpp" \
                -I"$d/lzsse2" -I"$d/lzsse4" -I"$d/lzsse8" \
                -I"$d" -msse4.1 -o "$WORK/manual/ConorStokes__LZSSE/LZSSE-example" || true ;;
        'Bulat-Ziganshin@@FA')
            run_logged "$repo" manual-g++ g++ -std=c++11 -O0 -g \
                -D__cdecl= "$d/CELS/CELS.cpp" "$d/CELS/easy_codec.cpp" \
                "$d/CELS/simple_host.cpp" -I"$d/CELS" \
                -o "$WORK/manual/Bulat-Ziganshin__FA/FA-cels" || true
            run_logged "$repo" protobuf-g++ g++ -std=c++11 -O0 -g \
                "$d/ProtoBuf"/*.cpp "$d/ProtoBuf/src/generator/descriptor.pb.cpp" \
                -I"$d/ProtoBuf" -I"$d/ProtoBuf/src/generator" -o "$WORK/manual/Bulat-Ziganshin__FA/FA-protobuf" || true ;;
        'jibsen@@brieflz')
            make_build "$repo" "$d/examples" -f makefile.elf || true ;;
        'jrmuizel@@GLZA')
            run_logged "$repo" manual-gcc gcc -O0 -g \
                "$d/GLZAcompress.c" -lm -pthread -o "$WORK/manual/jrmuizel__GLZA/GLZAcompress" || true
            run_logged "$repo" manual-gcc-encode gcc -O0 -g "$d/GLZAencode.c" -lm -o "$WORK/manual/jrmuizel__GLZA/GLZAencode" || true
            run_logged "$repo" manual-gcc-decode gcc -O0 -g "$d/GLZAdecode.c" -lm -pthread -o "$WORK/manual/jrmuizel__GLZA/GLZAdecode" || true
            run_logged "$repo" manual-gcc-format gcc -O0 -g "$d/GLZAformat.c" -lm -o "$WORK/manual/jrmuizel__GLZA/GLZAformat" || true ;;
        'nemequ@@lzmat')
            run_logged "$repo" manual-shared gcc -O0 -g -fPIC -shared \
                "$d/lzmat_enc.c" "$d/lzmat_dec.c" -I"$d" \
                -o "$WORK/manual/nemequ__lzmat/liblzmat.so" || true ;;
        'macosforge@@alac')
            make_build "$repo" "$d/codec" || true
            run_logged "$repo" manual-g++ g++ -std=gnu++17 -O0 -g \
                -I"$d/codec" -I"$d/convert-utility" \
                "$d/convert-utility/main.cpp" "$d/convert-utility/CAFFileALAC.cpp" \
                "$d/codec/EndianPortable.c" "$d/codec/ALACBitUtilities.c" \
                "$d/codec/ALACDecoder.cpp" "$d/codec/ALACEncoder.cpp" \
                "$d/codec/ag_dec.c" "$d/codec/ag_enc.c" \
                "$d/codec/dp_dec.c" "$d/codec/dp_enc.c" \
                "$d/codec/matrix_dec.c" "$d/codec/matrix_enc.c" \
                -o "$WORK/manual/macosforge__alac/alacconvert" || true ;;
        *)
            if [ -f "$d/CMakeLists.txt" ]; then
                cmake_build "$repo" "$d" "${repo//@@/__}" || true
            elif [ -f "$d/Makefile" ]; then
                make_build "$repo" "$d" || true
            else
                printf '%s\tno-build-system\t-\t-\n' "$repo" >> "$MANIFEST"
            fi ;;
    esac
}

collect_repo() {
    local repo=$1
    local d
    d=$(repo_dir "$repo")
    local safe=${repo//@@/__}
    local roots=("$d" "$WORK/cmake/$safe" "$WORK/manual/$safe")
    while IFS= read -r -d '' f; do
        case "$f" in
            */.git/*|*/CMakeFiles/*) continue ;;
        esac
        local info
        info=$(file -b "$f")
        case "$info" in
            ELF*executable*|ELF*"shared object"*) ;;
            *) continue ;;
        esac
        local base dest rel digest n=0
        base=$(basename "$f")
        dest="$OUT/${safe}__${base}"
        while [ -e "$dest" ]; do
            digest=$(sha256sum "$f" | cut -c1-12)
            dest="$OUT/${safe}__${base}__${digest}"
            n=$((n + 1))
            [ "$n" -gt 2 ] && break
        done
        [ -e "$dest" ] && continue
        cp -p "$f" "$dest"
        chmod a+rX "$dest"
        printf '%s\tbinary\t%s\t%s\t%s\n' "$repo" "$f" "${dest##*/}" "$info" >> "$MANIFEST"
        printf '  collected %s -> %s\n' "$info" "${dest##*/}"
    done < <(find "${roots[@]}" -type f -perm /111 -print0 2>/dev/null)
}

mapfile -t REPOS < <(find "$SRC" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
for repo in "${REPOS[@]}"; do
    if [ -n "${ONLY_REPOS:-}" ] && [[ ",${ONLY_REPOS}," != *",${repo},"* ]]; then
        continue
    fi
    build_repo "$repo"
    collect_repo "$repo"
done

printf '%s\n' '--- ELF collection summary ---'
find "$OUT" -maxdepth 1 -type f ! -name .build_manifest.tsv -print0 |
    xargs -0 -r file | sort
printf 'Collected ELF files: '
find "$OUT" -maxdepth 1 -type f ! -name .build_manifest.tsv | wc -l
printf 'Build manifest: %s\n' "$MANIFEST"
