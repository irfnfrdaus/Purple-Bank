"""Public prefix must keep browser links, API and cookies within its path."""

from tests.conftest import login


def test_prefix_pages_api_and_session_boundaries(bank):
    client, _ = bank
    base = '/purplebank'
    redirect = client.get(base, follow_redirects=False)
    assert redirect.status_code == 308 and redirect.headers['location'] == base + '/'
    for path in ('/', '/pin-demo'):
        response = client.get(base + path)
        assert response.status_code == 200
        assert 'href="/assets/' not in response.text
        assert 'src="/assets/' not in response.text
        assert base + '/assets/' in response.text
    assert client.get(base + '/assets/app.js').status_code == 200
    assert client.get(base + '/api/health').json()['database'] == 'ok'
    assert client.get(base + '/api/health').headers['cache-control'] == 'no-store'
    assert client.get(base + '/api/overview').status_code == 401
    body = {'email': 'irfan@purple.demo', 'password': 'PurpleDemo!2026', 'device_id': 'purple-demo-browser'}
    result = client.post(base + '/api/auth/login', json=body)
    assert result.status_code == 200
    assert 'Path=/purplebank' in result.headers['set-cookie']
    assert client.get(base + '/api/overview').status_code == 200
    assert client.get(base + '/api/admin/overview').status_code == 403
    assert client.get('/api/overview').status_code == 401
    assert client.post(base + '/api/auth/logout').status_code == 200
    assert client.get(base + '/api/overview').status_code == 401
    assert client.post(base + '/api/auth/login', json=body, headers={'Origin':'https://elsewhere.invalid'}).status_code == 403
    # Existing private root still works, and arbitrary prefix headers are ignored.
    assert 'href="/assets/' in client.get('/', headers={'X-Forwarded-Prefix':'/evil'}).text
    assert login(client).status_code == 200
