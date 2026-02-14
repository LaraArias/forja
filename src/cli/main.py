import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from converter.converter import convert_md_to_html


def main():
    parser = argparse.ArgumentParser(description="Convert Markdown to HTML")
    parser.add_argument("input", help="Input Markdown file")
    parser.add_argument("-o", "--output", help="Output HTML file (default: stdout)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: file '{args.input}' not found", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        md_content = f.read()

    html = convert_md_to_html(md_content)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
    else:
        print(html)


if __name__ == "__main__":
    main()
