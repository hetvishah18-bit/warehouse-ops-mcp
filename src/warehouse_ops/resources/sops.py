"""Standard operating procedures, stored as Markdown files in sop_docs/."""

from pathlib import Path

SOP_DIR = Path(__file__).resolve().parent / "sop_docs"


def _reader(path: Path):
    """Make a no-argument function that returns one SOP file's text."""
    def read() -> str:
        return path.read_text()
    return read


def register(mcp) -> None:
    for path in sorted(SOP_DIR.glob("*.md")):
        topic = path.stem  # e.g. 'yard-management'
        mcp.resource(
            f"warehouse://sops/{topic}",
            name=f"sop-{topic}",
            title=f"SOP: {topic.replace('-', ' ').title()}",
            description=f"Standard operating procedure for {topic.replace('-', ' ')}.",
            mime_type="text/markdown",
        )(_reader(path))
