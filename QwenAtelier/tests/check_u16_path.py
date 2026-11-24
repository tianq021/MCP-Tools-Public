"""Static guard: the 16-bit path must not pass through the uint8 conversion.

This is a source check, not a runtime proof. It complements the compiled pixel
chain test by rejecting the specific regression where 16-bit output is produced
from sd_image_t (uint8) instead of the decoded float tensor.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "native" / "stable-diffusion.cpp"


def body(text: str, signature: str) -> str:
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
    raise AssertionError(f"unbalanced body for {signature}")


def read(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def main() -> int:
    failures = []

    converter = read("src/runtime/image_u16.cpp")
    for banned in ("float_to_u8", "uint8_t"):
        if banned in converter:
            failures.append(f"image_u16.cpp contains {banned}")

    preprocessing = read("src/runtime/preprocessing.hpp")
    wrapper = body(preprocessing, "preprocessing_tensor_frame_to_sd_image_u16")
    if "preprocessing_tensor_frame_to_sd_image(" in wrapper or "float_to_u8" in wrapper:
        failures.append("u16 preprocessing wrapper calls the uint8 conversion")
    if "sd_hp::frame_to_u16" not in wrapper:
        failures.append("u16 preprocessing wrapper does not call sd_hp::frame_to_u16")

    util = read("src/core/util.cpp")
    util_body = body(util, "sd_image_u16_t tensor_to_sd_image_u16")
    if "preprocessing_tensor_frame_to_sd_image(tensor" in util_body.replace(
            "preprocessing_tensor_frame_to_sd_image_u16", ""):
        failures.append("util.cpp u16 conversion calls the uint8 tensor conversion")
    if "preprocessing_tensor_frame_to_sd_image_u16" not in util_body:
        failures.append("util.cpp u16 conversion does not call the u16 wrapper")

    image = read("src/pipeline/image.cpp")
    for signature in ("static sd_image_u16_t* tensors_to_sd_images_u16",
                      "static sd_image_u16_t* decode_image_outputs_u16"):
        block = body(image, signature)
        if "tensor_to_sd_image(" in block:
            failures.append(f"{signature} calls the uint8 tensor_to_sd_image")

    server = read("examples/server/async_jobs.cpp")
    block = body(server, "bool execute_img_gen_job")
    high = block[block.index("if (high_precision)"):block.index("SDImageVec results;")]
    if "generate_image_u16" not in high or "encode_png16" not in high:
        failures.append("server 16-bit branch does not use generate_image_u16/encode_png16")
    if "encode_image_to_vector" in high or "generate_image(" in high:
        failures.append("server 16-bit branch reuses an 8-bit encoder/entry point")

    if failures:
        print("FAIL")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: 16-bit path has no uint8 conversion call in the checked functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
