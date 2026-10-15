from types import SimpleNamespace

import pytest

from app.core import ai_identity as identity
from app.core.config import Settings
from app.repositories import chat, tickets


@pytest.mark.parametrize('configured,expected', [(None, 'Sophia'), ('', 'Sophia'), ('  ', 'Sophia'), ('  Ava  ', 'Ava')])
def test_agent_name_fallback_and_override(monkeypatch, configured, expected):
    monkeypatch.setattr(identity, 'get_settings', lambda: SimpleNamespace(ai_agent_name=configured))
    assert identity.get_agent_name() == expected


def test_agent_name_loaded_from_environment(monkeypatch):
    monkeypatch.setenv('AI_AGENT_NAME', 'Ava')
    assert Settings(_env_file=None).ai_agent_name == 'Ava'


def test_historical_ticket_note_uses_current_name_and_escapes_html(monkeypatch):
    monkeypatch.setattr(identity, 'get_settings', lambda: SimpleNamespace(ai_agent_name='Ava & <Team>'))
    row = {'author_id': None, 'author_email': identity.TROUBLESHOOT_AGENT_EMAIL,
           'author_display_name': 'Troubleshooting Agent',
           'body': '<p><strong>AI Troubleshooting Agent</strong> - the troubleshooter is finished.</p>'}
    result = tickets._normalise_reply(row)
    assert result['author_display_name'] == 'Ava & <Team>'
    assert result['body'] == '<p><strong>Ava &amp; &lt;Team&gt;</strong> - Ava &amp; &lt;Team&gt; is finished.</p>'
    assert row['author_display_name'] == 'Troubleshooting Agent'
    row['author_id'] = 123
    assert tickets._normalise_reply(row)['body'] == row['body']


@pytest.mark.asyncio
async def test_historical_chat_reads_use_bot_identity_only(monkeypatch):
    monkeypatch.setattr(identity, 'get_settings', lambda: SimpleNamespace(ai_agent_name='Ava', matrix_bot_user_id='@bot:example.com'))
    rows = [
        {'sender_matrix_id': '@bot:example.com', 'sender_user_id': None, 'sender_display_name': 'AI Waiting Assistant', 'body': 'AI Waiting Assistant: hello'},
        {'sender_matrix_id': '@human:example.com', 'sender_user_id': 1, 'sender_display_name': 'AI Waiting Assistant', 'body': 'AI Waiting Assistant: hello'},
    ]
    async def fetch_all(*args):
        return rows
    monkeypatch.setattr(chat.db, 'fetch_all', fetch_all)
    messages = await chat.get_messages(1)
    assert messages[0]['sender_display_name'] == 'Ava'
    assert messages[0]['body'] == 'Ava: hello'
    assert messages[1] == rows[1]


@pytest.mark.asyncio
async def test_agent_prompt_and_prior_turns_share_configured_name(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import agent

    monkeypatch.setattr(identity, 'get_settings', lambda: SimpleNamespace(ai_agent_name='Ava'))
    invoke = AsyncMock(return_value={'response': {'response': 'Hello'}})
    monkeypatch.setattr(agent.modules_service, 'trigger_module', invoke)
    await agent._invoke_agent_llm('answer', 'Answer this question')
    payload = invoke.call_args.args[1]
    assert 'Your name is "Ava"' in payload['prompt']
    assert 'Your name is "Ava"' in payload['messages'][0]['content']
    context = agent._build_conversation_context([
        {'role': 'user', 'content': 'Ask the MyPortal Agent'},
        {'role': 'agent', 'content': 'I am the MyPortal Agent.'},
    ])
    assert context == 'User: Ask the MyPortal Agent Ava: I am Ava.'


@pytest.mark.asyncio
async def test_stored_agent_turns_use_configured_name(monkeypatch):
    from unittest.mock import AsyncMock
    from app.repositories import agent_conversations

    monkeypatch.setattr(identity, 'get_settings', lambda: SimpleNamespace(ai_agent_name='Ava'))
    monkeypatch.setattr(agent_conversations.db, 'fetch_one', AsyncMock(return_value={'id': 'c', 'user_id': 1, 'company_id': 2}))
    monkeypatch.setattr(agent_conversations.db, 'fetch_all', AsyncMock(return_value=[
        {'turn_number': 1, 'role': 'user', 'content': 'MyPortal Agent'},
        {'turn_number': 2, 'role': 'agent', 'content': 'MyPortal Agent: hello'},
    ]))
    result = await agent_conversations.get('c', user_id=1, company_id=2)
    assert result['turns'][0]['content'] == 'MyPortal Agent'
    assert result['turns'][1]['content'] == 'Ava: hello'
