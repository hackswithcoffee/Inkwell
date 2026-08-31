"""CLI: python -m inkwell.extractor <transcript> [--context recap] [--allies roster]"""
import argparse

from .passes import extract_data

parser = argparse.ArgumentParser(description="Inkwell session data extractor")
parser.add_argument("transcript_path", help="Path to the cleaned transcript")
parser.add_argument("--context", default=None, help="Path to the previous session recap for continuity")
parser.add_argument("--allies", default=None, help="Path to the allies roster file for context")
args = parser.parse_args()
extract_data(args.transcript_path, args.context, args.allies)
