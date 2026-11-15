from mcp_server.ratelimit import TokenBucket


def test_bucket_allows_n_per_minute_then_reports_retry_after():
    now = [0.0]
    bucket = TokenBucket(3, clock=lambda: now[0])
    assert [bucket.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    retry = bucket.acquire()
    assert retry >= 1 and retry == int(retry)
    assert retry == 20  # 60s / 3 per minute = one token per 20s
    now[0] += 20.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() >= 1
