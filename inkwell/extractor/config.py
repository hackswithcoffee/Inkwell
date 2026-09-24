"""Model choices and tunables for the extraction passes."""
import os

# The extractor runs as its own process, so it resolves the repo root itself
# rather than importing the pipeline's config for it.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Sized for a 32GB Apple Silicon Mac, where macOS lets the GPU wire roughly
# 21–24GB. gemma4:26b is a mixture-of-experts model (~4B active per token), so
# it writes at small-model speed with 26B-class judgment, and at 18GB it fits
# with room left for the KV cache. One model for every pass means Ollama loads
# it once per run instead of swapping between passes. The two names stay
# separate so the passes can be split across models again by editing one line.
NARRATIVE_MODEL = "gemma4:26b"
EXTRACTION_MODEL = "gemma4:26b"

# One context size for every call. Ollama reloads the model whenever num_ctx
# changes, so letting it vary per call would reload an 18GB model per chunk.
# 32K covers the whole-session passes (every chunk summary plus the previous
# recap and the rules primer) for a four-hour session; a prompt that would
# not fit raises it for that call rather than being silently truncated.
NUM_CTX = 32768
MAX_NUM_CTX = 131072

# Seconds to wait on a single generation before giving up on it.
REQUEST_TIMEOUT = 1800

CHUNK_SIZE_WORDS = 2000
PRIMER_FILENAME = "summarizer_primer.md"
