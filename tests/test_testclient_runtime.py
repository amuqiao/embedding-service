def test_starlette_testclient_uses_httpx2_backend():
    import httpx2
    import starlette.testclient as testclient

    assert testclient.httpx is httpx2
