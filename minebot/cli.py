"""Offline administration, bounded discovery and verified original collection."""

import argparse
import json
from pathlib import Path

from .collector import run
from .collection import collect
from .diagnostics import check_github
from .validation import validate
from .lock import AlreadyRunning
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data/discovery'))
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'run', 'collect', 'validate', 'check-github', 'pause', 'resume'):
        commands.add_parser(name)
    probe = commands.add_parser('probe', help='Authenticate, then discover metadata with at most 10 total requests')
    probe.add_argument('--request-budget', type=int, default=10)
    listing = commands.add_parser('candidates')
    listing.add_argument('--limit', type=int, default=100)
    listing.add_argument('--offset', type=int, default=0)
    configure = commands.add_parser('configure')
    configure.add_argument('file', type=Path)
    for action in ('dismiss', 'restore', 'retry', 'retry-validation'):
        commands.add_parser(action).add_argument('candidate_id')
    commands.add_parser('attempts').add_argument('candidate_id')
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
        elif args.command == 'attempts':
            result = store.attempts(args.candidate_id)
        elif args.command in ('dismiss', 'restore', 'retry', 'retry-validation'):
            store.action(args.candidate_id, args.command)
            result = {'action': args.command, 'candidate_id': args.candidate_id}
        elif args.command == 'collect':
            result = collect(store)
        elif args.command == 'validate':
            result = validate(store)
        elif args.command in ('check-github', 'probe'):
            result = check_github(store, discover=args.command == 'probe',
                                  budget=getattr(args, 'request_budget', 10))
        else:
            result = run(store)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get('status') in ('setup_error', 'failed') else 0
    except (ValueError, OSError, AlreadyRunning) as error:
        print(json.dumps({'error': str(error)}))
        return 1
