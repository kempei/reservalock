from reserva_request.util import hybrid_dict_cache
import pytest
import time

access_count: int = 0


@hybrid_dict_cache(__local_only=True, default_local_ttl=10)
def get_local_data(key: str, local_ttl: int = 10):
    global access_count
    access_count += 1
    return key


@hybrid_dict_cache(default_local_ttl=1, default_s3_ttl=10)
def get_data(key: str):
    global access_count
    access_count += 1
    return key


def test_local_cache():
    global access_count
    # cache check
    access_count = 0
    get_local_data(key="test1")
    get_local_data(key="test1")
    assert access_count == 1

    # default local ttl check
    access_count = 0
    get_local_data(key="test2")
    time.sleep(10)
    get_local_data(key="test2")
    assert access_count == 2

    # ondemand local ttl check
    access_count = 0
    get_local_data(key="test3", local_ttl=1)
    time.sleep(5)
    get_local_data(key="test3", local_ttl=1)
    assert access_count == 2


def test_s3_cache():
    global access_count
    # s3 cache hit
    access_count = 0
    get_data("test_s3_1")
    time.sleep(3)
    get_data("test_s3_1")
    assert access_count == 1
    time.sleep(15)

    # s3 cache miss
    access_count = 0
    get_data("test_s3_2")
    time.sleep(15)
    get_data("test_s3_2")
    assert access_count == 2
