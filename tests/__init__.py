async def test_resume_session_no_active_session(mocker):
    mocker.patch('your_module_name.client.aio.live.connect', return_value=AsyncMock())
    instance = YourClass()
    instance.session = None
    await instance.resume_session()
    assert instance.session is None

async def test_resume_session_with_active_session(mocker):
    mock_session = AsyncMock()
    mocker.patch('your_module_name.client.aio.live.connect', return_value=mock_session)
    instance = YourClass()
    instance.session = mock_session
    instance.tasks = [AsyncMock(), AsyncMock()]
    await instance.resume_session()
    assert instance.session is not None

async def test_resume_session_cancel_tasks(mocker):
    mock_session = AsyncMock()
    mocker.patch('your_module_name.client.aio.live.connect', return_value=mock_session)
    instance = YourClass()
    instance.session = mock_session
    instance.tasks = [AsyncMock(), AsyncMock()]
    await instance.resume_session()
    for task in instance.tasks:
        task.cancel.assert_called_once()

async def test_resume_session_exception_handling(mocker):
    mocker.patch('your_module_name.client.aio.live.connect', side_effect=Exception("Test Exception"))
    instance = YourClass()
    instance.session = None
    await instance.resume_session()
    assert instance.active is False