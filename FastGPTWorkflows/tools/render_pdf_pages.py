import argparse
from pathlib import Path

import pypdfium2 as pdfium


def main():
    parser = argparse.ArgumentParser(description="Render PDF pages to PNG for visual QA")
    parser.add_argument("pdf")
    parser.add_argument("output_dir")
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    document = pdfium.PdfDocument(str(pdf_path))
    for index in range(len(document)):
        page = document[index]
        bitmap = page.render(scale=args.scale)
        image = bitmap.to_pil()
        output_path = output_dir / f"page-{index + 1}.png"
        image.save(output_path)
        print(f"Wrote {output_path}")
        image.close()
        bitmap.close()
        page.close()


if __name__ == "__main__":
    main()
