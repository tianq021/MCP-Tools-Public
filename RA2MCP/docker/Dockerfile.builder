# Lean builder image for ra2yrcpp x86 (i686) Windows DLL.
# Mirrors ra2yrcpp/docker/Dockerfile essentials, but builds protobuf from the
# pinned 3rdparty/protobuf submodule (v21.12 / 3.21.12) instead of cloning, and
# skips cppcheck/code-quality tooling that the DLL build does not need.
#
# Build context: E:\RA2MCP\vendor\ra2yrcpp
#   docker build -f E:\RA2MCP\docker\Dockerfile.builder -t ra2mcp-builder:local E:\RA2MCP\vendor\ra2yrcpp
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN dpkg --add-architecture i386 \
    && apt-get update -y \
    && apt-get install --no-install-recommends -y \
        bash \
        ca-certificates \
        cmake \
        g++-mingw-w64-i686-posix \
        git \
        libz-mingw-w64-dev \
        make \
        protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN protoc --version \
    && i686-w64-mingw32-g++ --version | head -1 \
    && cmake --version | head -1 \
    && ls -l /usr/i686-w64-mingw32/lib/libz.a /usr/i686-w64-mingw32/include/zlib.h

WORKDIR /app

# Protobuf source: pinned submodule, not a git clone (offline-friendly, exact version).
COPY 3rdparty/protobuf /app/protobuf
COPY toolchains/mingw-w64-i686.cmake /app/protobuf/

WORKDIR /app/protobuf

# Static libprotobuf for i686-w64-mingw32. Native protoc comes from Ubuntu
# (3.21.12, the same protocol version as the submodule), so no double build.
RUN cmake \
        -DCMAKE_INSTALL_PREFIX=/usr/i686-w64-mingw32 \
        -Dprotobuf_BUILD_LIBPROTOC=OFF \
        -Dprotobuf_BUILD_PROTOC_BINARIES=OFF \
        -Dprotobuf_WITH_ZLIB=ON \
        -DProtobuf_USE_STATIC_LIBS=ON \
        -Dprotobuf_MSVC_STATIC_RUNTIME=OFF \
        -Dprotobuf_BUILD_EXAMPLES=OFF \
        -Dprotobuf_BUILD_TESTS=OFF \
        -DZLIB_LIB=/usr/i686-w64-mingw32/lib \
        -DZLIB_INCLUDE_DIR=/usr/i686-w64-mingw32/include \
        --toolchain mingw-w64-i686.cmake \
        -S . -B build/win32 \
    && cmake --build build/win32 --parallel 8 \
    && cd build/win32 \
    && make install

RUN ls -l /usr/i686-w64-mingw32/lib/libprotobuf.a /usr/i686-w64-mingw32/include/google/protobuf/message.h

WORKDIR /work
CMD ["bash"]
