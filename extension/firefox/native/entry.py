"""Console-only native helper: Firefox needs binary stdin/stdout handles."""
import argparse
from pathlib import Path

from lantern.browser_bridge import native_host_main

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--data-dir", type=Path)
options, _firefox_arguments = parser.parse_known_args()
raise SystemExit(native_host_main(options.data_dir))
