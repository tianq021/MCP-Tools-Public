#!/usr/bin/env bash
# Build ra2yrcpp x86 (i686-w64-mingw32) inside the ra2mcp-builder:local image.
# Invoked by scripts/build_ra2yrcpp_docker.ps1 via `docker run`.
#
# Mounts (see the PowerShell wrapper):
#   /work/repo  = E:\RA2MCP\vendor\ra2yrcpp
#   /work/out   = E:\RA2MCP\build
set -euo pipefail

REPO=/work/repo
OUT=/work/out
BUILD="$OUT/ra2yrcpp-i686"
PKG="$OUT/pkg"

mkdir -p "$BUILD" "$PKG"

# The upstream Dockerfile does this for the same reason: ra2yrcpp's
# CMakeLists.txt requires all four MinGW runtime DLLs under the sysroot
# (they are only copied next to test executables; tests are off here, but
# find_file(... REQUIRED) still runs at configure time).
for f in libgcc_s_dw2-1.dll libstdc++-6.dll; do
    ln -sf /usr/lib/gcc/i686-w64-mingw32/*-posix/"$f" /usr/i686-w64-mingw32/lib/"$f"
done

export PROTOC_PATH=/usr/bin/protoc
SOVERSION="dsh-$(git -C "$REPO" rev-parse --short HEAD)"

echo "== protoc: $($PROTOC_PATH --version)"
echo "== cmake:  $(cmake --version | head -1)"
echo "== g++:    $(i686-w64-mingw32-g++ --version | head -1)"
echo "== version: $SOVERSION"
echo "== protobuf lib: $(ls -l /usr/i686-w64-mingw32/lib/libprotobuf.a)"

cmake \
    -DCMAKE_INSTALL_PREFIX="$PKG" \
    -DRA2YRCPP_VERSION="$SOVERSION" \
    -DRA2YRCPP_BUILD_MAIN_DLL=ON \
    -DRA2YRCPP_BUILD_TESTS=OFF \
    -DRA2YRCPP_BUILD_CLI_TOOL=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    --toolchain "$REPO/toolchains/mingw-w64-i686.cmake" \
    -S "$REPO" -B "$BUILD"

cmake --build "$BUILD" --parallel 8

cmake --build "$BUILD" --target install

echo "== install tree =="
find "$PKG" -type f | sort
