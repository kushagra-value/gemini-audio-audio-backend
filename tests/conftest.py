import pytest
import asyncio
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_session():
    return AsyncMock()

@pytest.fixture
def mock_tasks():
    return [AsyncMock() for _ in range(3)]

@pytest.fixture
def mock_client():
    class MockClient:
        class aio:
            class live:
                @staticmethod
                async def connect(model, config):
                    return AsyncMock()
    return MockClient()

@pytest.mark.asyncio
async def test_resume_session_no_active_session(mock_client):
    obj = YourClass()  # Replace with your actual class
    obj.session = None
    await obj.resume_session()
    assert obj.session is None

@pytest.mark.asyncio
async def test_resume_session_cancel_tasks(mock_client, mock_tasks):
    obj = YourClass()  # Replace with your actual class
    obj.session = mock_session()
    obj.tasks = mock_tasks
    await obj.resume_session()
    for task in mock_tasks:
        task.cancel.assert_called_once()

@pytest.mark.asyncio
async def test_resume_session_exception(mock_client):
    obj = YourClass()  # Replace with your actual class
    obj.session = mock_session()
    obj.tasks = []
    with patch('your_module.asyncio.gather', side_effect=Exception("Test Exception")):
        await obj.resume_session()
    assert not obj.active  # Assuming active is set to False on exception