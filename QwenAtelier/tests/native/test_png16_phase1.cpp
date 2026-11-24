// Phase-1 native check: float -> uint16 -> PNG -> (verified independently in Python).
// Compiles only the production conversion/encoder units; no model, no CUDA, no engine.
#include <cmath>
#include <chrono>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

#include "image_u16.h"
#include "png16.h"

namespace {

bool write_bytes(const std::string& path, const void* data, std::size_t size) {
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        return false;
    }
    stream.write(static_cast<const char*>(data), static_cast<std::streamsize>(size));
    return static_cast<bool>(stream);
}

struct Pattern {
    std::string name;
    int width      = 0;
    int height     = 0;
    int channels   = 0;
    int frames     = 1;
    bool planar_5d = false;  // false: [w][h][c][frame], true: [w][h][frame][c]
    std::vector<float> source;
};

std::string join(const std::string& directory, const std::string& name, const char* suffix) {
    return directory + "\\" + name + suffix;
}

// Contiguous dims are [w][h][c][frame] and [w][h][frame][c]; x is fastest.
std::size_t offset_4d(const Pattern& p, int x, int y, int c, int f) {
    return static_cast<std::size_t>(x) +
           static_cast<std::size_t>(p.width) *
               (static_cast<std::size_t>(y) +
                static_cast<std::size_t>(p.height) *
                    (static_cast<std::size_t>(c) + static_cast<std::size_t>(p.channels) * f));
}

std::size_t offset_5d(const Pattern& p, int x, int y, int c, int f) {
    return static_cast<std::size_t>(x) +
           static_cast<std::size_t>(p.width) *
               (static_cast<std::size_t>(y) +
                static_cast<std::size_t>(p.height) *
                    (static_cast<std::size_t>(f) + static_cast<std::size_t>(p.frames) * c));
}

Pattern make_ramp() {
    Pattern p;
    p.name     = "ramp3";
    p.width    = 256;
    p.height   = 256;
    p.channels = 3;
    p.frames   = 1;
    p.source.resize(static_cast<std::size_t>(p.width) * p.height * p.channels, 0.0f);
    for (int y = 0; y < p.height; ++y) {
        for (int x = 0; x < p.width; ++x) {
            const int index = y * p.width + x;              // 0..65535
            const float v   = static_cast<float>(index) / 65535.0f;
            for (int c = 0; c < p.channels; ++c) {
                p.source[offset_4d(p, x, y, c, 0)] = v;
            }
        }
    }
    return p;
}

Pattern make_edges() {
    Pattern p;
    p.name     = "edges3";
    p.width    = 10;
    p.height   = 1;
    p.channels = 3;
    p.frames   = 1;
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const float inf = std::numeric_limits<float>::infinity();
    const float values[10] = {-1.0f, -0.0f, 1e-9f, 0.5f, 0.99999994f, 1.0f, 2.0f, nan, inf, -inf};
    p.source.resize(static_cast<std::size_t>(p.width) * p.channels, 0.0f);
    for (int x = 0; x < p.width; ++x) {
        for (int c = 0; c < p.channels; ++c) {
            p.source[offset_4d(p, x, 0, c, 0)] = values[x];
        }
    }
    return p;
}

Pattern make_planar(const char* name, bool layout_5d) {
    Pattern p;
    p.name      = name;
    p.width     = 3;
    p.height    = 2;
    p.channels  = 3;
    p.frames    = 2;
    p.planar_5d = layout_5d;
    p.source.resize(static_cast<std::size_t>(p.width) * p.height * p.channels * p.frames, 0.0f);
    for (int f = 0; f < p.frames; ++f) {
        for (int c = 0; c < p.channels; ++c) {
            for (int y = 0; y < p.height; ++y) {
                for (int x = 0; x < p.width; ++x) {
                    const int pixel = y * p.width + x;
                    const float v   = static_cast<float>(f * 100 + c * 10 + pixel) / 1000.0f;
                    const std::size_t at = layout_5d ? offset_5d(p, x, y, c, f) : offset_4d(p, x, y, c, f);
                    p.source[at] = v;
                }
            }
        }
    }
    return p;
}

Pattern make_rgba() {
    Pattern p;
    p.name     = "rgba4";
    p.width    = 64;
    p.height   = 32;
    p.channels = 4;
    p.frames   = 1;
    const std::size_t count = static_cast<std::size_t>(p.width) * p.height;
    p.source.resize(count * p.channels, 0.0f);
    for (std::size_t i = 0; i < count; ++i) {
        const int x = static_cast<int>(i % static_cast<std::size_t>(p.width));
        const int y = static_cast<int>(i / static_cast<std::size_t>(p.width));
        const float v = static_cast<float>(i) / static_cast<float>(count - 1);
        for (int c = 0; c < p.channels; ++c) {
            p.source[offset_4d(p, x, y, c, 0)] = v;
        }
    }
    return p;
}

bool run_pattern(const std::string& directory, const Pattern& p, std::string* manifest_entry) {
    const std::size_t samples = static_cast<std::size_t>(p.width) * p.height * p.channels;
    std::vector<uint16_t> converted(samples, 0);
    // Exact production entry point the engine wrapper will call (frame 0 of 1 for images).
    if (!sd_hp::frame_to_u16(p.source.data(), p.width, p.height, p.channels, 0, p.frames, p.planar_5d,
                             converted.data())) {
        std::fprintf(stderr, "frame_to_u16 failed for %s\n", p.name.c_str());
        return false;
    }

    const std::string f32_path = join(directory, p.name, ".f32");
    const std::string u16_path = join(directory, p.name, ".u16");
    const std::string png_path = join(directory, p.name, ".png");
    if (!write_bytes(f32_path, p.source.data(), p.source.size() * sizeof(float)) ||
        !write_bytes(u16_path, converted.data(), converted.size() * sizeof(uint16_t))) {
        std::fprintf(stderr, "write failed for %s\n", p.name.c_str());
        return false;
    }

    const std::string metadata = std::string("{\"phase\":1,\"pattern\":\"") + p.name + "\",\"note\":\"中文测试\"}";
    const std::vector<uint8_t> png =
        sd_hp::encode_png16(converted.data(), p.width, p.height, p.channels, "parameters", metadata, 6);
    if (png.empty() || !write_bytes(png_path, png.data(), png.size())) {
        std::fprintf(stderr, "encode_png16 failed for %s\n", p.name.c_str());
        return false;
    }

    char entry[512];
    std::snprintf(entry, sizeof(entry),
                  "{\"name\":\"%s\",\"width\":%d,\"height\":%d,\"channels\":%d,\"frames\":%d,"
                  "\"layout\":\"%s\",\"samples\":%zu,\"png_bytes\":%zu}",
                  p.name.c_str(), p.width, p.height, p.channels, p.frames,
                  p.planar_5d ? "w-h-frame-c" : "w-h-c-frame", samples, png.size());
    *manifest_entry = entry;
    return true;
}

bool check_rejections() {
    std::uint16_t one = 0;
    float value       = 0.5f;
    if (sd_hp::frame_to_u16(nullptr, 1, 1, 3, 0, 1, false, &one)) {
        return false;
    }
    if (sd_hp::frame_to_u16(&value, 1, 1, 0, 0, 1, false, &one)) {
        return false;
    }
    if (sd_hp::frame_to_u16(&value, 1, 1, 3, 1, 1, false, &one)) {
        return false;
    }
    if (!sd_hp::encode_png16(&one, 1, 1, 2).empty()) {
        return false;
    }
    if (!sd_hp::encode_png16(nullptr, 1, 1, 3).empty()) {
        return false;
    }
    return true;
}

// Synthetic 1024x1024 timing/size probe. Not a claim about real-image PNG size.
void bench_1024(const std::string& directory) {
    const int width = 1024, height = 1024, channels = 3;
    const std::size_t count = static_cast<std::size_t>(width) * height * channels;
    std::vector<float> source(count, 0.0f);
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const float fx = static_cast<float>(x) / (width - 1);
            const float fy = static_cast<float>(y) / (height - 1);
            const float v  = 0.65f * fx + 0.25f * fy + 0.05f * std::sin(fx * 37.0f) + 0.02f;
            for (int c = 0; c < channels; ++c) {
                const std::size_t at = static_cast<std::size_t>(x) +
                                       static_cast<std::size_t>(width) *
                                           (static_cast<std::size_t>(y) + static_cast<std::size_t>(height) * c);
                source[at] = v;
            }
        }
    }
    std::vector<uint16_t> converted(count, 0);
    const auto c0 = std::chrono::steady_clock::now();
    sd_hp::frame_to_u16(source.data(), width, height, channels, 0, 1, false, converted.data());
    const auto c1 = std::chrono::steady_clock::now();

    std::vector<double> encode_ms;
    std::vector<uint8_t> png;
    for (int i = 0; i < 3; ++i) {
        const auto e0 = std::chrono::steady_clock::now();
        png           = sd_hp::encode_png16(converted.data(), width, height, channels, "parameters", "", 6);
        const auto e1 = std::chrono::steady_clock::now();
        encode_ms.push_back(std::chrono::duration<double, std::milli>(e1 - e0).count());
    }
    if (png.empty()) {
        std::fprintf(stderr, "bench1024 encode failed\n");
        return;
    }
    write_bytes(join(directory, "bench1024", ".u16"), converted.data(), converted.size() * sizeof(uint16_t));
    write_bytes(join(directory, "bench1024", ".png"), png.data(), png.size());
    std::sort(encode_ms.begin(), encode_ms.end());
    std::printf("bench1024: convert=%.1fms encode_min=%.1fms encode_med=%.1fms png=%zuB raw_pixels=%zuB\n",
                std::chrono::duration<double, std::milli>(c1 - c0).count(), encode_ms.front(),
                encode_ms[encode_ms.size() / 2], png.size(), count * sizeof(uint16_t));
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s <output-directory>\n", argv[0]);
        return 2;
    }
    const std::string directory = argv[1];

    const std::vector<Pattern> patterns = {make_ramp(), make_edges(), make_planar("planar4d", false),
                                           make_planar("planar5d", true), make_rgba()};

    std::string manifest = "[\n";
    for (std::size_t i = 0; i < patterns.size(); ++i) {
        std::string entry;
        if (!run_pattern(directory, patterns[i], &entry)) {
            return 1;
        }
        manifest += "  " + entry + (i + 1 == patterns.size() ? "\n" : ",\n");
        std::printf("%s: %dx%d ch=%d frames=%d layout=%s\n", patterns[i].name.c_str(), patterns[i].width,
                    patterns[i].height, patterns[i].channels, patterns[i].frames,
                    patterns[i].planar_5d ? "w-h-frame-c" : "w-h-c-frame");
    }
    manifest += "]\n";
    if (!write_bytes(join(directory, "manifest", ".json"), manifest.data(), manifest.size())) {
        return 1;
    }
    if (!check_rejections()) {
        std::fprintf(stderr, "argument validation failed\n");
        return 1;
    }
    bench_1024(directory);
    std::printf("native phase-1 conversion + encoder completed\n");
    return 0;
}
