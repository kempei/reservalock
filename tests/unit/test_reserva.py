
from reserva_request import app, remotelock
import pytest

def read_rsv_info_from_reservation_html_file(filename:str) -> dict[str, str]:
    with open(filename, 'r', encoding='UTF-8') as f:
        html = f.read()
        rsv_info:dict[str, str] = app.get_reservation_info_from_reserva_html(html)
        print(rsv_info)
        return rsv_info

def get_transformed_rsv_time_from_rsv_info(rsv_info:dict[str, str]):
    r:remotelock.RemoteLock = remotelock.RemoteLock({}, rsv_info)
    transformed_rsv_time = r.transform_rsv_time()
    print(transformed_rsv_time)
    return transformed_rsv_time

def test_reserva_html_info():
    assert read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_single.html") == {'hidden_rsv_no': '24470971', 'rsv_time': '2022/08/07 17:00〜21:00', 'name': 'しんぐる\xa0花子', 'name_kana': 'シングル\xa0ハナコ', 'email': 'hanako@example.com', 'phone': '0458293049', 'visible_rsv_no': 'f8pQ4XuLB', 'rsv_status': '予約確定'}
    assert read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_double.html") == {'hidden_rsv_no': '24562984', 'rsv_time': '2022/08/12 09:00〜17:00', 'name': 'ダブル\xa0太郎', 'name_kana': 'ダブル\xa0タロウ', 'email': 'double@example.com', 'phone': '0312345678', 'visible_rsv_no': 'QdXZMiHil', 'rsv_status': '予約確定'}
    assert read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_triple.html") == {'hidden_rsv_no': '24562984', 'rsv_time': '2022/08/12 09:00〜21:00', 'name': 'ダブル\xa0太郎', 'name_kana': 'ダブル\xa0タロウ', 'email': 'double@example.com', 'phone': '0312345678', 'visible_rsv_no': 'QdXZMiHil', 'rsv_status': '予約確定'}
    with pytest.raises(app.DiscontinuousReservationError) as e:
        read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_double_invalid.html")
    assert str(e.value) == "連続していない複数の予約はサポートされていません。予約されている時間帯: [2022/08/12 09:00～13:00] [2022/08/12 17:00～21:00]"

def test_remotelock_rsv_time():
    assert get_transformed_rsv_time_from_rsv_info(read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_single.html")) == ('2022-08-07T16:30:00', '2022-08-07T21:00:00')
    assert get_transformed_rsv_time_from_rsv_info(read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_double.html")) == ('2022-08-12T08:30:00', '2022-08-12T17:00:00')
    assert get_transformed_rsv_time_from_rsv_info(read_rsv_info_from_reservation_html_file("./tests/unit/html/reserva_20220812_triple.html")) == ('2022-08-12T08:30:00', '2022-08-12T21:00:00')
