"""Model choices and tunables for the extraction passes."""
import os

# The extractor runs as its own process, so it resolves the repo root itself
# rather than importing the pipeline's config for it.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
NARRATIVE_MODEL = "mistral-nemo:12b"
EXTRACTION_MODEL = "mistral:7b"
CHUNK_SIZE_WORDS = 2000
PRIMER_FILENAME = "summarizer_primer.md"
