"""Keep startup failures visible in a log even in the windowed Windows build."""
import os
import sys
import traceback
from pathlib import Path

if __name__ == '__main__':
    directory = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LanternTV'
    if '--data-dir' in sys.argv:
        directory = Path(sys.argv[sys.argv.index('--data-dir') + 1])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'launcher.log').open('w', encoding='utf-8') as diagnostic:
        if sys.stdout is None:
            sys.stdout = diagnostic
        if sys.stderr is None:
            sys.stderr = diagnostic
        try:
            if '--check-receiver' in sys.argv:
                from lantern.diagnostics import check_receiver
                config = Path(sys.argv[sys.argv.index('--check-receiver') + 1])
                check_receiver(config, directory)
            else:
                from lantern.app import main
                main()
        except Exception:
            traceback.print_exc(file=diagnostic)
            sys.exit(1)
