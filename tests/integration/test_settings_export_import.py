import pytest
from starlette.testclient import TestClient
from backend.models.repository import Repository
from backend.api.main import create_app

@pytest.fixture
def client(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path}/test_export_import.db")
    repo.initialize()
    app = create_app(repo)
    return TestClient(app)

def test_settings_export_and_import(client):
    # 1. Update some settings
    client.put('/api/settings', json={
        'universe_min_turnover24h_usdt': 20000000.0,
        'broke_pb_position_usdt': 800.0,
        'broke_pb_telegram_topic_id': '777'
    })

    # 2. Export settings
    export_res = client.get('/api/settings/export')
    assert export_res.status_code == 200
    data = export_res.json()
    assert data['format'] == 'brokeweb-settings'
    assert data['version'] == 1
    assert data['settings']['universe_min_turnover24h_usdt'] == 20000000.0
    assert data['settings']['broke_pb_position_usdt'] == 800.0
    assert data['settings']['broke_pb_telegram_topic_id'] == '777'
    assert 'parameters' in data

    # 3. Modify settings to something else
    client.put('/api/settings', json={
        'universe_min_turnover24h_usdt': 5000000.0,
        'broke_pb_position_usdt': 250.0,
        'broke_pb_telegram_topic_id': '111'
    })
    curr = client.get('/api/settings').json()
    assert curr['broke_pb_position_usdt'] == 250.0

    # 4. Import the exported payload back
    import_res = client.post('/api/settings/import', json=data)
    assert import_res.status_code == 200
    assert import_res.json()['status'] == 'imported'

    # 5. Verify restored settings
    restored = client.get('/api/settings').json()
    assert restored['universe_min_turnover24h_usdt'] == 20000000.0
    assert restored['broke_pb_position_usdt'] == 800.0
    assert restored['broke_pb_telegram_topic_id'] == '777'

def test_test_telegram_targets(client):
    # Configure settings
    client.put('/api/settings', json={
        'telegram_bot_token': 'bot:we',
        'telegram_chat_id': '-100111',
        'telegram_topic_id': '1',
        'broke_pb_telegram_bot_token': 'bot:pb',
        'broke_pb_telegram_chat_id': '-100222',
        'broke_pb_telegram_topic_id': '2',
        'broke_pb_pm_telegram_bot_token': 'bot:pm',
        'broke_pb_pm_telegram_chat_id': '-100333',
        'broke_pb_pm_telegram_topic_id': '3',
    })

    # Test broke_we
    res1 = client.post('/api/alerts/test-telegram', json={'target': 'broke_we'})
    assert res1.status_code == 200
    assert res1.json()['target'] == 'broke_we'
    assert res1.json()['chat'] == '-100111'

    # Test broke_pb
    res2 = client.post('/api/alerts/test-telegram', json={'target': 'broke_pb'})
    assert res2.status_code == 200
    assert res2.json()['target'] == 'broke_pb'
    assert res2.json()['chat'] == '-100222'

    # Test broke_pb_pm
    res3 = client.post('/api/alerts/test-telegram', json={'target': 'broke_pb_pm'})
    assert res3.status_code == 200
    assert res3.json()['target'] == 'broke_pb_pm'
    assert res3.json()['chat'] == '-100333'
