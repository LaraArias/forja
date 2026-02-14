import markdown


def convert_md_to_html(md_text: str) -> str:
    """Convert Markdown text to HTML string.

    Supports: headers (h1-h6), bold, italic, links, code blocks, and lists.
    """
    extensions = ["fenced_code"]
    return markdown.markdown(md_text, extensions=extensions)
