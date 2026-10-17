"""All reply generation lives here; independent of the message transport."""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
INSTRUCTIONS = (
    'You are Echo, a helpful personal assistant chatting over iMessage. '
    'Give clear, concise, conversational answers. Use plain text. '
    'You have no tools or access to the computer, calendar, files, or live web. '
    'Never claim to have taken an action or scheduled a reminder. '
    'Never begin a reply with /echo.'
)


def process_message(message: str, sender: str) -> str:
    """Generate one stateless reply. Sender stays local; no history is uploaded."""
    load_dotenv(ENV_PATH)
    if not os.environ.get('OPENAI_API_KEY', '').strip():
        raise ValueError('Set OPENAI_API_KEY in the project .env file')
    with OpenAI(timeout=30.0, max_retries=1) as client:
        result = client.responses.create(
            model=os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini'),
            instructions=INSTRUCTIONS,
            input=message,
            max_output_tokens=600,
            store=False,
        )
    if result.status != 'completed' or not result.output_text.strip():
        raise RuntimeError('OpenAI did not return a complete text response')
    return result.output_text.strip()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Test OpenAI without sending iMessages.')
    parser.add_argument('message', nargs='?', default='Hello')
    parser.add_argument('--sender', default='local-test')
    args = parser.parse_args()
    try:
        print(process_message(args.message, args.sender))
    except Exception as exc:
        # API exceptions can include request details; do not expose them in logs.
        parser.exit(1, f'Agent failed ({type(exc).__name__}). Check your API key, model, billing, and connection.\n')
