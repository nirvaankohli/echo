"""All reply generation lives here; independent of the message transport."""

import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from echo.runtime.config import load_env

ENV_PATH = Path(__file__).resolve().parents[2] / '.env'
INSTRUCTIONS = (
    'You are Echo, a helpful personal assistant chatting over iMessage. '
    'Give clear, concise, conversational answers. Use plain text. '
    'Only claim actions confirmed by available tools. '
    'Never begin a reply with /echo.'
)


def process_message(message: str, sender: str, *, allow_tools: bool = False) -> str:
    """Generate one stateless reply. Sender stays local; no history is uploaded."""
    load_env()
    load_dotenv(ENV_PATH)
    if not os.environ.get('OPENAI_API_KEY', '').strip():
        raise ValueError('Set OPENAI_API_KEY in the project .env file')
    with OpenAI(timeout=30.0, max_retries=1) as client:
        if allow_tools:
            from echo.tools.client import enabled_for, respond
            if enabled_for(sender):
                return asyncio.run(respond(client, message, INSTRUCTIONS,
                                           os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')))
        result = client.responses.create(
            model=os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini'),
            instructions=INSTRUCTIONS + 'You have no tools, computer access, or scheduling capability.',
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
    parser.add_argument('--tools', action='store_true', help='Allow real tools for the configured owner')
    args = parser.parse_args()
    try:
        print(process_message(args.message, args.sender, allow_tools=args.tools))
    except Exception as exc:
        # API exceptions can include request details; do not expose them in logs.
        parser.exit(1, f'Agent failed ({type(exc).__name__}). Check your API key, model, billing, and connection.\n')
