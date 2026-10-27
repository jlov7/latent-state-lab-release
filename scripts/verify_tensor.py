"""Require full restoration/replay and a discriminating text control."""
import json

from latent_state_lab.tensor_probe import compare


def main():
    report = compare()
    for arm in ('uninterrupted', 'restore', 'replay'):
        if not report['arms'][arm]['exact_state_match'] or report['arms'][arm]['token_matches'] != 12:
            raise RuntimeError('untrained numerical continuation differs: ' + arm)
    if report['arms']['text']['exact_state_match']:
        raise RuntimeError('text-only negative control did not discriminate')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
