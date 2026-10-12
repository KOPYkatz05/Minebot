"""Offline administration and bounded metadata discovery."""

import argparse
import json
from pathlib import Path

from .collector import run
from .lock import AlreadyRunning
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data/discovery'))
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'run', 'pause', 'resume'):
        commands.add_parser(name)
    listing = commands.add_parser('candidates')
    listing.add_argument('--limit', type=int, default=100)
    listing.add_argument('--offset', type=int, default=0)
    configure = commands.add_parser('configure')
    configure.add_argument('file', type=Path)
    for action in ('dismiss', 'restore'):
        commands.add_parser(action).add_argument('candidate_id')
    args = parser.parse_args(argv)
    try:
        store = Store(args.data_dir)
        if args.command in ('init', 'status'):
            result = store.status()
        elif args.command == 'configure':
            result = store.configure(json.loads(args.file.read_text(encoding='utf-8')))
        elif args.command in ('pause', 'resume'):
            result = store.configure({'paused': args.command == 'pause'})
        elif args.command == 'candidates':
            result = store.candidates(args.limit, args.offset)
        elif args.command in ('dismiss', 'restore'):
            store.action(args.candidate_id, args.command)
            result = {'action': args.command, 'candidate_id': args.candidate_id}
        else:
            result = run(store)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get('status') in ('setup_error', 'failed') else 0
    except (ValueError, OSError, AlreadyRunning) as error:
        print(json.dumps({'error': str(error)}))
        return 1
