"""The one test that spends money.

Every other test stubs the model, which is what makes the suite free and
offline — and also what makes it blind. Nothing in it can catch a retired
model id, a rejected key, or a request shape the API stopped accepting.

So: one ping, the smallest turn that still crosses every boundary — key, model
id, endpoint, loop, trace write. Deselected by default, run on merge to main.
"""

import os

import pytest
from fastapi.testclient import TestClient

from ninja import agent, server

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key")
def test_a_real_turn_completes():
    reply = TestClient(server.app).post("/api/chat", json={"text": "Reply with: OK"})

    assert reply.status_code == 200, reply.text
    body = reply.json()
    # Not asserting on the wording — that is the model's business, and pinning
    # it would make this flap. Only that a turn came back whole.
    assert body["reply"].strip()
    assert isinstance(body["trace_id"], int)


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no API key")
def test_the_configured_model_id_still_exists():
    # Cheaper than a turn and the likelier breakage: ids get retired, and the
    # failure mode is every chat 404ing with nothing in the suite to catch it.
    import anthropic

    assert anthropic.Anthropic().models.retrieve(agent.MODEL).id
