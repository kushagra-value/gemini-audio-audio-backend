import pytest
import asyncio
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_resume_session_no_active_session():
    obj = YourClass()  # Replace with your actual class name
    obj.session = None
    await obj.resume_session()
    assert obj.session is None

@pytest.mark.asyncio
async def test_resume_session_with_active_session():
    obj = YourClass()  # Replace with your actual class name
    obj.session = AsyncMock()
    obj.tasks = [AsyncMock(), AsyncMock()]
    
    with patch('client.aio.live.connect', new_callable=AsyncMock) as mock_connect:
        await obj.resume_session()
        assert obj.session is not None
        mock_connect.assert_called_once()

@pytest.mark.asyncio
async def test_resume_session_cancel_tasks():
    obj = YourClass()  # Replace with your actual class name
    obj.session = AsyncMock()
    obj.tasks = [AsyncMock(), AsyncMock()]
    
    for task in obj.tasks:
        task.cancel = AsyncMock()
    
    await obj.resume_session()
    for task in obj.tasks:
        task.cancel.assert_called_once()

@pytest.mark.asyncio
async def test_resume_session_exception_handling():
    obj = YourClass()  # Replace with your actual class name
    obj.session = AsyncMock()
    obj.tasks = [AsyncMock(), AsyncMock()]
    
    with patch('client.aio.live.connect', side_effect=Exception("Connection error")):
        await obj.resume_session()
        assert obj.active is False

requirements-dev.txt:
pytest
pytest-asyncio